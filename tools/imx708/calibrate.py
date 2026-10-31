#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Method/reference adapted from ov5647-jetson/tools/isp by
# Copyright (C) 2026 Artur Andrzejczak <andrzejczak.artur@gmail.com>
"""Fit a labelled chart RAW capture, or generate its measured candidate ISP text."""
import argparse
import datetime
import json
from pathlib import Path
import shlex
import sys

import numpy as np

from isp_tools import (GRAY_INDICES, PATTERNS, REFERENCE_LINEAR, fit_ccm,
                       generate_override, measure_wb, parse_corners, parse_size,
                       patch_boxes, patch_centres, read_raw, sample_patches,
                       score_chart, sha256)


def fit(args):
    width, height = parse_size(args.size)
    rgb = read_raw(args.raw, width, height, black=args.black, bayer=args.bayer,
                   stride=args.stride, bit_depth=args.bit_depth, frame=args.frame)
    centres = patch_centres(parse_corners(args.corners) / 2)
    boxes = patch_boxes(rgb, centres, args.half)
    full = (1 << args.bit_depth) - 1 - args.black
    # Inspect every ROI pixel, not just its median, before fitting.
    if any(np.any(box >= args.clip_fraction * full) for box in boxes):
        raise ValueError('chart contains clipped/near-clipped pixels; reduce exposure')
    patches = sample_patches(rgb, centres, args.half)
    if np.any(patches <= args.min_signal):
        raise ValueError('chart has dark/nonpositive patch channels; improve exposure or check black')
    white, wb = measure_wb(patches[GRAY_INDICES])
    x = patches * wb
    exposure_scale = REFERENCE_LINEAR[GRAY_INDICES, 1].sum() / x[GRAY_INDICES, 1].sum()
    x *= exposure_scale
    matrix = fit_ccm(x[:18], REFERENCE_LINEAR[:18], args.ridge)
    source = {'name': args.illuminant, 'cct': args.cct, 'white': white.tolist(),
              'wb': wb.tolist(), 'ccm': matrix.tolist(), 'patches_dn': patches.tolist(),
              'exposure_scale': float(exposure_scale),
              'scores': {'wb_only': score_chart(x), 'after': score_chart(x @ matrix.T)},
              'capture': {'file': str(Path(args.raw).resolve()), 'sha256': sha256(args.raw),
                          'chart': ('ColorChecker Classic reflective' if args.reference_kind == 'reflective'
                                    else 'ColorChecker Classic approximate display'),
                          'reference_kind': args.reference_kind,
                          'size': [width, height], 'bayer': args.bayer,
                          'bit_depth': args.bit_depth, 'storage': 'right-aligned LE16',
                          'stride_bytes': args.stride or width * 2, 'black_dn': args.black,
                          'frame': args.frame, 'corners_sensor_px': parse_corners(args.corners).tolist(),
                          'patch_half_bayer_quad_px': args.half,
                          'exposure_ns': args.exposure_ns, 'gain': args.gain,
                          'focus_dac': args.focus, 'illuminant': args.illuminant,
                          'cct_kelvin': args.cct, 'clip_fraction': args.clip_fraction,
                          'min_signal_dn': args.min_signal}}
    warnings = []
    if args.reference_kind == 'display':
        warnings.append('DISPLAY EXPERIMENT ONLY: emissive sRGB patches are not a certified reflective ColorChecker. '
                        'Results depend on display profile/brightness, spectrum, camera exposure and flicker; '
                        'do not use as general-purpose or cross-illuminant ISP calibration.')
        print(warnings[0], file=sys.stderr)
    config = {'schema_version': 1, 'sensor': 'IMX708',
              'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'command': shlex.join([sys.executable] + sys.argv),
              'fit': {'method': 'constrained linear least squares on 18 colour patches',
                      'ridge': args.ridge, 'orientation': 'linear_srgb = camera_wb @ ccm.T'},
              'status': ('display experiment only' if warnings else
                         'candidate; requires independent ISP and illuminant validation'),
              'warnings': warnings,
              'sources': [source]}
    # Validate the candidate before writing either output; no chart => no output.
    text = generate_override(config)
    if args.override and Path(args.override).resolve() == Path(args.out).resolve():
        raise ValueError('JSON and ISP output paths must differ')
    Path(args.out).write_text(json.dumps(config, indent=2, allow_nan=False) + '\n')
    if args.override:
        Path(args.override).write_text(text)
    print(json.dumps(source['scores'], indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('fit', help='one measured source; all coordinates are sensor pixels')
    p.add_argument('--raw', required=True)
    p.add_argument('--reference-kind', choices=['display', 'reflective'], required=True,
                   help='display is experimental only; never a reflected chart calibration')
    p.add_argument('--size', required=True, help='WIDTHxHEIGHT')
    p.add_argument('--corners', required=True, help='TL;TR;BL;BR patch centres, x,y each')
    p.add_argument('--bayer', choices=PATTERNS, default='RGGB')
    p.add_argument('--bit-depth', type=int, default=10)
    p.add_argument('--stride', type=int, help='bytes per stored row')
    p.add_argument('--black', type=float, default=64)
    p.add_argument('--frame', type=int, default=0)
    p.add_argument('--half', type=int, default=10, help='ROI half size in Bayer quads')
    p.add_argument('--illuminant', required=True, help='measured source description')
    p.add_argument('--cct', required=True, type=float, help='measured matrix label, Kelvin')
    p.add_argument('--exposure-ns', type=int)
    p.add_argument('--gain', type=float)
    p.add_argument('--focus', type=int)
    p.add_argument('--ridge', type=float, default=0)
    p.add_argument('--clip-fraction', type=float, default=.98)
    p.add_argument('--min-signal', type=float, default=1)
    p.add_argument('--out', required=True, help='measurement JSON')
    p.add_argument('--override', help='optional candidate .isp output')
    p = sub.add_parser('generate', help='combine up to eight measured source JSON files')
    p.add_argument('json', nargs='+')
    p.add_argument('--out', required=True)
    args = parser.parse_args()
    try:
        if args.action == 'fit':
            if not 0 < args.clip_fraction <= 1 or not np.isfinite(args.min_signal) or args.min_signal < 0:
                raise ValueError('clip fraction must be (0,1]; minimum signal must be finite and nonnegative')
            fit(args)
        else:
            config = {'schema_version': 1, 'sensor': 'IMX708', 'sources': []}
            for path in args.json:
                loaded = json.loads(Path(path).read_text())
                generate_override(loaded)  # Validate each input, including its schema.
                config['sources'].extend(loaded['sources'])
            text = generate_override(config)
            Path(args.out).write_text(text)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, 'calibrate: %s\n' % error)


if __name__ == '__main__':
    main()
