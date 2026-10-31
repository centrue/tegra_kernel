import importlib.util
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).parents[1] / 'tools/imx708'
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('af_runtime', ROOT/'af_runtime.py')
runtime = importlib.util.module_from_spec(spec) if (ROOT/'af_runtime.py').exists() else None
if runtime is not None:
    spec.loader.exec_module(runtime)


class Lens:
    def __init__(self):
        self.positions = []
        self.owners = []
        self.closed = False

    def move(self, value):
        self.positions.append(value)
        self.owners.append(threading.get_ident())
        return value

    def close(self):
        self.closed = True


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(runtime, 'AF runtime must exist')

    def test_latest_frame_mailbox_discards_backlog(self):
        mailbox = runtime.Mailbox()
        mailbox.publish({'value': 1})
        mailbox.publish({'value': 2})
        frame = mailbox.take(0, .01)
        self.assertEqual(frame['value'], 2)
        self.assertEqual(frame['sequence'], 2)
        self.assertIsNone(mailbox.take(2, .01))

    def test_manual_commands_owned_by_worker_and_cancel_af(self):
        mailbox, lens = runtime.Mailbox(), Lens()
        locks = []
        worker = runtime.Worker(mailbox, lens, lambda on: locks.append(on))
        worker.start()
        try:
            worker.command('mode', 'continuous')
            worker.command('focus', 710)
            self.assertEqual(worker.status()['mode'], 'off')
            self.assertEqual(worker.status()['position'], 710)
            self.assertEqual(lens.positions[-1], 710)
            self.assertEqual(len(set(lens.owners)), 1)
            self.assertNotEqual(lens.owners[0], threading.get_ident())
        finally:
            worker.close()
        self.assertTrue(lens.closed)
        self.assertFalse(worker.thread.is_alive())

    def test_hardware_failure_restores_locks_and_reports_error(self):
        class Broken(Lens):
            def move(self, value):
                if value == 700:
                    raise OSError('DAC mismatch')
                return super().move(value)
        locks = []
        worker = runtime.Worker(runtime.Mailbox(), Broken(), lambda on: locks.append(on))
        worker.start()
        try:
            with self.assertRaisesRegex(RuntimeError, 'DAC mismatch'):
                worker.command('focus', 700)
            self.assertEqual(worker.status()['state'], 'ERROR')
        finally:
            worker.close()
        self.assertFalse(locks[-1])

    def test_luma_snapshot_repackages_padding(self):
        frame = dict(data=bytes([99, 1, 2, 88, 3, 4, 88]), width=2,
                     height=2, stride=3, offset=1)
        self.assertEqual(runtime.luma_bytes(frame), bytes([1, 2, 3, 4]))

    def test_preview_control_preserves_legacy_and_exposes_af_status(self):
        spec = importlib.util.spec_from_file_location('preview', ROOT/'preview-x11.py')
        preview = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(preview)
        self.assertTrue(hasattr(preview, 'control_command'), 'AF socket command handler missing')
        worker = runtime.Worker(runtime.Mailbox(), Lens(), lambda on: None)
        worker.start()
        try:
            reply = preview.control_command(worker, 'af continuous')
            self.assertIn('continuous', reply)
            self.assertEqual(preview.control_command(worker, 'focus 720'), 'focus=720\n')
            self.assertEqual(preview.control_command(worker, 'status'), 'focus=720\n')
            self.assertIn('MANUAL', preview.control_command(worker, 'af status'))
            with self.assertRaises(ValueError):
                preview.control_command(worker, 'af nonsense')
        finally:
            worker.close()

    def test_partial_camera_lock_failure_restores_both_properties(self):
        self.assertTrue(hasattr(runtime,'CameraLocks'), 'transactional camera locks missing')
        class Camera:
            def __init__(self):self.values={'aelock':False,'awblock':False};self.fail=('awblock',True)
            def get_property(self,key):return self.values[key]
            def set_property(self,key,value):
                if self.fail==(key,value):
                    self.fail=None
                    raise RuntimeError('property failure')
                self.values[key]=value
        camera=Camera();locks=runtime.CameraLocks(camera)
        with self.assertRaises(RuntimeError):locks(True)
        self.assertEqual(camera.values,{'aelock':False,'awblock':False})
        locks(True);camera.fail=('aelock',False)
        with self.assertRaises(RuntimeError):locks(False)
        self.assertFalse(camera.values['awblock'])
        locks(False)
        self.assertEqual(camera.values,{'aelock':False,'awblock':False})

    def test_shutdown_keeps_pipeline_until_worker_confirmed_closed(self):
        spec=importlib.util.spec_from_file_location('preview',ROOT/'preview-x11.py')
        preview=importlib.util.module_from_spec(spec);spec.loader.exec_module(preview)
        self.assertTrue(hasattr(preview,'shutdown_camera'),'ordered shutdown missing')
        order=[]
        class Closing:
            def close(self):
                order.append('lens')
                raise RuntimeError('still closing')
        with self.assertRaises(RuntimeError):
            preview.shutdown_camera(Closing(),lambda:order.append('locks'),lambda:order.append('stream'))
        self.assertEqual(order,['lens'])

    def test_worker_close_waits_for_lens_beyond_previous_join_timeout(self):
        entered,release=threading.Event(),threading.Event()
        class Slow(Lens):
            def close(self):
                entered.set();release.wait(2);super().close()
        lens=Slow();worker=runtime.Worker(runtime.Mailbox(),lens,lambda on:None)
        worker.start()
        original_join=worker.thread.join
        timeouts=[]
        def record_join(timeout=None):
            timeouts.append(timeout)
            # Reproduce the old timeout without waiting5s.
            original_join(.01 if timeout is not None else None)
        errors=[]
        def close():
            try:worker.close()
            except Exception as error:errors.append(error)
        with mock.patch.object(worker.thread,'join',record_join):
            caller=threading.Thread(target=close);caller.start()
            self.assertTrue(entered.wait(1))
            # A timeout must not let the caller tear down the stream early.
            caller.join(.05)
            self.assertTrue(caller.is_alive())
            release.set();caller.join(1)
        self.assertFalse(errors)
        self.assertTrue(lens.closed)
        self.assertEqual(timeouts,[None])


if __name__ == '__main__':
    unittest.main()
