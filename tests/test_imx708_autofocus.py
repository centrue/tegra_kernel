import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).parents[1] / 'tools/imx708/autofocus.py'
spec = importlib.util.spec_from_file_location('autofocus', PATH)
af = importlib.util.module_from_spec(spec) if PATH.exists() else None
if af is not None:
    spec.loader.exec_module(af)


def sample(score=1., mean=100., thumb=0.):
    return dict(score=score, mean=mean, variance=100., clipped=0.,
                thumbnail=(thumb,) * 16, valid=True, reason='')


class AutofocusTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(af, 'autofocus module must exist')

    def test_luma_stride_roi_and_brightness_normalization(self):
        w, h, stride = 64, 48, 72
        def image(scale):
            data = bytearray([255] * (stride*h+8))
            for y in range(h):
                for x in range(w):
                    data[8+y*stride+x] = 16 + scale*(20 if (x//4+y//4)%2 else 60)
            return data
        a = af.measure_luma(image(1), w, h, stride, 8)
        b = af.measure_luma(image(2), w, h, stride, 8)
        self.assertTrue(a['valid'])
        self.assertAlmostEqual(a['score'], b['score'], places=6)
        self.assertEqual(a['clipped'], 0)
        self.assertFalse(af.measure_luma(bytes([16])*w*h, w, h)['valid'])
        with self.assertRaises(ValueError):
            af.measure_luma(b'bad', w, h)

    def test_peak_requires_prominence_and_bracketing(self):
        self.assertIsNone(af.choose_peak([(1, 1.), (2, 2.), (3, 3.)]))
        self.assertIsNone(af.choose_peak([(1, 1.), (2, 1.01), (3, 1.)]))
        self.assertEqual(af.choose_peak([(1, 1.), (2, 2.), (3, 1.)]), (2, 2.))

    def test_motion_thumbnail_averages_fine_texture(self):
        # A one-pixel phase change in fine screen texture is not a new subject.
        w, h = 320, 240
        frames = [bytes(80 if (x+phase)%2 else 120 for y in range(h) for x in range(w))
                  for phase in (0, 1)]
        a, b = [af.measure_luma(frame,w,h) for frame in frames]
        self.assertLess(af.distance(a['thumbnail'],b['thumbnail']), .08)

    def drive(self, engine, peak=688, seconds=24., start=0.):
        now = start
        seq = 0
        positions = []
        while now < start+seconds:
            now += 1/15
            seq += 1
            score = 0.1 + 10 / (1+((engine.position-peak)/35)**2)
            action = engine.feed(sample(score), now, seq, now)
            if action is not None:
                positions.append(action)
                engine.moved(action, now, seq)
            if engine.state == 'LOCKED':
                break
        return now, seq, positions

    def test_global_scan_converges_and_holds(self):
        engine = af.Controller()
        engine.set_mode('continuous', 0.)
        now, seq, positions = self.drive(engine)
        self.assertEqual(engine.state, 'LOCKED')
        self.assertLessEqual(abs(engine.position-688), 4)
        self.assertLess(now, 25)
        for n in range(1, 901):
            self.assertIsNone(engine.feed(sample(engine.locked_score), now+n/15,
                                          seq+n, now+n/15))
        self.assertEqual(engine.state, 'LOCKED')

    def test_old_frames_never_score_and_manual_cancels(self):
        engine = af.Controller()
        engine.set_mode('continuous', 0.)
        position = None
        for n in range(1, 20):
            position = engine.feed(sample(), n/15, n, n/15)
            if position is not None:
                break
        self.assertIsNotNone(position)
        engine.moved(position, 2., 30)
        for n in range(10):
            engine.feed(sample(100), 2.2+n/15, 31+n, 1.9)
        self.assertEqual(len(engine.samples), 0)
        self.assertEqual(engine.manual(700), 700)
        engine.moved(700, 3., 50)
        for n in range(20):
            self.assertIsNone(engine.feed(sample(), 3.1+n/15, 51+n, 3.1+n/15))
        self.assertEqual(engine.state, 'MANUAL')
        self.assertEqual(engine.position, 700)

    def test_no_texture_and_motion_do_not_start_scan(self):
        engine = af.Controller()
        engine.set_mode('continuous', 0.)
        for n in range(60):
            bad = sample()
            bad.update(valid=False, reason='low texture')
            self.assertIsNone(engine.feed(bad, n/15, n, n/15))
        for n in range(60, 100):
            self.assertIsNone(engine.feed(sample(thumb=n%2), n/15, n, n/15))
        self.assertEqual(engine.position, 688)

    def test_scene_change_reacquires_even_if_score_rises(self):
        engine = af.Controller()
        engine.set_mode('continuous', 0.)
        now, seq, _ = self.drive(engine)
        actions = []
        for n in range(1, 80):
            action = engine.feed(sample(engine.locked_score*2, thumb=.4),
                                 now+n/15, seq+n, now+n/15)
            if action is not None:
                actions.append(action)
                break
        self.assertTrue(actions)
        self.assertTrue(engine.searching)

    def test_search_timeout_restores_previous_position(self):
        engine = af.Controller()
        engine.set_mode('continuous', 0.)
        for n in range(1, 30):
            action = engine.feed(sample(), n/15, n, n/15)
            if action is not None:
                engine.moved(action, n/15, n)
                break
        self.assertTrue(engine.searching)
        self.assertEqual(engine.tick(60.), 688)
        self.assertFalse(engine.searching)
        self.assertIn('timeout', engine.reason)


if __name__ == '__main__':
    unittest.main()
