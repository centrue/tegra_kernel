# SPDX-License-Identifier: GPL-2.0-only
# Portions adapted from ov5647-jetson/tools/isp by
# Copyright (C) 2026 Artur Andrzejczak <andrzejczak.artur@gmail.com>
# Reference/license: ../../public_sources/ov5647-jetson/{tools/isp,LICENSE}
"""Measured IMX708 calibration primitives. RGB vectors are rows; y = x @ CCM.T.

RAW is right-aligned little-endian uint16, NOT packed MIPI RAW10. No automatic
bit shifting, Bayer guessing, histogram WB, or sensor-specific tuning constants.
"""
from pathlib import Path
import hashlib

import numpy as np

# ColorChecker Classic published nominal sRGB reference, not camera tuning.
SRGB8 = np.array([
    (115, 82, 68), (194, 150, 130), (98, 122, 157), (87, 108, 67),
    (133, 128, 177), (103, 189, 170), (214, 126, 44), (80, 91, 166),
    (193, 90, 99), (94, 60, 108), (157, 188, 64), (224, 163, 46),
    (56, 61, 150), (70, 148, 73), (175, 54, 60), (231, 199, 31),
    (187, 86, 149), (8, 133, 161), (243, 243, 242), (200, 200, 200),
    (160, 160, 160), (122, 122, 121), (85, 85, 85), (52, 52, 52)], float)
GRAY_INDICES = [19, 20, 21]  # N8, N6.5, N5; exclude near-white/black.
XYZ_FROM_RGB = np.array([[.4124, .3576, .1805], [.2126, .7152, .0722],
                         [.0193, .1192, .9505]])
PATTERNS = ('RGGB', 'BGGR', 'GRBG', 'GBRG')


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def parse_size(text):
    try:
        w, h = (int(x) for x in text.lower().split('x'))
    except (ValueError, AttributeError):
        raise ValueError('size must be WIDTHxHEIGHT') from None
    if w <= 0 or h <= 0 or w % 2 or h % 2:
        raise ValueError('size must have positive even dimensions')
    return w, h


def split_bayer(mosaic, bayer='RGGB'):
    """Return half-resolution R, Gr, Gb, B planes without subtracting black.

    Gr is the green in the red row; Gb is the green in the blue row.
    Bayer describes the stored top-left 2x2 cell, including any sensor flips.
    """
    a = np.asarray(mosaic, dtype=float)
    if bayer not in PATTERNS:
        raise ValueError('Bayer must be one of ' + ', '.join(PATTERNS))
    if a.ndim != 2 or min(a.shape) <= 0 or any(n % 2 for n in a.shape):
        raise ValueError('mosaic must be a nonempty even-sized 2D array')
    tile = np.array(list(bayer)).reshape(2, 2)
    ry, rx = np.argwhere(tile == 'R')[0]
    by, bx = np.argwhere(tile == 'B')[0]
    return np.stack([a[ry::2, rx::2], a[ry::2, (1 - rx)::2],
                     a[by::2, (1 - bx)::2], a[by::2, bx::2]], axis=-1)


def read_raw(path, width, height, black=64., bayer='RGGB', stride=None,
             bit_depth=10, frame=0, return_planes=False):
    """Load one complete frame as black-subtracted R,(Gr+Gb)/2,B DN.

    Stride is bytes per row including padding. Multiple frames are accepted only
    if the entire file is an exact multiple of stride*height. Select with frame;
    averaging is deliberately left to callers. Negative DN are retained.
    """
    if (not isinstance(width, int) or not isinstance(height, int) or
            min(width, height) <= 0 or width % 2 or height % 2):
        raise ValueError('RAW width and height must be positive even integers')
    if not isinstance(bit_depth, int) or not 1 <= bit_depth <= 16:
        raise ValueError('bit depth must be 1..16')
    maximum = (1 << bit_depth) - 1
    if not np.isfinite(black) or not 0 <= black < maximum:
        raise ValueError('black must be finite and below sensor full scale')
    stride = width * 2 if stride is None else stride
    if not isinstance(stride, int) or stride < width * 2 or stride % 2:
        raise ValueError('stride must be even and at least width*2 bytes')
    size = Path(path).stat().st_size
    frame_bytes = stride * height
    if size == 0 or size % frame_bytes:
        raise ValueError('empty or truncated RAW: file size must be a multiple of stride*height')
    if not isinstance(frame, int) or not 0 <= frame < size // frame_bytes:
        raise ValueError('frame index outside RAW capture')
    with open(path, 'rb') as stream:
        stream.seek(frame * frame_bytes)
        a = np.frombuffer(stream.read(frame_bytes), dtype='<u2').reshape(height, stride // 2)
    a = a[:, :width]  # Padding is not image data and may contain arbitrary bits.
    if np.any(a > maximum):
        raise ValueError('samples exceed bit depth: packed/left-aligned RAW is unsupported')
    planes = split_bayer(a, bayer) - black
    if return_planes:
        return planes
    return np.stack([planes[..., 0], (planes[..., 1] + planes[..., 2]) / 2,
                     planes[..., 3]], axis=-1)


def srgb_to_linear(rgb):
    """sRGB [0,1] to linear RGB (no exposure normalization)."""
    x = np.asarray(rgb, float)
    return np.where(x <= .04045, x / 12.92, ((np.maximum(x, 0) + .055) / 1.055) ** 2.4)


REFERENCE_LINEAR = srgb_to_linear(SRGB8 / 255.)


def lab(rgb):
    xyz = (np.asarray(rgb, float) @ XYZ_FROM_RGB.T) / (XYZ_FROM_RGB @ np.ones(3))
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], axis=-1)


def parse_corners(text):
    try:
        a = np.array([[float(v) for v in p.split(',')] for p in text.split(';')])
    except ValueError:
        raise ValueError('corners must be x,y;x,y;x,y;x,y (TL,TR,BL,BR patch centres)') from None
    if a.shape != (4, 2) or not np.isfinite(a).all():
        raise ValueError('need four finite corner patch centres')
    # Nondegenerate convex quadrilateral in perimeter order.
    perimeter = a[[0, 1, 3, 2]]
    edges = np.roll(perimeter, -1, axis=0) - perimeter
    cross = edges[:, 0] * np.roll(edges[:, 1], -1) - edges[:, 1] * np.roll(edges[:, 0], -1)
    if not (np.all(cross > 0) or np.all(cross < 0)):
        raise ValueError('corner patch centres must form a convex quadrilateral')
    return a


def patch_centres(corners):
    """Bilinear 6x4 grid; use a near-frontoparallel, flat chart."""
    tl, tr, bl, br = np.asarray(corners, float)
    return np.array([(1-u)*(1-v)*tl + u*(1-v)*tr + (1-u)*v*bl + u*v*br
                     for v in np.linspace(0, 1, 4) for u in np.linspace(0, 1, 6)])


def patch_boxes(image, centres, half):
    if not isinstance(half, int) or half < 1:
        raise ValueError('patch half size must be a positive integer')
    boxes = []
    for x, y in np.asarray(centres):
        if not np.isfinite([x, y]).all():
            raise ValueError('nonfinite patch centre')
        x, y = int(round(float(x))), int(round(float(y)))
        if x-half < 0 or y-half < 0 or x+half > image.shape[1] or y+half > image.shape[0]:
            raise ValueError('patch ROI outside image; check coordinates and half size')
        boxes.append(image[y-half:y+half, x-half:x+half])
    if not boxes:
        raise ValueError('no patch ROIs')
    return boxes


def sample_patches(image, centres, half):
    return np.array([np.median(b.reshape(-1, b.shape[-1]), axis=0)
                     for b in patch_boxes(image, centres, half)])


def measure_wb(gray_patches):
    a = np.asarray(gray_patches, float)
    if a.ndim != 2 or a.shape[1] != 3 or not len(a) or not np.isfinite(a).all() or np.any(a <= 0):
        raise ValueError('gray patches must contain positive finite RGB measurements')
    total = a.sum(axis=0)
    return total[[0, 2]] / total[1], total[1] / total


def fit_ccm(camera, target, ridge=0.):
    """Constrained linear least squares: target = camera @ M.T, M @ 1 = 1.

    Fit each row's first two coefficients; third is 1 - first - second.
    Ridge penalizes ||M-I||^2, without releasing the neutral constraint.
    """
    x, y = np.asarray(camera, float), np.asarray(target, float)
    if (x.ndim != 2 or x.shape[1] != 3 or x.shape != y.shape or len(x) < 3 or
            not np.isfinite(x).all() or not np.isfinite(y).all()):
        raise ValueError('CCM needs matching finite Nx3 camera/reference arrays')
    if not np.isfinite(ridge) or ridge < 0:
        raise ValueError('ridge must be nonnegative')
    design = x[:, :2] - x[:, 2:3]
    if np.linalg.matrix_rank(design) < 2:
        raise ValueError('chart has insufficient independent colours to fit a CCM')
    transform = np.array([[1, 0], [0, 1], [-1, -1]], float)
    base = np.array([0, 0, 1.])
    rows = []
    for channel in range(3):
        a = np.vstack([design, np.sqrt(ridge) * transform])
        b = np.concatenate([y[:, channel] - x[:, 2],
                            np.sqrt(ridge) * (np.eye(3)[channel] - base)])
        p = np.linalg.lstsq(a, b, rcond=None)[0]
        rows.append([p[0], p[1], 1 - p.sum()])
    return np.array(rows)


def score_chart(linear_rgb):
    """Exposure-normalized CIE76, nominal D65 reference; no WB in scoring."""
    x = np.asarray(linear_rgb, float)
    if x.shape != (24, 3) or not np.isfinite(x).all() or x[20, 1] <= 0:
        raise ValueError('score needs 24 finite RGB patches and a positive N6.5 green')
    scale = REFERENCE_LINEAR[20, 1] / x[20, 1]
    corrected = x * scale
    actual, reference = lab(corrected), lab(REFERENCE_LINEAR)
    de = np.linalg.norm(actual - reference, axis=1)
    gray = actual[19:23]
    return {'colour_delta_e76_mean': float(de[:18].mean()),
            'gray_delta_e76_mean': float(de[18:].mean()),
            'gray_a_mean': float(gray[:, 1].mean()), 'gray_b_mean': float(gray[:, 2].mean()),
            'gray_chroma_mean': float(np.linalg.norm(gray[:, 1:], axis=1).mean()),
            'exposure_scale': float(scale), 'patch_delta_e76': de.tolist()}


def generate_override(config):
    """Emit measured FusionLights and transposed NVIDIA colour matrices only.

    Eight AWB slots are distributed across measured sources (repeated if fewer
    than eight). This is a limited candidate, not an inferred all-light tuning.
    """
    sources = config.get('sources', [])
    if config.get('schema_version') != 1 or config.get('sensor') != 'IMX708' or not 1 <= len(sources) <= 8:
        raise ValueError('need schema_version=1, sensor=IMX708 and 1..8 measured sources')
    sources = sorted(sources, key=lambda s: s['cct'])
    ccts, validated = set(), []
    for source in sources:
        white, matrix = np.asarray(source['white'], float), np.asarray(source['ccm'], float)
        capture = source.get('capture', {})
        if (capture.get('reference_kind') not in ('reflective', 'display') or
                capture.get('chart') not in ('ColorChecker Classic reflective', 'ColorChecker Classic approximate display') or
                len(capture.get('sha256', '')) != 64):
            raise ValueError('each source needs labelled chart measurement provenance')
        expected_chart = ('ColorChecker Classic approximate display' if capture['reference_kind'] == 'display'
                          else 'ColorChecker Classic reflective')
        if capture['chart'] != expected_chart:
            raise ValueError('chart label and reference_kind disagree')
        cct = source['cct']
        if not np.isfinite(cct) or not 2500 <= cct <= 9000 or cct in ccts:
            raise ValueError('use distinct measured CCT labels in the reference-tested 2500..9000 K range')
        ccts.add(cct)
        if white.shape != (2,) or not np.isfinite(white).all() or np.any(white <= 0):
            raise ValueError('white must be positive finite measured R/G and B/G')
        if (matrix.shape != (3, 3) or not np.isfinite(matrix).all() or
                not np.allclose(matrix.sum(axis=1), 1, atol=1e-6, rtol=0) or np.max(np.abs(matrix)) >= 4):
            raise ValueError('CCM must be finite 3x3, neutral preserving and coefficients below 4 in magnitude')
        vector = np.array([white[0], 1, 1, white[1]])
        fusion = np.rint(vector * (900 / vector.max())).astype(int)
        if np.any(fusion <= 0):
            raise ValueError('white ratios too extreme for FusionLights integer representation')
        validated.append((source, matrix, fusion))
    out = ['# IMX708 measured candidate; limited to supplied chart illuminants.',
           '# Adapted from Artur Andrzejczak ov5647-jetson/tools/isp (GPL-2.0).',
           '# No LSC, black-level ISP policy, saturation boost or unmeasured whites.']
    if any(s['capture'].get('reference_kind') == 'display' for s in sources):
        out.insert(0, '# DISPLAY EXPERIMENT ONLY: not certified reflective or general-purpose ISP calibration.')
        out.insert(1, '# Valid only as an experiment with the measured display/profile/brightness/setup.')
    for slot in range(8):
        source, _, fusion = validated[slot * len(validated) // 8]
        out.append('# Measurement SHA256: ' + source['capture']['sha256'])
        out.append('awb.v4.FusionLights[%d] = {%s};' % (slot, ','.join(map(str, fusion))))
    out.append('awb.v4.FusionInitLight = 0;')
    out.append('colorCorrection.numColorCorrectionMatrices = %d;' % len(validated))
    for i, (source, matrix, _) in enumerate(validated):
        out.append('colorCorrection.set[%d].cct = %g;' % (i, source['cct']))
        for row, values in enumerate(matrix.T):
            out.append('colorCorrection.set[%d].ccMatrix[%d] = { %s, 0.0 };' %
                       (i, row, ', '.join(format(v, '.9g') for v in values)))
        out.append('colorCorrection.set[%d].ccMatrix[3] = { 0.0, 0.0, 0.0, 1.0 };' % i)
    return '\n'.join(out) + '\n'
