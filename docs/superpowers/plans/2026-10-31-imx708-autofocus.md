# IMX708 continuous autofocus implementation plan

Approved in conversation on 2026-10-31. Keep the current ISP SHA256
224dea93b8cf292fc56dd98fe8475f3fb012b96423a10b745abb72f5f9109cf9.

1. Add tested, reusable luma scoring and an event-driven continuous contrast AF
   controller. Default central 50% ROI, range352..928, coarse32/fine4; initial
   full scan, subsequent local +/-64 coarse16/fine +/-16 then full fallback.
   Discard old frames; settle150ms plus2 frames, median3 samples; verify DAC.
   Require bracketed10% prominent peak and90% confirmation. Reject motion,
   darkness, clipping and insufficient texture; preserve last reliable focus.
2. Integrate one GI GStreamer Argus pipeline, independent leaky X11/appsink
   branches, latest-frame mailbox, single lens owner and cancellable commands.
   Preserve legacy commands; add af continuous/once/stop/status and CLI options.
   Restore AE/AWB locks on every search exit; close lens before stream shutdown.
3. Package/deploy camera-tools only, keeping kernel and ISP unchanged. Log
   hardware positions, scores, timestamps and transitions; collect local images.
4. Verify unit cases, test-pattern pipeline, actual focus/retention/cancellation,
   near/far targets when physically available, 60s stationary and10min preview.
   Initial deadline25s, successful local12s, local+full fallback40s. Compare AF
   with independent dense scans, target>=90%; retain evidence and limitations.

Commit every verified logical modification directly in the existing workspace.
Do not touch unrelated dirty files. No kernel build expected.
