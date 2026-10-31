import importlib.util
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location("focus_scan", Path(__file__).parents[1] / "tools/imx708/focus-scan.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class FocusScoreTests(unittest.TestCase):
    def test_flat_has_zero_gradient(self):
        self.assertEqual(module.measure(struct.pack('<H', 120) * (64*64), 64, 64)['score'], 0)

    def test_edges_score_higher_than_flat(self):
        pixels = [100 if (x // 8) % 2 else 300 for y in range(64) for x in range(64)]
        result = module.measure(struct.pack('<'+'H'*len(pixels), *pixels), 64, 64)
        self.assertGreater(result['score'], 0)
        self.assertEqual(result['saturated_fraction'], 0)

    def test_rejects_truncated_frame(self):
        with self.assertRaises(ValueError):
            module.measure(b'\0'*8, 64, 64)

    def test_explicit_roi_rejects_outside_image(self):
        with self.assertRaises(ValueError):
            module.measure(struct.pack('<H',120)*(64*64),64,64,(50,50,40,40))

if __name__ == '__main__':
    unittest.main()
