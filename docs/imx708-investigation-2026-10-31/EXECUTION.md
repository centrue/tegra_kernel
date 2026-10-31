# IMX708 execution ledger
Plan: docs/superpowers/plans/2026-10-31-imx708-colour-focus.md
Ruling: Work in place per AGENTS.md; preserve unrelated existing changes.
Ruling: ISP tooling delegated in parallel; kernel/lens critical path stays local.
Baseline: video0 works, lens node absent, ID fix committed 6063c337c.
Task1: lens module compiles; actual DAC readback matches all scanned positions. First scan rejected due dark centre ROI; added explicit ROI after RED/GREEN test.
Task2: reciprocal gain helper boundary/mapping test and IMX708 static tests passed; committed 5fcceef35. Full DEB build running with ccache.
Task1: formal DW9817 node active after kernel #3. Text-target scan best688 versus512 gave 8.7366x gradient score; actual DACs match. Phone target not yet visibly confirmed; centre dark, cannot calibrate.
Task2: kernel #3 and merged DT deployed, Image SHA256 e149efa898d998b584307c7be1019427a2f8f1dc5c47195eb9e3dd8c78773fd9. Live codes114/512/768/896/960 all pass.
Task3: tools committed7260bbb30;22 tests pass in tmp/colour-focus/venv. Phone chart generated e6ed33522 and user placed it; treat as display experiment.
Review: fresh reviewer found P2 focus illumination consistency and pre-averaging green clipping; fixing both. Kernel4.9 FD/unbind lifetime inspection found no P0/P1.
Runtime: SSH became No route to host while uploading capture-focused-isp.py; no new photo from that attempt. Investigating network, not assuming capture success.
Task3 tools:25 Python tests now pass, C gain test and three IMX708 static checks pass. Review fixes committed5cf865adc/4e73d7eca. Kernel unchanged after successful #3 deployment.
Lens lifecycle hardware: multiple opens, closing one FD preserves DAC688; last close standby; reopening reapplies688; unbind with an open FD rejects ioctl with ENODEV, final close and rebind succeed. No kernel crash.
User steering: show live video via local X11 first. X11 forwarding validated. ffplay GLX failed; installed gstreamer1.0-x after correcting Nano's outdated clock. Final renderer is ximagesink with explicit forwarded display; NVIDIA producer EGL uses headless display.
X11 preview verified: local window ID35651586 renamed IMX708 Nano - X11; image960x540, requested15fps, focus688 held throughout, control socket /tmp/imx708-preview.sock. Live SSH session handle59320 is intentionally running. Do not start competing captures while preview owns the camera.
Preview script committed5e04c33ef; imx708-camera-tools_0.2.0+g5e04c33ef_all.deb built and installed, scripts under /opt/imx708-tools/. Kernel and tools DEBs available in out/ov5647-builtin/deb/.
Remaining: phone display experiment ISP calibration. Live view still shows only part of the chart and generic ISP tint; no calibrated override installed. User can align phone using preview before next capture.
