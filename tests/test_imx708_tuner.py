import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
import sys
from unittest import mock


PATH = Path(__file__).parents[1] / 'tools/imx708/camera-tuner.py'
sys.path.insert(0,str(PATH.parent))
spec = importlib.util.spec_from_file_location('imx708_tuner', PATH)
tuner = importlib.util.module_from_spec(spec) if PATH.exists() else None
if tuner is not None:
    spec.loader.exec_module(tuner)


class Timers:
    def __init__(self):
        self.now, self.sequence, self.tasks = 0, 0, {}

    def add(self, delay, callback):
        self.sequence += 1
        self.tasks[self.sequence] = (self.now + delay, callback)
        return self.sequence

    def remove(self, identifier):
        self.tasks.pop(identifier, None)

    def advance(self, milliseconds):
        self.now += milliseconds
        for identifier, (deadline, callback) in list(self.tasks.items()):
            if deadline <= self.now:
                del self.tasks[identifier]
                callback()


class TunerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(tuner, 'camera-tuner module must exist without importing GTK')

    def test_click_maps_letterbox_and_clamps_roi(self):
        for x, y, width, height, expected in (
                (500, 500, 1000, 1000, (.35, .35, .3, .3)),
                (0, 500, 1000, 1000, (0., .35, .3, .3)),
                (1000, 500, 1000, 1000, (.7, .35, .3, .3)),
                (500, 218.75, 1000, 1000, (.35, 0., .3, .3)),
                (600, 200, 1200, 400, (.35, .35, .3, .3))):
            with self.subTest(x=x, y=y, width=width):
                actual = tuner.click_roi(x, y, width, height)
                for a, b in zip(actual, expected):
                    self.assertAlmostEqual(a, b)
        self.assertIsNone(tuner.click_roi(500, 100, 1000, 1000))
        self.assertIsNone(tuner.click_roi(100, 200, 1200, 400))
        self.assertIsNone(tuner.click_roi(0, 0, 0, 0))

    def test_debounce_merges_latest_changes_and_flushes_once(self):
        timers, changes = Timers(), []
        debounce = tuner.Debouncer(timers.add, timers.remove, changes.append)
        debounce.update('focus', 600)
        timers.advance(100)
        debounce.update('focus', 608)
        debounce.update('gain', 2)
        timers.advance(149)
        self.assertEqual(changes, [])
        timers.advance(1)
        self.assertEqual(changes, [{'focus': 608, 'gain': 2}])
        debounce.update('gamma', 1.5)
        debounce.flush()
        timers.advance(200)
        self.assertEqual(changes[-1], {'gamma': 1.5})
        self.assertEqual(len(changes), 2)
        debounce.update('gamma', 2)
        debounce.discard()
        timers.advance(200)
        self.assertEqual(len(changes), 2)

    def exchange(self, reply):
        with tempfile.TemporaryDirectory(prefix='imx708-tuner-test-') as directory:
            path = str(Path(directory) / 'control.sock')
            received, release = [], threading.Event()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(path)
                server.listen(1)
                def serve():
                    with server.accept()[0] as connection:
                        data = b''
                        while not data.endswith(b'\n'):
                            data += connection.recv(1024)
                        received.append(data)
                        encoded = reply.encode()
                        connection.sendall(encoded[:5])
                        connection.sendall(encoded[5:])
                        release.wait(1)
                thread = threading.Thread(target=serve)
                thread.start()
                try:
                    result = tuner.request(path, 'settings get', timeout=.3)
                finally:
                    release.set()
                    thread.join(2)
                self.assertEqual(received, [b'settings get\n'])
                return result

    def test_socket_reads_fragmented_newline_json_without_waiting_for_eof(self):
        self.assertEqual(self.exchange('{"focus":600,"preset_path":"/tmp/设置.json"}\n'),
                         {'focus': 600, 'preset_path': '/tmp/设置.json'})

    def test_socket_accepts_legacy_focus_and_reports_backend_errors(self):
        self.assertEqual(self.exchange('focus=608\n'), {'focus': 608})
        with self.assertRaisesRegex(RuntimeError, 'invalid gain'):
            self.exchange('ERROR: invalid gain\n')
        with self.assertRaises(ValueError):
            self.exchange('[]\n')

    def test_socket_rejects_command_injection_before_connecting(self):
        with self.assertRaises(ValueError):
            tuner.request('/does/not/exist', 'settings get\nstop')

    def test_async_client_serializes_batches_and_dispatches_callbacks(self):
        entered, release, dispatched = threading.Event(), threading.Event(), threading.Event()
        calls, deliveries = [], []
        owner = threading.get_ident()
        def transport(path, command):
            self.assertNotEqual(threading.get_ident(), owner)
            calls.append(command)
            if command == 'roi 0,0,.3,.3':
                entered.set()
                self.assertTrue(release.wait(2))
            return {'command': command}
        def dispatch(callback, *args):
            deliveries.append((callback, args))
            if len(deliveries) == 2:
                dispatched.set()
        replies = []
        client = tuner.AsyncClient('/unused', dispatch, transport)
        try:
            first = client.submit_many(['roi 0,0,.3,.3', 'af once'],
                                       lambda reply, error: replies.append((reply, error)))
            self.assertTrue(entered.wait(1))
            second = client.submit('settings get', lambda reply, error: replies.append((reply, error)))
            self.assertEqual(calls, ['roi 0,0,.3,.3'])
            release.set()
            first.result(2)
            second.result(2)
            self.assertTrue(dispatched.wait(1))
            self.assertEqual(replies, [])
            for callback, args in deliveries:
                callback(*args)
            self.assertEqual(calls, ['roi 0,0,.3,.3', 'af once', 'settings get'])
            self.assertEqual(replies[-1], ({'command': 'settings get'}, None))
        finally:
            release.set()
            client.close(lambda *args: None).result(2)

    def test_roi_failure_does_not_start_af(self):
        calls = []
        def transport(path, command):
            calls.append(command)
            raise RuntimeError('bad ROI')
        client = tuner.AsyncClient('/unused', lambda *args: None, transport)
        try:
            future = client.submit_many(['roi invalid', 'af once'], lambda *args: None)
            with self.assertRaisesRegex(RuntimeError, 'bad ROI'):
                future.result(1)
            self.assertEqual(calls, ['roi invalid'])
        finally:
            client.close(lambda *args: None).result(2)

    def test_child_command_passes_embed_focus_and_manual_defaults(self):
        command = tuner.child_command(12345, '/tmp/output', '/tmp/camera.sock', 600)
        self.assertEqual(Path(command[2]), PATH.with_name('preview-x11.py'))
        for flag, value in (('--window-id', '12345'), ('--autofocus', 'off'),
                            ('--focus', '600'), ('--exposure-ns', '10000000'),
                            ('--gain', '2'), ('--isp-digital-gain', '1')):
            self.assertEqual(command[command.index(flag) + 1], value)

    def test_existing_server_is_not_launched_or_stopped(self):
        backend = tuner.PreviewProcess('/tmp/output', '/tmp/existing-camera.sock', 600)
        with mock.patch.object(tuner.os.path, 'lexists', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'existing-camera.sock'):
                backend.start(123)
        self.assertIsNone(backend.process)
        backend.stop()

    def test_stop_before_realize_prevents_late_child_launch(self):
        backend = tuner.PreviewProcess('/tmp/output', '/tmp/unused.sock', 688)
        backend.stop()
        with self.assertRaisesRegex(RuntimeError, 'closing'):
            backend.start(123)
        self.assertIsNone(backend.process)

    def test_shutdown_stops_only_owned_ready_child_and_bounds_waits(self):
        events = []
        class Child:
            def poll(self):
                return None
            def wait(self, timeout=None):
                events.append(('wait', timeout))
                if len([item for item in events if item[0] == 'wait']) < 3:
                    raise subprocess.TimeoutExpired('preview', timeout)
                return -9
            def terminate(self):
                events.append(('terminate',))
            def kill(self):
                events.append(('kill',))
        with mock.patch.object(tuner, 'request', side_effect=lambda *a, **k: events.append(('stop',))):
            tuner.stop_child(Child(), '/tmp/owned.sock', ready=True)
        self.assertEqual([e[0] for e in events], ['stop', 'wait', 'terminate', 'wait', 'kill', 'wait'])
        self.assertTrue(all(e[1] > 0 for e in events if e[0] == 'wait'))
        events.clear()
        with mock.patch.object(tuner, 'request', side_effect=AssertionError('must not contact other server')):
            tuner.stop_child(Child(), '/tmp/not-owned.sock', ready=False)

    def test_stdout_recognizes_ready_and_af_events_only(self):
        self.assertEqual(tuner.parse_line('READY: preview\n'), ('ready', None))
        event = {'event': 'locked', 'position': 600, 'state': 'LOCKED'}
        self.assertEqual(tuner.parse_line(json.dumps(event)), ('event', event))
        self.assertIsNone(tuner.parse_line('Argus diagnostic line'))

    def test_actions_flush_visible_focus_and_settings_before_the_action(self):
        for action in ('settings save', 'settings load', 'settings reset', 'snapshot',
                       'af once', 'af continuous', 'af stop'):
            with self.subTest(action=action):
                calls = []
                app = tuner.TunerWindow.__new__(tuner.TunerWindow)
                app.ready, app.closing, app.revisions = True, False, {}
                app.client = tuner.AsyncClient('/unused', lambda *args: None,
                    lambda path, command: calls.append(command) or {})
                timers = Timers()
                app.debounce = tuner.Debouncer(timers.add, timers.remove, app._send_changes)
                try:
                    app.debounce.update('gain', 2)
                    app.debounce.update('focus', 600)
                    app._action(action)
                    app.client.submit('settings get', lambda *args: None).result(1)
                    self.assertEqual(calls, ['settings set {"gain":2}', 'focus 600', action, 'settings get'])
                    timers.advance(200)
                    self.assertEqual(len(calls), 4)
                finally:
                    app.client.close(lambda *args: None).result(2)

    def test_async_close_cancels_backlog_before_owned_child_cleanup(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def transport(path, command):
            calls.append(command)
            entered.set()
            release.wait(2)
            return {}
        client = tuner.AsyncClient('/unused', lambda *args: None, transport)
        first = client.submit('settings get', lambda *args: None)
        self.assertTrue(entered.wait(1))
        pending = client.submit('af once', lambda *args: None)
        finished = client.close(lambda *args: None, lambda: calls.append('stop-owned-child'))
        try:
            self.assertTrue(pending.cancelled())
            with self.assertRaises(RuntimeError):
                client.submit('snapshot', lambda *args: None)
        finally:
            release.set()
            finished.result(2)
        self.assertEqual(calls, ['settings get', 'stop-owned-child'])

    def test_hardware_readback_is_optional_and_separate_from_requested_values(self):
        self.assertIn('等待', tuner.hardware_text(None))
        text = tuner.hardware_text({'exposure_ms': 5.01, 'gain': 2.02, 'frame_ms': 66.7})
        self.assertIn('5.01 ms', text)
        self.assertIn('2.02', text)
        self.assertIn('66.7 ms', text)

    def test_readback_refresh_is_delayed_and_runs_only_once(self):
        app=tuner.TunerWindow.__new__(tuner.TunerWindow)
        timers=Timers();calls=[]
        app.ready=True;app.closing=False;app.readback_timer=None
        app.GLib=type('GLib',(),dict(timeout_add=staticmethod(timers.add),source_remove=staticmethod(timers.remove)))
        app._submit=calls.append
        self.assertTrue(hasattr(app,'_queue_hardware_refresh'),'settled readback refresh missing')
        app._queue_hardware_refresh();timers.advance(499)
        self.assertEqual(calls,[])
        timers.advance(1);self.assertEqual(calls,['settings get'])
        timers.advance(1000);self.assertEqual(calls,['settings get'])

    def test_restore_preset_uses_regular_validated_backend_commands(self):
        self.assertTrue(hasattr(tuner,'preset_commands'),'startup preset restore missing')
        with tempfile.TemporaryDirectory() as path:
            preset=Path(path)/'restore.json'
            preset.write_text(json.dumps(dict(version=1,settings={'gain':3,'red':.45},focus=612,roi=[.1,.2,.3,.3])))
            commands=tuner.preset_commands(preset)
        self.assertEqual(json.loads(commands[0][len('settings set '):]),{'gain':3,'red':.45,'focus':612})
        self.assertTrue(commands[1].startswith('roi '))
        self.assertEqual(commands[-1],'settings get')


if __name__ == '__main__':
    unittest.main()
