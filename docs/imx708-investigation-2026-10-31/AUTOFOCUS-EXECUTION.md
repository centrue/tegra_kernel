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
