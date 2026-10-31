#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""GTK3/X11 manual camera tuner. Run with python3 under an existing ssh -Y.

The GUI preserves DISPLAY; preview-x11.py owns Argus, EGL and the lens.
GTK imports are deliberately lazy so geometry and protocol tests run headless.
"""
import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading


DEFAULT_OUTPUT = '/home/jetson/imx708-deploy/manual-tuning'
DEFAULT_SOCKET = '/tmp/imx708-preview.sock'
# label, minimum, maximum, increment, decimal places, initial value
CONTROLS = {
    'exposure_ms': ('曝光 (ms)', .5, 66.6, .1, 1, 10),
    'gain': ('模拟增益', 1.125, 16, .125, 3, 2),
    'isp_gain': ('ISP 增益', 1, 8, .1, 2, 1),
    'exposure_compensation': ('曝光补偿 EV', -2, 2, .1, 1, 0),
    'red': ('红', 0, 1, .01, 2, .5),
    'green': ('绿', 0, 1, .01, 2, .5),
    'blue': ('蓝', 0, 1, .01, 2, .5),
    'saturation': ('饱和度', 0, 2, .01, 2, 1),
    'brightness': ('亮度', -1, 1, .01, 2, 0),
    'contrast': ('对比度', 0, 2, .01, 2, 1),
    'gamma': ('伽马', .1, 3, .01, 2, 1),
    'focus': ('焦距 DAC', 0, 1023, 1, 0, 688),
}


def click_roi(x, y, width, height):
    """Map a click through a centered 16:9 letterbox; ignore the black bars."""
    if not all(math.isfinite(v) for v in (x, y, width, height)) or min(width, height) <= 0:
        return None
    video_width = min(width, height * 16 / 9)
    video_height = video_width * 9 / 16
    left, top = (width - video_width) / 2, (height - video_height) / 2
    if not left <= x <= left + video_width or not top <= y <= top + video_height:
        return None
    cx, cy = (x - left) / video_width, (y - top) / video_height
    return (min(.7, max(0., cx - .15)), min(.7, max(0., cy - .15)), .3, .3)


class Debouncer:
    def __init__(self, schedule, cancel, callback, delay=150):
        self.schedule, self.cancel, self.callback = schedule, cancel, callback
        self.delay, self.timer, self.pending = delay, None, {}

    def update(self, key, value):
        self.pending[key] = value
        if self.timer is not None:
            self.cancel(self.timer)
        self.timer = self.schedule(self.delay, self._fire)

    def _fire(self):
        self.timer = None
        pending, self.pending = self.pending, {}
        if pending:
            self.callback(pending)
        return False

    def flush(self):
        if self.timer is not None:
            self.cancel(self.timer)
            self.timer = None
        self._fire()

    def discard(self):
        if self.timer is not None:
            self.cancel(self.timer)
        self.timer, self.pending = None, {}


def request(path, command, timeout=5):
    """One newline-delimited request/response per Unix connection."""
    if not command or any(c in command for c in '\r\n\0'):
        raise ValueError('command must contain exactly one line')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        connection.sendall((command + '\n').encode('utf-8'))
        data = bytearray()
        while b'\n' not in data:
            chunk = connection.recv(4096)
            if not chunk:
                raise RuntimeError('backend closed before a complete response')
            data.extend(chunk)
            if len(data) > 1024 * 1024:
                raise RuntimeError('backend response is too large')
    line = bytes(data).split(b'\n', 1)[0].decode('utf-8').strip()
    if line.startswith('ERROR:'):
        raise RuntimeError(line[6:].strip())
    if line.startswith('focus='):
        return {'focus': int(line[6:])}
    result = json.loads(line)
    if not isinstance(result, dict):
        raise ValueError('backend response must be a JSON object')
    return result


class AsyncClient:
    """Serialize socket work, including ROI+AF batches, away from GTK."""
    def __init__(self, path, dispatch, transport=None):
        self.path, self.dispatch = path, dispatch
        self.transport = transport or request
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='tuner-socket')
        self.lock, self.pending, self.closing = threading.Lock(), set(), False

    def _deliver(self, future, callback):
        with self.lock:
            self.pending.discard(future)
        if future.cancelled():
            return
        try:
            result, error = future.result(), None
        except Exception as exception:
            result, error = None, exception
        self.dispatch(callback, result, error)

    def submit(self, command, callback):
        return self.submit_many([command], callback)

    def submit_many(self, commands, callback):
        def run():
            result = None
            for command in commands:
                result = self.transport(self.path, command)
            return result
        with self.lock:
            if self.closing:
                raise RuntimeError('client is closing')
            future = self.executor.submit(run)
            self.pending.add(future)
        future.add_done_callback(lambda completed: self._deliver(completed, callback))
        return future

    def close(self, callback, cleanup=None):
        with self.lock:
            self.closing = True
            pending = list(self.pending)
        for future in pending:
            future.cancel()
        future = self.executor.submit(cleanup or (lambda: None))
        future.add_done_callback(lambda completed: self._deliver(completed, callback))
        self.executor.shutdown(wait=False)
        return future


def child_command(window_id, output, socket_path, focus=688):
    return [sys.executable, '-u', str(Path(__file__).resolve().with_name('preview-x11.py')),
            '--window-id', str(window_id), '--autofocus', 'off', '--focus', str(focus),
            '--output', str(output), '--socket', str(socket_path),
            '--exposure-ns', '10000000', '--gain', '2', '--isp-digital-gain', '1']


def stop_child(process, path, ready=False):
    if process is None or process.poll() is not None:
        return
    if ready:
        try:
            request(path, 'stop', timeout=2)
        except (OSError, ValueError, RuntimeError):
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


class PreviewProcess:
    def __init__(self, output, socket_path, focus):
        self.output, self.socket_path, self.focus = output, socket_path, focus
        self.lock, self.process = threading.Lock(), None
        self.ready = self.closing = False

    def start(self, window_id):
        with self.lock:
            if self.closing:
                raise RuntimeError('preview is closing')
            if os.path.lexists(self.socket_path):
                raise RuntimeError('控制套接字已存在：%s；请先关闭原有预览，本程序不会停止它。' % self.socket_path)
            self.process = subprocess.Popen(
                child_command(window_id, self.output, self.socket_path, self.focus),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                encoding='utf-8', errors='replace', bufsize=1, env=dict(os.environ))
            return self.process

    def mark_ready(self):
        with self.lock:
            self.ready = True

    def stop(self):
        with self.lock:
            self.closing = True
            process, ready = self.process, self.ready
        stop_child(process, self.socket_path, ready)


def parse_line(line):
    line = line.strip()
    if line == 'READY' or line.startswith('READY:'):
        return 'ready', None
    try:
        record = json.loads(line)
    except ValueError:
        return None
    if isinstance(record, dict) and record.get('event') in ('search', 'locked', 'unconfirmed', 'error'):
        return 'event', record
    return None


def hardware_text(readings):
    if not readings:
        return '实际曝光 / 增益：等待硬件读回'
    return '实际：曝光 %s ms，增益 %s；帧长 %s ms' % (
        readings.get('exposure_ms', '—'), readings.get('gain', '—'), readings.get('frame_ms', '—'))


class TunerWindow:
    def __init__(self, args, Gtk, Gdk, GLib):
        self.args, self.Gtk, self.GLib = args, Gtk, GLib
        self.ready = self.closing = self.updating = False
        self.adjustments, self.widgets, self.values = {}, {}, {}
        self.revisions, self.dirty = {}, set()
        self.backend = PreviewProcess(args.output, args.socket, args.focus)
        self.client = AsyncClient(args.socket, GLib.idle_add)
        self.debounce = Debouncer(GLib.timeout_add, GLib.source_remove, self._send_changes)
        self.window = Gtk.Window(title='IMX708 手动调节 · 0.4')
        self.window.set_default_size(1280, 760)
        self.window.connect('delete-event', self._close)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_border_width(10)
        self.window.add(outer)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        outer.pack_start(row, True, True, 0)
        aspect = Gtk.AspectFrame.new(None, .5, .5, 16 / 9, False)
        aspect.set_shadow_type(Gtk.ShadowType.NONE)
        self.video = Gtk.DrawingArea()
        self.video.set_size_request(480, 270)
        self.video.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.video.connect('button-press-event', self._click)
        self.video.connect('realize', self._realize)
        aspect.add(self.video)
        row.pack_start(aspect, True, True, 0)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_size_request(380, -1)
        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.scroll.add(self.panel)
        row.pack_start(self.scroll, False, False, 0)
        self.scroll.set_sensitive(False)
        exposure = self._section('曝光')
        self._toggle(exposure, 'auto_exposure', '自动曝光')
        for key in ('exposure_ms', 'gain', 'isp_gain', 'exposure_compensation'):
            self._numeric(exposure, key)
        white = self._section('白平衡')
        combo = Gtk.ComboBoxText()
        for index, label in enumerate(('关闭', '自动', '白炽灯', '荧光灯', '暖荧光灯',
                                       '日光', '阴天', '暮光', '阴影')):
            combo.append(str(index), '%d · %s' % (index, label))
        combo.set_active_id('1')
        combo.connect('changed', lambda widget: self._edit('wbmode', int(widget.get_active_id())))
        self.widgets['wbmode'] = combo
        white.pack_start(combo, False, False, 0)
        self._toggle(white, 'awblock', '锁定自动白平衡')
        rgb = self._section('RGB 微调 / 中性 0.5')
        for key in ('red', 'green', 'blue'):
            self._numeric(rgb, key)
        image = self._section('画面')
        for key in ('saturation', 'brightness', 'contrast', 'gamma'):
            self._numeric(image, key)
        focus = self._section('对焦（默认手动）')
        self._numeric(focus, 'focus', args.focus)
        steps = Gtk.Box(spacing=4)
        for delta in (-8, -1, 1, 8):
            button = Gtk.Button(label='%+d' % delta)
            button.connect('clicked', lambda widget, step=delta:
                           self.adjustments['focus'].set_value(self.adjustments['focus'].get_value() + step))
            steps.pack_start(button, True, True, 0)
        focus.pack_start(steps, False, False, 0)
        buttons = Gtk.Box(spacing=4)
        for label, mode in (('单次对焦', 'once'), ('连续对焦', 'continuous'), ('停止对焦', 'stop')):
            button = Gtk.Button(label=label)
            button.connect('clicked', lambda widget, mode=mode: self._action('af ' + mode))
            buttons.pack_start(button, True, True, 0)
        focus.pack_start(buttons, False, False, 0)
        self.af_label = self._label('自动对焦：关闭 / 手动')
        focus.pack_start(self.af_label, False, False, 0)
        focus.pack_start(self._label('点击画面：以点击位置为中心，选择 30% 区域并单次对焦。'), False, False, 0)
        presets = self._section('设置与截图')
        buttons = Gtk.Box(spacing=4)
        for label, command in (('保存', 'settings save'), ('加载', 'settings load'),
                               ('重置', 'settings reset'), ('截图', 'snapshot')):
            button = Gtk.Button(label=label)
            button.connect('clicked', lambda widget, command=command: self._action(command))
            buttons.pack_start(button, True, True, 0)
        presets.pack_start(buttons, False, False, 0)
        self.path_label = self._label('输出目录：' + str(args.output))
        self.path_label.set_selectable(True)
        presets.pack_start(self.path_label, False, False, 0)
        self.hardware_label = self._label(hardware_text(None))
        outer.pack_start(self.hardware_label, False, False, 0)
        self.status = self._label('正在启动预览…')
        self.status.set_selectable(True)
        outer.pack_start(self.status, False, False, 0)

    def _label(self, text):
        label = self.Gtk.Label(label=text, xalign=0)
        label.set_line_wrap(True)
        label.set_max_width_chars(48)
        return label

    def _section(self, title):
        frame = self.Gtk.Frame(label=title)
        box = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=5)
        box.set_border_width(8)
        frame.add(box)
        self.panel.pack_start(frame, False, False, 0)
        return box

    def _toggle(self, box, key, label):
        widget = self.Gtk.CheckButton(label=label)
        widget.connect('toggled', lambda widget: self._edit(key, widget.get_active()))
        box.pack_start(widget, False, False, 0)
        self.widgets[key] = widget

    def _numeric(self, box, key, initial=None):
        Gtk = self.Gtk
        label, low, high, step, digits, default = CONTROLS[key]
        box.pack_start(self._label(label), False, False, 0)
        adjustment = Gtk.Adjustment(value=default if initial is None else initial,
                                    lower=low, upper=high, step_increment=step, page_increment=step * 8)
        row = Gtk.Box(spacing=6)
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adjustment)
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        spin = Gtk.SpinButton(adjustment=adjustment, climb_rate=step, digits=digits)
        row.pack_start(scale, True, True, 0)
        row.pack_start(spin, False, False, 0)
        box.pack_start(row, False, False, 0)
        self.adjustments[key], self.widgets[key] = adjustment, row
        adjustment.connect('value-changed', lambda adj: self._edit(
            key, int(round(adj.get_value())) if key == 'focus' else adj.get_value()))

    def _manual_sensitivity(self):
        manual = not self.widgets['auto_exposure'].get_active()
        for key in ('exposure_ms', 'gain'):
            self.widgets[key].set_sensitive(manual)

    def _edit(self, key, value):
        if self.updating or not self.ready or self.closing:
            return
        self.revisions[key] = self.revisions.get(key, 0) + 1
        self.dirty.add(key)
        self.debounce.update(key, value)
        self._manual_sensitivity()

    def _send_changes(self, changes):
        settings = {key: value for key, value in changes.items() if key != 'focus'}
        if settings:
            self._submit('settings set ' + json.dumps(settings, separators=(',', ':')), submitted=set(settings))
        if 'focus' in changes:
            self._submit('focus %d' % changes['focus'], submitted={'focus'})

    def _submit(self, command, submitted=None, initializing=False):
        revisions = dict(self.revisions)
        self.client.submit(command, lambda reply, error:
                           self._reply(reply, error, revisions, submitted or set(), command, initializing))

    def _reply(self, reply, error, revisions, submitted, command='', initializing=False):
        if self.closing:
            return False
        if error is not None:
            self.status.set_text('操作失败：' + str(error))
            return False
        if command.startswith('focus '):
            reply = dict(reply, af_mode='off', af_state='MANUAL')
        self._apply(reply, revisions, submitted)
        if initializing:
            self.scroll.set_sensitive(True)
            self.status.set_text('预览就绪；手动调节，自动对焦默认关闭。')
        if command in ('settings save', 'settings load'):
            self.path_label.set_text('设置文件：' + str(reply.get('preset_path', Path(self.args.output) / 'settings.json')))
        elif command == 'settings reset':
            self.status.set_text('已恢复默认参数。')
        elif command == 'snapshot':
            snapshot = reply.get('snapshot') or {}
            self.path_label.set_text('截图已保存：' + str(snapshot.get('png') or snapshot.get('i420') or snapshot))
        return False

    def _apply(self, reply, revisions=None, submitted=None):
        values = dict(reply)
        for old, new in (('position', 'focus'), ('mode', 'af_mode'), ('state', 'af_state')):
            if old in values and new not in values:
                values[new] = values[old]
        self.updating = True
        try:
            for key in set(self.adjustments) | set(self.widgets):
                if key not in values:
                    continue
                if key in self.dirty:
                    if key not in (submitted or ()) or (revisions or {}).get(key, 0) != self.revisions.get(key, 0):
                        continue
                    self.dirty.discard(key)
                elif revisions is not None and revisions.get(key, 0) != self.revisions.get(key, 0):
                    continue
                if key in self.adjustments:
                    self.adjustments[key].set_value(values[key])
                elif key == 'wbmode':
                    self.widgets[key].set_active_id(str(values[key]))
                else:
                    self.widgets[key].set_active(bool(values[key]))
            self.values.update(values)
            if 'hardware' in values:
                self.hardware_label.set_text(hardware_text(values['hardware']))
            self._manual_sensitivity()
            mode = self.values.get('af_mode', 'off')
            state = self.values.get('af_state', 'MANUAL')
            self.af_label.set_text('自动对焦：%s / %s；DAC %d' % (
                {'off': '关闭', 'once': '单次', 'continuous': '连续'}.get(mode, mode),
                state, round(self.adjustments['focus'].get_value())))
        finally:
            self.updating = False

    def _action(self, command):
        if self.ready and not self.closing:
            self.debounce.flush()
            self._submit(command)

    def _click(self, widget, event):
        if event.button != 1 or not self.ready or self.closing or not self.scroll.get_sensitive():
            return False
        allocation = widget.get_allocation()
        roi = click_roi(event.x, event.y, allocation.width, allocation.height)
        if roi is not None:
            self.debounce.flush()
            revisions = dict(self.revisions)
            self.client.submit_many(['roi ' + ','.join('%.6f' % v for v in roi), 'af once'],
                                    lambda reply, error: self._reply(reply, error, revisions, set()))
            self.status.set_text('已选择对焦区域，正在单次对焦…')
        return True

    def _realize(self, widget):
        try:
            xid = widget.get_window().get_xid()
        except (AttributeError, RuntimeError) as error:
            self.status.set_text('需要 X11 显示：' + str(error))
            return
        threading.Thread(target=self._read_backend, args=(xid,), name='tuner-preview', daemon=True).start()
        self.GLib.timeout_add_seconds(20, self._startup_timeout)

    def _read_backend(self, xid):
        tail = deque(maxlen=8)
        try:
            process = self.backend.start(xid)
            for line in process.stdout:
                tail.append(line.strip())
                parsed = parse_line(line)
                if parsed is not None:
                    kind, record = parsed
                    if kind == 'ready':
                        self.backend.mark_ready()
                    self.GLib.idle_add(self._backend_event, kind, record)
            returncode = process.wait()
            self.GLib.idle_add(self._backend_failed, '预览已退出 (%s)：%s' % (returncode, '\n'.join(tail)))
        except Exception as error:
            self.GLib.idle_add(self._backend_failed, str(error))

    def _backend_event(self, kind, record):
        if self.closing:
            return False
        if kind == 'ready':
            if not self.ready:
                self.ready = True
                self._submit('settings get', initializing=True)
        else:
            self._apply(record)
            if record.get('event') in ('error', 'unconfirmed'):
                self.status.set_text('对焦：' + str(record.get('reason') or record['event']))
        return False

    def _backend_failed(self, message):
        if not self.closing:
            self.ready = False
            self.debounce.discard()
            self.scroll.set_sensitive(False)
            self.status.set_text(message)
        return False

    def _startup_timeout(self):
        if not self.ready and not self.closing:
            self._backend_failed('预览启动超时，请检查后台输出。关闭窗口将停止本程序启动的预览。')
        return False

    def _close(self, *args):
        if not self.closing:
            self.closing = True
            self.debounce.discard()
            self.scroll.set_sensitive(False)
            self.status.set_text('正在关闭预览…')
            self.client.close(self._closed, self.backend.stop)
        return True

    def _closed(self, result, error):
        if error is not None:
            print('Preview shutdown: %s' % error, file=sys.stderr)
        self.window.destroy()
        self.Gtk.main_quit()
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(DEFAULT_OUTPUT))
    parser.add_argument('--socket', default=DEFAULT_SOCKET)
    parser.add_argument('--focus', type=int, default=688, help='initial manual focus DAC (0..1023)')
    args = parser.parse_args(argv)
    if not 0 <= args.focus <= 1023:
        parser.error('focus must be 0..1023')
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('GdkX11', '3.0')
    from gi.repository import Gdk, GdkX11, GLib, Gtk
    if not Gtk.init_check(None)[0]:
        parser.error('无法连接 X11 显示，请使用 ssh -Y 并保留 DISPLAY / XAUTHORITY')
    # Importing GdkX11 installs the native X11 get_xid() method on Gdk.Window.
    window = TunerWindow(args, Gtk, Gdk, GLib)
    window.window.show_all()
    Gtk.main()


if __name__ == '__main__':
    main()
