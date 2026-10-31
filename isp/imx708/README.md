# IMX708 measured ISP candidates

These tools implement Task 3 of the colour/focus plan. They contain **no
calibrated IMX708 payload**. A real input capture and explicit reference kind
are required to produce a measurement JSON or ISP candidate. Independent ISP
captures and validation under other illuminants remain necessary before release.

The workflow and colour conversions are adapted from
[`public_sources/ov5647-jetson/tools/isp/`](../../public_sources/ov5647-jetson/tools/isp/),
Copyright (C) 2026 Artur Andrzejczak <andrzejczak.artur@gmail.com>, GPL-2.0.
The adapted files retain attribution and SPDX identifiers; the complete license
is [`public_sources/ov5647-jetson/LICENSE`](../../public_sources/ov5647-jetson/LICENSE).
No OV5647 white points, colour matrices, black16, BGGR assumption, LSC, or
saturation adjustments are used. The nominal ColorChecker sRGB reference values
are chart references, not sensor tuning.

## Dependencies and tests

Fit/score on a workstation with Python 3.8+ and an isolated environment:

```sh
python3 -m venv /tmp/imx708-isp-venv
/tmp/imx708-isp-venv/bin/python -m pip install numpy Pillow
PYTHONDONTWRITEBYTECODE=1 /tmp/imx708-isp-venv/bin/python -B -m unittest discover \
  -s tests -p test_imx708_isp_tools.py -v
bash -n tools/imx708/capture-isp.sh
```

NumPy is the only fitting dependency. Pillow is required for scoring PNG/JPEG.
SciPy is not required. The board capture wrapper uses Bash, Python 3 stdlib,
coreutils, flock, systemd/journalctl and the existing NVIDIA GStreamer elements.
Tests use temporary fake service/GStreamer executables and never access cameras.

## RAW input and fitting

`isp_tools.read_raw(path, width, height, black=64, bayer='RGGB', stride=None,
bit_depth=10, frame=0)` returns an `(H/2,W/2,3)` array of black-subtracted
R, mean(Gr,Gb), B in sensor DN. `split_bayer` exposes R, Gr, Gb, B separately.

Storage is **right-aligned little-endian 16-bit samples containing RAW10**;
packed MIPI RAW10 and left-aligned samples are rejected, not guessed. Stride is
in **bytes per stored row**, default `width*2`. Width/height must be even. The
whole file must contain an exact number of complete frames; `--frame` explicitly
selects one, with no silent averaging or discarded trailing bytes. Padding bytes
are ignored. RGGB, BGGR, GRBG and GBRG describe the actual stored top-left cell;
there is no automatic pattern detection. A wrong but valid Bayer pattern is
not intrinsically detectable: verify it with controlled channel tests. IMX708
RGGB/black64 defaults are starting assumptions; measure black under the actual
mode/gain/exposure and pass it explicitly.

For a **reflective** ColorChecker Classic under a controlled, measured light:

```sh
/tmp/imx708-isp-venv/bin/python tools/imx708/calibrate.py fit \
  --raw chart.raw --reference-kind reflective --size 2304x1296 \
  --bayer RGGB --stride 4608 --black 64 \
  --corners '400,250;1800,250;400,1000;1800,1000' --half 10 \
  --illuminant 'measured laboratory source' --cct 5000 \
  --exposure-ns 10000000 --gain 2 --focus 688 \
  --out /tmp/fit-source.json --override /tmp/source-candidate.isp
```

Numbers/coordinates in commands are examples, not calibration data. Exposure,
gain and focus are provenance supplied by the caller, not hardware readbacks.
Use the main control tools to hold the verified lens position during capture.
Missing provenance is stored as null. The command, input SHA256, selected frame,
size, storage, Bayer, depth, stride, black, illuminant, CCT, ROI geometry,
measured white R/G and B/G, WB gains, CCM and scores are retained in JSON.

Corner coordinates are **centres of corner patches**: dark skin, bluish green,
white and black (TL,TR,BL,BR). All fit coordinates are full sensor pixels; `half`
is in Bayer quads, so 10 samples a 40x40 sensor-pixel box. Use only the chart's
6x4 grid, excluding added focus/grayscale decorations. Position the flat chart
near frontoparallel: interpolation is bilinear, not perspective rectification.
ROIs outside the image fail rather than being clipped silently. Fit rejects
near-clipped ROI pixels (default 98% of black-subtracted full scale), dark patch
channels (median <=1 DN), and charts lacking independent colours. Thresholds
are explicit options. It cannot automatically detect motion or display flicker.

WB uses measured N8/N6.5/N5 gray patches, not the scene histogram. Their green
exposure is normalized to the nominal reference. A constrained linear
least-squares fit uses the 18 colour patches in **linear RGB**, with rows summing
to one to preserve neutrals. Optional `--ridge` penalizes distance from identity.
It minimizes linear RGB error, not perceptual delta E; the JSON reports
exposure-normalized CIE76 before/after fitting as a diagnostic.

The mathematical convention is `linear_srgb = camera_wb @ ccm.T`. The generator
**transposes** this matrix for NVIDIA's input-channel `ccMatrix` rows. It writes
only measured FusionLights, FusionInitLight and colour-matrix keys, with no LSC,
black-level ISP policy, AWB clamps or saturation boosts. Eight FusionLights
slots repeat available measured sources; this does not assert coverage of eight
illuminants. Matrix labels must be distinct within the reference-tested
2500–9000 K range, and coefficient magnitudes below 4. These are conservative
reference-derived parser checks, not Nano compatibility certification.

Combine measured sources with distinct CCT labels:

```sh
/tmp/imx708-isp-venv/bin/python tools/imx708/calibrate.py generate \
  /tmp/fit-source.json /tmp/fit-other-source.json --out /tmp/multi-candidate.isp
```

## Smartphone/display experiments

Always pass `--reference-kind display` for a chart displayed on a smartphone or
monitor, both to `fit` and to `score`. It is an emissive approximation of the
nominal sRGB patches, **not a certified reflected ColorChecker**. Lock display
brightness/profile, disable adaptive brightness/colour/white-point features,
avoid clipping and PWM/refresh banding, and record the setup in `--illuminant`.
A CCT label is user supplied, not inferred from the screen RGB values.

Display measurement JSON carries `reference_kind=display`, an experimental
status and warnings. Any generated ISP containing display data starts with
`DISPLAY EXPERIMENT ONLY`, including when generated later from saved JSON.
The capture wrapper propagates this label from the ISP header, or accepts
`--reference-kind display`. Such fits may diagnose channel order and a particular
screen/setup, but cannot establish reflected-object accuracy, cross-illuminant
colour calibration, or a general-purpose production ISP. No chart image assets
are generated by these tools.

## Reversible ISP capture on the board

Use a fresh output directory for every capture. Mode/size/fps must match the
sensor mode implemented on the target. Defaults follow the working Nano baseline
(mode 1, 2304x1296 at 30 fps); the script does not guess other mode sizes.

```sh
sudo tools/imx708/capture-isp.sh none /tmp/generic-run \
  --size 2304x1296 --mode 1 --exposure-ns 10000000 --gain 2 \
  --focus 688 --illuminant 'same controlled source' --reference-kind reflective
sudo tools/imx708/capture-isp.sh /tmp/source-candidate.isp /tmp/candidate-run \
  --size 2304x1296 --mode 1 --exposure-ns 10000000 --gain 2 \
  --focus 688 --illuminant 'same controlled source' --reference-kind reflective
```

`none` temporarily removes the override; `keep` leaves it in place. A settings
lock excludes other instances of this script. When changing the override, it
backs up the original with `cp -a`, retaining mode, owner, timestamps and symlink
identity, clears `nvcam_cache_*.bin` and restarts nvargus-daemon. EXIT/INT/TERM/HUP
traps restore the original (or original absence), clear candidate cache and
restart again, including on parser/capture/verification failures. Restoration
errors make the run fail and preserve the backup for manual recovery.
SIGKILL, power loss and unrelated tools bypassing the lock cannot be recovered
by a shell trap; keep the output backup available.

When the override bytes/state do not change, restart/cache-clear is skipped;
`--restart` explicitly requests it. This includes `none` when already absent and
a byte-identical candidate. `keep` can capture without a fresh load event, but
records `override_load_verified=false` in that case. A candidate requires a fresh,
positive journal event naming the exact full override path; a missing/default
file message, a load attempt, or an old success does not pass. Targets that do
not emit positive load evidence fail verification rather than claiming success;
review their actual journal format before adapting the matcher.

The pipeline streams 50 frames by default as **NV12 → nvvidconv → I420**, retaining
only the last frame. There is no nvjpegenc dependency. A finite timeout and exact
I420 byte-count check reject stalled/truncated captures. `--frames` is explicit;
50 frames are a settling allowance, not a convergence guarantee. Repeated
`--argus-property 'awblock=true'` etc. pass properties as arguments without eval.

Outputs include `last.i420`, its SHA256, command/invocation text, full GStreamer
and journal logs, service/restore logs, override SHA256, original-file attributes,
requested controls and exit codes in `metadata.json`. The wrapper records focus
only and does not drive the lens. Its raw context options are provenance only:
Argus I420 is already processed data. Review LSC/module warnings in journal.log;
log retention does not mean these warnings have been resolved.
`IMX708_SETTINGS_DIR` selects an alternate settings directory for isolated tests.

## ISP comparison and limits

Decode I420 to PNG with an explicit YUV matrix/range matching the negotiated
GStreamer colourimetry, and preserve the conversion command. `score.py` deliberately
does not guess these properties. It assumes decoded sRGB, ignores embedded colour
profiles and normalizes exposure using N6.5 green; it applies no corrective WB.

```sh
/tmp/imx708-isp-venv/bin/python tools/imx708/score.py \
  --reference-kind reflective --corners '400,250;1800,250;400,1000;1800,1000' \
  --half 10 --out /tmp/comparison.json generic.png candidate.png
```

Scores include CIE76 over 18 colours and six grays, middle-gray a*/b*/chroma,
per-patch errors and ROI clipping fractions. `--scale` scales corner coordinates
only. Optional `--flat-field neutral.png` measures four corner/centre linear-luma
ratios; chart corners alone cannot establish lens shading. Optional
`--noise-pair frame1.png frame2.png` reports centre-crop temporal RGB sigma from
frame differences / sqrt(2), assuming a static scene and matched controls.
Motion, ISP processing and illumination changes invalidate a sensor-noise claim.

Synthetic tests validate the algorithms, parser-gating logic and rollback with
fake services. They do not establish actual Nano parser acceptance, override
loading, CCT interpolation, AE/AWB convergence or colour improvement. Reflective
chart fits also use nominal patch references; absolute characterization needs
measured chart spectra/illumination and independent validation. No permanent
calibrated file is included without measured chart input.
