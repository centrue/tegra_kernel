# SPDX-License-Identifier: GPL-2.0-only
"""Synthetic algorithm tests; run with python -m unittest discover -s tests -p test_imx708_isp_tools.py."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/imx708'))
try:
    import isp_tools as isp
except ImportError:
    isp = None


class ISPToolsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(isp, 'RAW/calibration implementation is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'frame.raw'

    def raw(self, tile=((864, 464), (264, 164)), stride=12):
        rows = np.full((4, stride // 2), 65535, dtype='<u2')
        rows[:, :4] = np.tile(tile, (2, 2))
        self.path.write_bytes(rows.tobytes())
        return isp.read_raw(self.path, 4, 4, stride=stride)

    def test_rggb_planes_and_little_endian_padded_stride(self):
        rgb = self.raw()
        np.testing.assert_allclose(rgb, np.tile([800, 300, 100], (2, 2, 1)))
        planes = isp.split_bayer(np.tile([[864, 464], [264, 164]], (2, 2)), 'RGGB')
        np.testing.assert_array_equal(planes[0, 0], [864, 464, 264, 164])

    def test_keeps_both_green_planes_for_clipping_detection(self):
        mosaic = np.full((4,4),128,dtype='<u2')
        mosaic[0,1] = 1023
        self.path.write_bytes(mosaic.tobytes())
        planes = isp.read_raw(self.path,4,4,return_planes=True)
        self.assertEqual(planes.shape,(2,2,4))
        self.assertEqual(planes[0,0,1],959)
        self.assertLess(isp.read_raw(self.path,4,4)[0,0,1],939.82)

    def test_wrong_bayer_is_observable_and_unknown_bayer_rejected(self):
        right = self.raw()
        wrong = isp.read_raw(self.path, 4, 4, stride=12, bayer='BGGR')
        self.assertFalse(np.allclose(right, wrong))
        np.testing.assert_allclose(wrong[0, 0], [100, 300, 800])
        for pattern, tile in [('BGGR', [[164, 264], [464, 864]]),
                              ('GRBG', [[464, 864], [164, 264]]),
                              ('GBRG', [[264, 164], [864, 464]])]:
            self.path.write_bytes(np.tile(tile, (2, 2)).astype('<u2').tobytes())
            np.testing.assert_allclose(isp.read_raw(self.path, 4, 4, bayer=pattern)[0, 0],
                                       [800, 300, 100])
        with self.assertRaises(ValueError):
            isp.read_raw(self.path, 4, 4, bayer='RGBG')

    def test_bad_stride_size_truncation_empty_and_high_bits_rejected(self):
        self.raw()
        for kwargs in [dict(stride=6), dict(stride=9), dict(black=1023)]:
            with self.assertRaises(ValueError):
                isp.read_raw(self.path, 4, 4, **kwargs)
        for payload in [b'', self.path.read_bytes()[:-1], b'\xff\xff' * 16]:
            self.path.write_bytes(payload)
            with self.assertRaises(ValueError):
                isp.read_raw(self.path, 4, 4)
        with self.assertRaises(ValueError):
            isp.read_raw(self.path, 3, 4)

    def test_multiframe_selection_is_explicit(self):
        self.raw(stride=8)
        self.path.write_bytes(self.path.read_bytes() * 2)
        np.testing.assert_allclose(isp.read_raw(self.path, 4, 4, frame=1)[0, 0], [800, 300, 100])
        with self.assertRaises(ValueError):
            isp.read_raw(self.path, 4, 4, frame=2)

    def test_measured_gray_white_balance_and_dark_rejection(self):
        greys = np.array([[100, 200, 50], [200, 400, 100], [50, 100, 25]])
        white, gains = isp.measure_wb(greys)
        np.testing.assert_allclose(white, [.5, .25])
        np.testing.assert_allclose(gains, [2, 1, 4])
        corrected = greys * gains
        np.testing.assert_allclose(corrected[:, 0], corrected[:, 1])
        np.testing.assert_allclose(corrected[:, 2], corrected[:, 1])
        with self.assertRaises(ValueError):
            isp.measure_wb([[0, 0, 0]])

    def test_ccm_fit_orientation_and_neutral_preservation(self):
        rng = np.random.default_rng(708)
        x = rng.uniform(.1, .7, (30, 3))
        expected = np.array([[1.2, -.15, -.05], [-.1, 1.15, -.05], [.05, -.2, 1.15]])
        target = x @ expected.T
        matrix = isp.fit_ccm(x, target, ridge=0)
        np.testing.assert_allclose(matrix, expected, atol=1e-12)
        np.testing.assert_allclose(x @ matrix.T, target, atol=1e-12)
        np.testing.assert_allclose(matrix @ np.ones(3), np.ones(3), atol=1e-12)
        with self.assertRaises(ValueError):
            isp.fit_ccm(np.ones((18, 3)), np.ones((18, 3)))

    def test_chart_roi_rejects_outside_instead_of_silently_clipping(self):
        centres = isp.patch_centres(isp.parse_corners('4,4;24,4;4,16;24,16'))
        self.assertEqual(centres.shape, (24, 2))
        image = np.full((20, 30, 3), .3)
        np.testing.assert_allclose(isp.sample_patches(image, centres, 2), .3)
        with self.assertRaises(ValueError):
            isp.sample_patches(image, centres, 6)
        with self.assertRaises(ValueError):
            isp.parse_corners('1,2;3,4')

    def config(self):
        return {'schema_version': 1, 'sensor': 'IMX708', 'sources': [{
            'name': 'measured lamp', 'cct': 5000, 'white': [.5, .25],
            'wb': [2, 1, 4], 'ccm': [[1.2, -.15, -.05], [-.1, 1.15, -.05], [.05, -.2, 1.15]],
            'capture': {'sha256': 'a' * 64, 'chart': 'ColorChecker Classic reflective', 'reference_kind': 'reflective'},
        }]}

    def test_generator_keys_transposition_and_measured_fusion(self):
        output = isp.generate_override(self.config())
        self.assertEqual(output.count('awb.v4.FusionLights['), 8)
        self.assertIn('awb.v4.FusionLights[0] = {450,900,900,225};', output)
        self.assertIn('colorCorrection.numColorCorrectionMatrices = 1;', output)
        self.assertIn('awb.v4.FusionInitLight = 4;', output)
        self.assertIn('colorCorrection.set[0].cct = 5000;', output)
        self.assertIn('ccMatrix[0] = { 1.2, -0.1, 0.05, 0.0 };', output)
        self.assertNotIn('lensShading', output)
        self.assertNotIn('manualBias', output)
        with self.assertRaises(ValueError):
            isp.generate_override({'schema_version': 1, 'sensor': 'IMX708', 'sources': []})
        config = self.config()
        config['sources'][0]['ccm'][0][0] = float('nan')
        with self.assertRaises(ValueError):
            isp.generate_override(config)

    def test_score_exact_reference_and_non_neutral_error(self):
        perfect = isp.score_chart(isp.REFERENCE_LINEAR)
        self.assertLess(perfect['colour_delta_e76_mean'], 1e-10)
        tinted = isp.score_chart(isp.REFERENCE_LINEAR * [1.2, 1, .8])
        self.assertGreater(tinted['colour_delta_e76_mean'], 1)
        self.assertGreater(tinted['gray_chroma_mean'], 1)

    def test_calibration_rejects_one_clipped_green_before_averaging(self):
        camera = isp.REFERENCE_LINEAR * [.5,1,.75]*600
        rgb = np.repeat(np.repeat(camera.reshape(4,6,3),16,axis=0),16,axis=1)
        mosaic=np.empty((128,192),dtype='<u2')
        mosaic[::2,::2]=np.rint(rgb[...,0]+64)
        mosaic[::2,1::2]=mosaic[1::2,::2]=np.rint(rgb[...,1]+64)
        mosaic[1::2,1::2]=np.rint(rgb[...,2]+64)
        mosaic[16,17]=1023
        self.path.write_bytes(mosaic.tobytes())
        output=Path(self.tmp.name)/'must-not-exist.json'
        cmd=[sys.executable,str(ROOT/'tools/imx708/calibrate.py'),'fit',
             '--raw',str(self.path),'--size','192x128','--corners','16,16;176,16;16,112;176,112',
             '--reference-kind','display','--half','4','--min-signal','0','--cct','6500',
             '--illuminant','synthetic test','--out',str(output)]
        result=subprocess.run(cmd,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('clipped',result.stderr)
        self.assertFalse(output.exists())

    def test_calibration_cli_synthetic_chart_and_missing_chart(self):
        # Chart RGB is linear with a known camera white point and 1.5x exposure.
        camera = isp.REFERENCE_LINEAR * [.5, 1, .75] * 600
        rgb = np.repeat(np.repeat(camera.reshape(4, 6, 3), 16, axis=0), 16, axis=1)
        mosaic = np.empty((128, 192), dtype='<u2')
        mosaic[0::2, 0::2] = np.rint(rgb[:, :, 0] + 64)
        mosaic[0::2, 1::2] = mosaic[1::2, 0::2] = np.rint(rgb[:, :, 1] + 64)
        mosaic[1::2, 1::2] = np.rint(rgb[:, :, 2] + 64)
        self.path.write_bytes(mosaic.tobytes())
        out = Path(self.tmp.name) / 'fit.json'
        override = out.with_suffix('.isp')
        cmd = [sys.executable, str(ROOT / 'tools/imx708/calibrate.py'), 'fit',
               '--raw', str(self.path), '--size', '192x128', '--corners', '16,16;176,16;16,112;176,112',
               '--reference-kind', 'reflective', '--half', '4', '--min-signal', '0', '--cct', '5000', '--illuminant', 'synthetic test only',
               '--out', str(out), '--override', str(override)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(out.read_text())
        source = config['sources'][0]
        np.testing.assert_allclose(source['wb'], [2, 1, 4 / 3], atol=.025)
        self.assertLess(source['scores']['after']['colour_delta_e76_mean'], 1)
        self.assertEqual(len(source['capture']['sha256']), 64)
        self.assertTrue(override.is_file())
        # Emissive display data must never be labelled reflective calibration.
        display_cmd = cmd.copy()
        display_cmd[display_cmd.index('reflective')] = 'display'
        result = subprocess.run(display_cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        display = json.loads(out.read_text())
        self.assertEqual(display['sources'][0]['capture']['reference_kind'], 'display')
        self.assertIn('display', display['status'].lower())
        self.assertTrue(display['warnings'])
        self.assertIn('DISPLAY EXPERIMENT ONLY', override.read_text())
        self.assertIn('display', result.stderr.lower())
        generated = subprocess.run([sys.executable, str(ROOT / 'tools/imx708/calibrate.py'),
                                    'generate', str(out), '--out', str(override)], capture_output=True, text=True)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        self.assertIn('DISPLAY EXPERIMENT ONLY', override.read_text())
        out.unlink()
        override.unlink()
        result = subprocess.run([sys.executable, str(ROOT / 'tools/imx708/calibrate.py'), 'fit',
                                 '--out', str(out)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(out.exists())
        self.assertFalse(override.exists())

class CaptureAndScoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_score_cli_image_identity_and_metadata(self):
        from PIL import Image
        image = np.repeat(np.repeat(isp.SRGB8.reshape(4, 6, 3), 16, axis=0), 16, axis=1).astype('uint8')
        path = self.root / 'chart.png'
        Image.fromarray(image).save(path)
        output = self.root / 'score.json'
        result = subprocess.run([sys.executable, str(ROOT / 'tools/imx708/score.py'),
                                 '--reference-kind', 'reflective', '--corners', '8,8;88,8;8,56;88,56', '--half', '3',
                                 '--out', str(output), str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(output.read_text())['images'][0]
        self.assertLess(record['scores']['colour_delta_e76_mean'], 1e-9)
        self.assertEqual(len(record['sha256']), 64)
        self.assertEqual(record['clipped_fraction'], 0)

    def capture(self, policy, journal='Loaded override file: {override}', fail=False,
                existing=True, restart=False):
        settings = self.root / 'settings'
        settings.mkdir(exist_ok=True)
        override = settings / 'camera_overrides.isp'
        if existing:
            override.write_text('original\n')
            override.chmod(0o640)
        candidate = self.root / 'candidate.isp'
        candidate.write_text('candidate\n')
        (settings / 'nvcam_cache_test.bin').write_bytes(b'cache')
        fake = self.root / 'bin'
        fake.mkdir(exist_ok=True)
        # Simulate only OS/service boundaries; calibration tests use real math.
        commands = {
            'systemctl': '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n',
            'journalctl': '#!/bin/sh\ncase " $* " in *" --show-cursor "*) echo "-- cursor: test-cursor";; *) printf "%s\\n" "$JOURNAL";; esac\n',
            'gst-launch-1.0': '''#!/usr/bin/env python3
import os, pathlib, sys
if os.environ.get('FAIL_CAPTURE') == '1': sys.exit(9)
for arg in sys.argv:
    if arg.startswith('location='):
        pattern = arg.split('=', 1)[1]
        pathlib.Path(pattern.replace('%03d', '049')).write_bytes(bytes(8*4*3//2))
''',
        }
        for name, content in commands.items():
            p = fake / name
            p.write_text(content)
            p.chmod(0o755)
        calls = self.root / 'calls'
        env = dict(os.environ, PATH=str(fake) + os.pathsep + os.environ['PATH'],
                   IMX708_SETTINGS_DIR=str(settings), CALLS=str(calls),
                   JOURNAL=journal.format(override=override), FAIL_CAPTURE=str(int(fail)))
        output = self.root / 'capture'
        command = ['bash', str(ROOT / 'tools/imx708/capture-isp.sh'),
                   str(candidate) if policy == 'candidate' else policy, str(output),
                   '--size', '8x4']
        if restart:
            command.append('--restart')
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        return result, override, output, calls, settings

    def test_capture_restores_contents_mode_owner_and_records_last_frame(self):
        result, override, output, calls, settings = self.capture('candidate')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(override.read_text(), 'original\n')
        self.assertEqual(override.stat().st_mode & 0o777, 0o640)
        self.assertEqual(override.stat().st_uid, os.getuid())
        self.assertEqual(override.stat().st_gid, os.getgid())
        self.assertEqual(len(calls.read_text().splitlines()), 2)
        self.assertFalse((settings / 'nvcam_cache_test.bin').exists())
        self.assertEqual((output / 'last.i420').stat().st_size, 48)
        metadata = json.loads((output / 'metadata.json').read_text())
        self.assertTrue(metadata['override_load_verified'])
        self.assertEqual(metadata['exit_code'], 0)
        self.assertEqual(len(metadata['override_sha256']), 64)
        self.assertEqual(len(metadata['frame_sha256']), 64)
        self.assertIn('nvvidconv', (output / 'command.txt').read_text())
        self.assertIn('format=NV12', (output / 'command.txt').read_text())
        self.assertIn('format=I420', (output / 'command.txt').read_text())

    def test_capture_accepts_r32_found_override_after_successful_capture(self):
        result, override, output, _, _ = self.capture('candidate',
            journal='---- imager: Found override file [{override}]. ----')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(json.loads((output/'metadata.json').read_text())['override_load_verified'])
        self.assertEqual(override.read_text(),'original\n')

    def test_capture_parser_error_restores_and_fails(self):
        result, override, output, calls, _ = self.capture('candidate', journal='Bad parameter at Line 3')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(override.read_text(), 'original\n')
        self.assertEqual(override.stat().st_mode & 0o777, 0o640)
        self.assertEqual(len(calls.read_text().splitlines()), 2)
        self.assertNotEqual(json.loads((output / 'metadata.json').read_text())['exit_code'], 0)

    def test_capture_requires_positive_path_specific_load_evidence(self):
        for journal in ['No override file found', 'Loaded default tuning',
                        'Loading override file: {override}',
                        'Loaded override file: {override}.wrong',
                        'Loaded override file: /unrelated/camera_overrides.isp']:
            with self.subTest(journal=journal):
                # independent folders for each shell invocation
                self.root = self.root / 'run'
                self.root.mkdir()
                result, override, output, _, _ = self.capture('candidate', journal=journal)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(override.read_text(), 'original\n')
                self.assertFalse(json.loads((output / 'metadata.json').read_text())['override_load_verified'])

    def test_capture_gstreamer_failure_and_absent_original_restore(self):
        result, override, output, calls, _ = self.capture('candidate', fail=True, existing=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(override.exists())
        self.assertEqual(len(calls.read_text().splitlines()), 2)
        self.assertNotEqual(json.loads((output / 'metadata.json').read_text())['exit_code'], 0)

    def test_keep_and_already_generic_do_not_restart_or_clear_cache(self):
        result, override, output, calls, settings = self.capture('keep')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(calls.exists())
        self.assertTrue((settings / 'nvcam_cache_test.bin').exists())
        self.assertEqual(override.read_text(), 'original\n')
        self.root = self.root / 'generic'
        self.root.mkdir()
        result, override, output, calls, settings = self.capture('none', existing=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(calls.exists())
        self.assertFalse(override.exists())
        self.assertTrue((settings / 'nvcam_cache_test.bin').exists())

    def test_explicit_restart_clears_cache_even_without_mutation(self):
        result, _, _, calls, settings = self.capture('none', existing=False, restart=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls.read_text().splitlines()), 1)
        self.assertFalse((settings / 'nvcam_cache_test.bin').exists())


    def test_flat_field_and_noise_metrics_use_separate_supplied_images(self):
        from PIL import Image
        chart = self.root / 'chart.png'
        Image.fromarray(np.repeat(np.repeat(isp.SRGB8.reshape(4, 6, 3), 16, 0), 16, 1).astype('uint8')).save(chart)
        flat = self.root / 'flat.png'
        pixels = np.full((64, 96, 3), 100, dtype='uint8')
        pixels[20:44, 32:64] = 150
        Image.fromarray(pixels).save(flat)
        out = self.root / 'metrics.json'
        command = [sys.executable, str(ROOT / 'tools/imx708/score.py'), str(chart),
                   '--reference-kind', 'reflective', '--corners', '8,8;88,8;8,56;88,56', '--half', '3',
                   '--flat-field', str(flat), '--noise-pair', str(flat), str(flat), '--out', str(out)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        metrics = json.loads(out.read_text())
        np.testing.assert_allclose(metrics['temporal_noise']['linear_rgb_sigma'], 0)
        self.assertTrue(all(0 < v < 1 for v in metrics['flat_field']['corner_to_centre_luma']))


if __name__ == '__main__':
    unittest.main()
