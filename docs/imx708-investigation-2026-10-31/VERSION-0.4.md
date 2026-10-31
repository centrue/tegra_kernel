# Version0.4: manual tuning and libcamera-style CDAF

Previous experimental AF checkpoint:7b9877865; preserve current ISP SHA
224dea93b8cf292fc56dd98fe8475f3fb012b96423a10b745abb72f5f9109cf9.

New controller source:public_sources/libcamera revision
c682979b50695b93526adb1e66db33743484b63b, src/ipa/rpi/controller/rpi/af.cpp
and src/ipa/rpi/vc4/data/imx708.json. BSD notices retained. New Python controller
ports programmed CDAF: direction/reversal,0.75 early termination,3point quadratic
peak, opposite-direction3point fine scan,5fresh-frame settling and0.8/10frame
CAF re-trigger. Focus uses the standard-lens0..15dioptre ->445..925 DAC map.

Adaptation: Nano receives timestamped I420 software contrast, not Pi ISP focus
statistics or PDAF regions. Driver already ramps hardware moves. No PDAF claim.
Keep shared-power/lens-FD sequencing, hardware readback, stale-frame rejection,
scene check at final focus and independent AE/AWB restoration. Remove the old
global black-subtracted mean drift guard: it falsely rejected Y24.59->26.34.
Synthetic tests cover near/far peaks, reversal, interpolation, dark mean drift,
flat scene and changed scene at confirmation; hardware validation is pending.

GUI defaults to manual AF off. Native exposure/gain/ISP gain/WB mode/lock/EV/
saturation adjust live; RGB micro-adjustment, brightness/contrast/gamma are
post-ISP preview/output filters and do not rewrite the ISP calibration. Manual
parameter changes stop AF before changing camera properties. JSON presets
include exact settings, focus, ROI and ISP SHA; snapshots include the filters.
GUI embeds child ximagesink into one GTK3 window via X11 window handle, preserving
the producer's headless NVIDIA EGL environment. New package version0.4.0.
