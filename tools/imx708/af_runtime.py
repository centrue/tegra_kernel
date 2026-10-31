# SPDX-License-Identifier: GPL-2.0-only
"""Single-owner lens worker and bounded frame handoff for live autofocus."""
from collections import deque
import fcntl
import os
from pathlib import Path
import struct
import threading
import time

from autofocus import Controller, measure_luma
from camera_io import read_register


class Mailbox:
    def __init__(self):
        self.condition = threading.Condition()
        self.sequence = 0
        self.frame = None

    def publish(self, frame):
        with self.condition:
            self.sequence += 1
            self.frame = dict(frame, sequence=self.sequence)
            self.condition.notify_all()

    def take(self, after, timeout):
        with self.condition:
            if self.sequence <= after:
                self.condition.wait(timeout)
            return self.frame if self.sequence > after else None

    def wake(self):
        with self.condition:
            self.condition.notify_all()


def luma_bytes(frame):
    data, width = frame['data'], frame['width']
    return b''.join(data[frame['offset']+y*frame['stride']:
                         frame['offset']+y*frame['stride']+width]
                    for y in range(frame['height']))


def i420_bytes(frame):
    data, width, height = frame['data'], frame['width'], frame['height']
    return b''.join(data[offset+y*stride:offset+y*stride+w]
                    for offset, stride, w, h in zip(frame['offsets'], frame['strides'],
                                                    (width, width//2, width//2),
                                                    (height, height//2, height//2))
                    for y in range(h))


class NanoLens:
    def __init__(self, bus='/dev/i2c-6'):
        self.fd = None
        self.bus = bus

    def move(self, position):
        if self.fd is None:
            node = next('/dev/'+p.parent.name
                        for p in Path('/sys/class/video4linux').glob('v4l-subdev*/name')
                        if 'dw9817' in p.read_text())
            self.fd = os.open(node, os.O_RDWR)
        fcntl.ioctl(self.fd, 0xc008561c, struct.pack('Ii', 0x009a090a, position))
        deadline = time.monotonic()+.3
        while True:
            actual = read_register(self.bus, 0x0c, 3, 1, 2) & 1023
            if actual == position:
                return actual
            if time.monotonic() >= deadline:
                raise OSError('DAC mismatch: requested%d actual%d' % (position, actual))
            time.sleep(.01)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class CameraLocks:
    """Restore independent camera properties even after a partial setter error."""
    def __init__(self, camera):
        self.camera = camera
        self.saved = {}

    def __call__(self, on):
        if on:
            if self.saved:
                raise RuntimeError('camera lock restoration still pending')
            self.saved = {key:self.camera.get_property(key) for key in ('aelock','awblock')}
            try:
                for key in self.saved:
                    self.camera.set_property(key,True)
            except Exception as error:
                try:
                    self(False)
                except Exception as restore_error:
                    raise RuntimeError('%s; rollback: %s'%(error,restore_error)) from error
                raise
        else:
            errors = []
            for key,value in list(self.saved.items()):
                try:
                    self.camera.set_property(key,value)
                    del self.saved[key]
                except Exception as error:
                    errors.append('%s: %s'%(key,error))
            if errors:
                raise RuntimeError('; '.join(errors))


class Worker:
    def __init__(self, mailbox, lens, set_locks, position=688, limits=(352, 928),
                 roi=(.25, .25, .5, .5), emit=None, save=None):
        self.mailbox, self.lens, self.set_locks = mailbox, lens, set_locks
        self.emit = emit or (lambda record: None)
        self.save = save or (lambda label, frame, status: None)
        self.controller = Controller(position, limits, self._event)
        self.roi = roi
        self.lock = threading.RLock()
        self.commands = deque()
        self.generation = 0
        self.stopping = False
        self.search_locked = False
        self.current_frame = None
        self.sequence = 0
        self.last_frame = time.monotonic()
        self.last_heartbeat = -float('inf')
        self.thread = threading.Thread(target=self._run, name='imx708-af', daemon=True)

    def _event(self, record):
        self.emit(dict(record, monotonic=time.monotonic(), sequence=self.sequence))
        if record['event'] in ('search', 'locked') and self.current_frame is not None:
            self.save(record['event'], self.current_frame, self.controller.status())

    def status(self):
        with self.lock:
            return self.controller.status()

    def start(self):
        self.thread.start()

    def command(self, kind, value=None):
        done, result = threading.Event(), {}
        with self.lock:
            if self.stopping:
                raise RuntimeError('AF worker is stopping')
            self.generation += 1
            self.commands.append((kind, value, done, result))
        self.mailbox.wake()
        if not done.wait(5):
            raise RuntimeError('AF command timed out')
        if 'error' in result:
            raise RuntimeError(result['error'])
        return result['status']

    def _sync_locks(self):
        desired = self.controller.searching
        if desired != self.search_locked:
            # Mark dirty before calling: a setter can partially succeed.
            self.search_locked = True
            self.set_locks(desired)
            self.search_locked = desired

    def _move(self, position):
        self._sync_locks()
        started = time.monotonic()
        actual = self.lens.move(position)
        if actual != position:
            raise OSError('DAC mismatch: requested%d actual%d' % (position, actual))
        finished = time.monotonic()
        self.controller.moved(position, finished, self.mailbox.sequence)
        self.emit(dict(event='move', requested=position, actual=actual,
                       monotonic=finished, duration=finished-started,
                       sequence=self.mailbox.sequence))

    def _error(self, error):
        self.controller.error(str(error))
        try:
            self.set_locks(False)
            self.search_locked = False
        except Exception as restore_error:
            self.search_locked = True
            self.emit(dict(event='restore_error',reason=str(restore_error)))

    def _run(self):
        try:
            with self.lock:
                try:
                    self._move(self.controller.position)
                except Exception as error:
                    self._error(error)
            while True:
                with self.lock:
                    if self.stopping:
                        break
                    while self.commands:
                        kind, value, done, result = self.commands.popleft()
                        try:
                            if kind == 'focus':
                                self._move(self.controller.manual(value))
                            elif kind == 'mode':
                                self.controller.set_mode(value, time.monotonic())
                                self._sync_locks()
                            elif kind == 'snapshot':
                                if self.current_frame is None:
                                    raise ValueError('no frame available')
                                self.save('snapshot', self.current_frame, self.controller.status())
                            else:
                                raise ValueError('unknown worker command')
                            result['status'] = self.controller.status()
                        except Exception as error:
                            self._error(error)
                            result['error'] = str(error)
                        finally:
                            done.set()
                    generation = self.generation
                frame = self.mailbox.take(self.sequence, .2)
                if frame is None:
                    with self.lock:
                        if time.monotonic()-self.last_frame > 2 and self.controller.mode != 'off':
                            self._error('video frames missing for2s')
                    continue
                self.sequence = frame['sequence']
                self.last_frame = time.monotonic()
                try:
                    measurement = measure_luma(frame['data'], frame['width'], frame['height'],
                                               frame['stride'], frame['offset'], self.roi)
                    with self.lock:
                        self.current_frame = frame
                        if generation != self.generation or self.stopping:
                            continue
                        action = self.controller.feed(measurement, time.monotonic(),
                                                      self.sequence, frame['captured_at'])
                        self._sync_locks()
                        if action is not None:
                            self._move(action)
                        now = time.monotonic()
                        if now-self.last_heartbeat > 1:
                            self.last_heartbeat = now
                            self.emit(dict(event='frame', **self.controller.status(),
                                           measured_score=measurement['score'],
                                           mean=measurement['mean'], valid=measurement['valid'],
                                           invalid_reason=measurement['reason'], sequence=self.sequence,
                                           pts=frame.get('pts'), monotonic=now,
                                           captured_at=frame['captured_at']))
                except Exception as error:
                    with self.lock:
                        self._error(error)
        finally:
            # Shared sensor power is still available: caller stops Gst after join.
            try:
                self.set_locks(False)
            finally:
                self.lens.close()
                with self.lock:
                    while self.commands:
                        _, _, done, result = self.commands.popleft()
                        result['error'] = 'AF worker stopped'
                        done.set()

    def close(self):
        with self.lock:
            self.stopping = True
            self.generation += 1
        self.mailbox.wake()
        # Never let a timeout turn the camera off while an in-flight lens I/O or
        # scoring operation still owns the lens FD. Frame waits are interruptible.
        self.thread.join()
