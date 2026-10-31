import importlib.util
from pathlib import Path
import unittest
import sys

ROOT=Path(__file__).parents[1]/'tools/imx708'
sys.path.insert(0,str(ROOT))
PATH=ROOT/'libcamera_af.py'
spec=importlib.util.spec_from_file_location('libcamera_af',PATH)
af=importlib.util.module_from_spec(spec) if PATH.exists() else None
if af is not None:spec.loader.exec_module(af)


def metric(contrast,mean=80,thumb=0):
    return dict(contrast=contrast,score=contrast/4096,mean=mean,variance=100,
                clipped=0,valid=True,reason='',thumbnail=(thumb,)*192)


class LibcameraAfTests(unittest.TestCase):
    def setUp(self):self.assertIsNotNone(af,'libcamera CDAF controller missing')

    def test_quadratic_estimate_between_measured_positions(self):
        points=[(x,100-(x-2.3)**2*10) for x in (1.,2.,3.)]
        self.assertAlmostEqual(af.find_peak(points,1),2.3)

    def run_scan(self,peak,mode='once',position=688,seconds=15):
        events=[];controller=af.Controller(position=position,emit=events.append)
        controller.set_mode(mode,0)
        positions=[];now=0
        for seq in range(1,int(seconds*15)+1):
            now=seq/15
            value=10+1000/(1+((controller.position-peak)/24)**2)
            action=controller.feed(metric(value),now,seq,now)
            if action is not None:
                positions.append(action);controller.moved(action,now,seq)
            if controller.state in ('LOCKED','FAILED'):break
        return controller,now,positions,events

    def test_scan_finds_near_and_far_peak_without_full_sweep(self):
        for peak in (500,620,780,900):
            controller,elapsed,positions,events=self.run_scan(peak)
            self.assertEqual(controller.state,'LOCKED',(peak,controller.status()))
            self.assertLessEqual(abs(controller.position-peak),10)
            self.assertLess(elapsed,12)
            self.assertLess(len(positions),25)

    def test_continuous_scan_reverses_when_starting_on_wrong_side(self):
        controller,elapsed,positions,events=self.run_scan(800,'continuous',640)
        self.assertEqual(controller.state,'LOCKED')
        self.assertLessEqual(abs(controller.position-800),10)
        self.assertTrue(any(e.get('state')=='COARSE1' for e in events))
        self.assertTrue(any(e.get('state')=='COARSE2' for e in events))

    def test_small_dark_mean_drift_does_not_abort_a_real_peak(self):
        controller=af.Controller();controller.set_mode('once',0)
        for seq in range(1,225):
            now=seq/15
            score=10+1000/(1+((controller.position-620)/24)**2)
            mean=24.5+2*(controller.position-445)/480
            action=controller.feed(metric(score,mean),now,seq,now)
            if action is not None:controller.moved(action,now,seq)
            if controller.state in ('LOCKED','FAILED'):break
        self.assertEqual(controller.state,'LOCKED')

    def test_scene_change_at_settle_cannot_be_confirmed(self):
        controller=af.Controller();controller.set_mode('once',0)
        for seq in range(1,225):
            now=seq/15;score=10+1000/(1+((controller.position-620)/24)**2)
            action=controller.feed(metric(score,thumb=.5 if controller.state=='SETTLE' else 0),now,seq,now)
            if action is not None:controller.moved(action,now,seq)
            if controller.state in ('LOCKED','FAILED'):break
        self.assertEqual(controller.state,'FAILED')
        self.assertIn('scene',controller.reason)

    def test_flat_scan_fails_and_manual_cancels(self):
        controller=af.Controller();controller.set_mode('once',0)
        for seq in range(1,225):
            now=seq/15;action=controller.feed(metric(10),now,seq,now)
            if action is not None:controller.moved(action,now,seq)
            if controller.state=='FAILED':break
        self.assertEqual(controller.state,'FAILED')
        self.assertEqual(controller.manual(700),700)
        controller.moved(700,20,300)
        self.assertIsNone(controller.feed(metric(1000),21,301,21))
        self.assertEqual(controller.status()['mode'],'off')


if __name__=='__main__':unittest.main()
