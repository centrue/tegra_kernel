import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

ROOT=Path(__file__).parents[1]/'tools/imx708';sys.path.insert(0,str(ROOT))


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


class ResolutionTests(unittest.TestCase):
    def test_all_driver_modes_are_selectable_and_cap_preview_fps(self):
        preview=module('preview_modes',ROOT/'preview-x11.py')
        for index,w,h,maximum in [(0,4608,2592,14),(1,2304,1296,56),(2,1536,864,120)]:
            args=SimpleNamespace(sensor_mode=index,fps=min(15,maximum),exposure_ns=None,
                gain=None,isp_digital_gain=1,headless=True,width=960,height=540)
            pipeline=preview.build_pipeline(args,'')
            self.assertIn('sensor-mode=%d'%index,pipeline)
            self.assertIn('width=%d,height=%d,format=NV12'%(w,h),pipeline)

    def test_gui_child_passes_selected_sensor_mode(self):
        tuner=module('tuner_modes',ROOT/'camera-tuner.py')
        self.assertTrue(hasattr(tuner,'RESOLUTIONS'),'resolution choices missing')
        for resolution,index in [('4608x2592',0),('2304x1296',1),('1536x864',2)]:
            command=tuner.child_command(123,'/tmp/output','/tmp/socket',600,resolution)
            self.assertEqual(command[command.index('--sensor-mode')+1],str(index))

    def test_restart_preset_preserves_user_values_without_overwriting_saved_preset(self):
        tuner=module('tuner_restart',ROOT/'camera-tuner.py')
        self.assertTrue(hasattr(tuner,'restart_preset'),'mode restart snapshot missing')
        with tempfile.TemporaryDirectory() as directory:
            saved=Path(directory)/'settings.json';saved.write_text('original')
            payload=dict(exposure_ms=17.5,gain=1.125,red=.46,focus=639,
                         auto_exposure=False,roi=[.1,.2,.3,.3],output=directory)
            path=tuner.restart_preset(directory,payload,'4608x2592')
            import json
            result=json.loads(Path(path).read_text())
            self.assertEqual(result['focus'],639)
            self.assertEqual(result['resolution'],'4608x2592')
            self.assertEqual(result['settings']['red'],.46)
            self.assertEqual(saved.read_text(),'original')


if __name__=='__main__':unittest.main()
