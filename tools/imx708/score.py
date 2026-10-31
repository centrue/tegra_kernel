#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Colour-reference/scoring method adapted from ov5647-jetson/tools/isp by
# Copyright (C) 2026 Artur Andrzejczak <andrzejczak.artur@gmail.com>
"""Score decoded sRGB chart images, preserving their SHA256 and sampling geometry.

Convert captured I420 to RGB PNG with explicit colour matrix/range first. This
script does not guess the ISP's YUV colourimetry. Flat-field and temporal noise
metrics require appropriate extra inputs, not coloured chart corners.
"""
import argparse
import json
from pathlib import Path
import shlex
import sys

import numpy as np
from PIL import Image

from isp_tools import (parse_corners, patch_boxes, patch_centres, sample_patches,
                       score_chart, sha256, srgb_to_linear)


def image_rgb(path):
    with Image.open(path) as im:
        return np.asarray(im.convert('RGB'), float) / 255


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-kind', choices=['display', 'reflective'], required=True)
    parser.add_argument('images', nargs='+', help='sRGB PNG/JPEG images of the same chart geometry')
    parser.add_argument('--corners', required=True, help='TL;TR;BL;BR patch centres, x,y each')
    parser.add_argument('--scale', type=float, default=1, help='scale coordinates to image size')
    parser.add_argument('--half', type=int, default=10, help='ROI half size in image pixels')
    parser.add_argument('--flat-field', help='separate uniformly lit neutral image for shading ratio')
    parser.add_argument('--noise-pair', nargs=2, help='two fixed-exposure/fixed-WB static frames')
    parser.add_argument('--out', required=True, help='JSON output')
    args = parser.parse_args()
    try:
        if not np.isfinite(args.scale) or args.scale <= 0:
            raise ValueError('coordinate scale must be finite and positive')
        centres = patch_centres(parse_corners(args.corners) * args.scale)
        records = []
        for path in args.images:
            rgb = image_rgb(path)
            patches = sample_patches(srgb_to_linear(rgb), centres, args.half)
            boxes = patch_boxes(rgb, centres, args.half)
            chart_pixels = np.concatenate([b.reshape(-1, 3) for b in boxes])
            records.append({'file': str(Path(path).resolve()), 'sha256': sha256(path),
                            'size': [rgb.shape[1], rgb.shape[0]],
                            'scores': score_chart(patches),
                            'clipped_fraction': float(np.mean(np.any(chart_pixels >= 254 / 255, axis=1))),
                            'black_fraction': float(np.mean(np.any(chart_pixels <= 1 / 255, axis=1)))})
        warnings = (['DISPLAY EXPERIMENT ONLY: nominal sRGB patch error is not certified reflected-chart accuracy.']
                    if args.reference_kind == 'display' else [])
        if warnings:
            print(warnings[0], file=sys.stderr)
        result = {'schema_version': 1, 'reference_kind': args.reference_kind, 'warnings': warnings, 'command': shlex.join([sys.executable] + sys.argv),
                  'assumed_encoding': 'sRGB; CIE76 D65 nominal ColorChecker reference',
                  'corners': (parse_corners(args.corners) * args.scale).tolist(),
                  'half': args.half, 'images': records,
                  'flat_field': None, 'temporal_noise': None}
        if args.flat_field:
            rgb = srgb_to_linear(image_rgb(args.flat_field))
            h, w, _ = rgb.shape
            # Five disjoint small boxes: centre and four corners used for shading.
            pad = max(1, min(h, w) // 20)
            centres_flat = np.array([[w / 2, h / 2], [pad * 2, pad * 2],
                                     [w - pad * 2, pad * 2], [pad * 2, h - pad * 2],
                                     [w - pad * 2, h - pad * 2]])
            values = sample_patches(rgb, centres_flat, pad) @ np.array([.2126, .7152, .0722])
            if values[0] <= 0:
                raise ValueError('flat-field centre is dark')
            result['flat_field'] = {'file': str(Path(args.flat_field).resolve()),
                                    'sha256': sha256(args.flat_field),
                                    'corner_to_centre_luma': (values[1:] / values[0]).tolist()}
        if args.noise_pair:
            a, b = [srgb_to_linear(image_rgb(p)) for p in args.noise_pair]
            if a.shape != b.shape:
                raise ValueError('noise pair sizes differ')
            # Centre crop; subtract channel means to reject a global exposure offset.
            h, w = a.shape[:2]
            difference = (a[h//4:3*h//4, w//4:3*w//4] - b[h//4:3*h//4, w//4:3*w//4]).reshape(-1, 3)
            result['temporal_noise'] = {'files': args.noise_pair,
                                        'sha256': [sha256(p) for p in args.noise_pair],
                                        'linear_rgb_sigma': (difference.std(axis=0) / np.sqrt(2)).tolist(),
                                        'mean_difference': difference.mean(axis=0).tolist(),
                                        'assumption': 'static scene, matched exposure/gain/WB; motion invalidates metric'}
        Path(args.out).write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
        for record in records:
            print('%s: colour dE76 %.3f, gray chroma %.3f' %
                  (record['file'], record['scores']['colour_delta_e76_mean'],
                   record['scores']['gray_chroma_mean']))
    except (OSError, ValueError) as error:
        parser.exit(1, 'score: %s\n' % error)


if __name__ == '__main__':
    main()
