# AF execution ledger

Plan: docs/superpowers/plans/2026-10-31-imx708-autofocus.md

- Baseline: 90381d29c. User approved continuous AF and preserving current ISP.
- Remote: GStreamer1.20.3; Gst/GstApp/GstVideo GI available, python3-gst-1.0
  absent. appsink and writable aelock/awblock confirmed. No live preview found.
- Ruling: use existing checkout per AGENTS.md; retain unrelated dirty changes.
- Pre-flight: controller emits lens actions, runtime is sole writer; cancellation
  must be checked before every action. Scoring uses I420 Y, never RAW black64.
- Task1: controller/scorer added; RED7 failures (module absent), GREEN33 full
  Python tests. Stride/normalization, peak bracketing, stale frames, cancellation,
  stationary60s, scene-change and timeout covered. Motion evaluated every frame
  before5Hz decisions to avoid periodic-motion aliasing.
- Task2: latest-frame mailbox, sole lens-owner worker and GI preview integrated;
  RED4 missing-runtime tests and RED1 missing-socket-handler test observed;
  GREEN38 full Python tests. Legacy socket behavior preserved. New package0.3
  declares GI/GStreamer dependencies; remote I2C node is root-only, so deployment
  uses sudo with explicit forwarded XAUTHORITY, without changing device modes.
- Checkpoint v0.3: code committed through5df191e0c.47 Python tests pass using
  tmp/colour-focus/venv; C gain and three IMX708 static contracts pass. Fresh
  reviewer found6 issues, all reproduced/fixed and follow-up21 AF tests pass.
- Hardware: test-pattern display/analysis90 frames each, stride324/width322;
  clean EOS and DAC readbacks verified. Full-ROI scorer improved188ms to36ms.
  One actual lock620 in21.3s occurred, but this is NOT AF acceptance: repeated
  scans miss/abort, near phone/stationary comparison not completed,10min stability
  not claimed. GUI/manual tuning is now requested; old AF remains experimental.
- Evidence: tmp/colour-focus/autofocus-v1..v5 and autofocus/*.log. v5 uses fixed
  exposure10ms/gain2 and left text ROI(.01,.25,.22,.35). Its drift guard aborted
  on Ymean24.59->26.34 because black-subtracted signal was only8.59; this guard
  is too sensitive near black. User reports slow/missed best focus.
- User steering: preserve this checkpoint; start version0.4 with manual GUI
  tuning and new AF based on public_sources/libcamera. Current ISP is unchanged.
  v5 preview remains running, AF explicitly switched off; new work must not
  accidentally reactivate the experimental old scanner.
