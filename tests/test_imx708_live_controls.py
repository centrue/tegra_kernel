import importlib.util
from pathlib import Path
import tempfile
import unittest
import sys
from unittest import mock

PATH=Path(__file__).parents[1]/'tools/imx708/live_controls.py'
sys.path.insert(0,str(PATH.parent))
spec=importlib.util.spec_from_file_location('live_controls',PATH)
live=importlib.util.module_from_spec(spec) if PATH.exists() else None
if live is not None:spec.loader.exec_module(live)


class Element:
    def __init__(self):self.values={};self.fail=None
    def set_property(self,key,value):
        if key==self.fail:self.fail=None;raise RuntimeError('setter failure')
        self.values[key]=value
    def get_property(self,key):return self.values.get(key)


class Worker:
    def __init__(self):self.calls=[];self.position=688
    def command(self,kind,value=None):
        self.calls.append((kind,value))
        if kind=='focus':self.position=value
    def status(self):return dict(position=self.position,mode='off',state='MANUAL')


class LiveControlsTests(unittest.TestCase):
    def setUp(self):self.assertIsNotNone(live,'live tuning backend missing')

    def make(self,path):
        elements=[Element() for _ in range(4)]
        worker=Worker()
        return live.Settings(*elements,worker,path),elements,worker

    def test_live_ranges_rgb_and_manual_focus_defaults(self):
        with tempfile.TemporaryDirectory() as path:
            controls,elements,worker=self.make(path)
            state=controls.apply({'exposure_ms':12.5,'gain':3,'red':.6})
            self.assertEqual(elements[0].values['exposuretimerange'],'12500000 12500000')
            self.assertEqual(elements[0].values['gainrange'],'3 3')
            self.assertEqual(elements[1].values['r'],.6)
            self.assertEqual(state['focus'],688)
            self.assertIn(('mode','off'),worker.calls)

    def test_invalid_input_cannot_change_any_properties(self):
        with tempfile.TemporaryDirectory() as path:
            controls,elements,_=self.make(path)
            for value in ({'gain':float('nan')},{'gain':17},{'wbmode':9},
                          {'exposure_ms':500},{'auto_exposure':'false'},{'unknown':1}):
                before=[dict(e.values) for e in elements]
                with self.assertRaises(ValueError):controls.apply(value)
                self.assertEqual([e.values for e in elements],before)

    def test_property_failure_rolls_back_and_does_not_save_state(self):
        with tempfile.TemporaryDirectory() as path:
            controls,elements,_=self.make(path);controls.apply({})
            before=controls.get();elements[1].fail='r'
            with self.assertRaises(RuntimeError):controls.apply({'red':.7,'gain':5})
            self.assertEqual(controls.get(),before)
            self.assertEqual(elements[0].values['gainrange'],'2 2')

    def test_preset_round_trip_restores_focus_and_validates_before_applying(self):
        with tempfile.TemporaryDirectory() as path:
            controls,_,worker=self.make(path)
            controls.apply({'green':.45,'exposure_ms':8});worker.position=640
            controls.save();controls.apply({'green':.7});worker.position=700
            state=controls.load()
            self.assertEqual(state['green'],.45);self.assertEqual(state['focus'],640)
            self.assertTrue((Path(path)/'settings.json').exists())

    def test_hardware_readback_is_separate_from_requested_settings(self):
        with mock.patch('camera_io.read_register',side_effect=[2412,748,512,4989,7824]), \
             mock.patch.object(Path,'read_bytes',return_value=b'585600000\0'):
            state=live.hardware_state()
        self.assertAlmostEqual(state['exposure_ms'],9.998,places=3)
        self.assertEqual(state['gain'],2.)
        self.assertGreater(state['frame_ms'],66)


if __name__=='__main__':unittest.main()
