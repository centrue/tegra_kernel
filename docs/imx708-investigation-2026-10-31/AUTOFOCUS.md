# IMX708 live contrast autofocus

Run on Nano with X11 forwarding (or add --headless):

```
ssh -Y jetson@jetson-nano 'sudo -n env DISPLAY="$DISPLAY" XAUTHORITY="$HOME/.Xauthority" python3 /opt/imx708-tools/preview-x11.py --output /home/jetson/imx708-deploy/autofocus --autofocus continuous --exposure-ns 20000000 --gain 4 --isp-digital-gain 1'
```

One Argus stream feeds the display and latest-frame analysis. Central50% ROI,
DAC352..928, full coarse32/fine4 search followed by local continuous searches.
This is userspace contrast AF for this program, not native Argus/PDAF support.
Current ISP is read for its SHA256 and is never written.

Control socket `/tmp/imx708-preview.sock` is owner-only. Send one command per
connection: `focus N`, `status`, `af continuous`, `af once`, `af stop`,
`af status`, `snapshot`, `stop`. Legacy status remains `focus=N`; AF status is
JSON. Manual focus cancels AF and stays locked. `af stop` holds current focus.
`--autofocus off` is the compatibility default; deployment explicitly enables
continuous. `--af-roi x,y,w,h` uses normalized coordinates, `--af-range min:max`
changes the scan range. Textured, steady targets are required; boundary/flat
peaks remain unconfirmed.688 is only the startup fallback, not universal focus.
The deployment uses sudo because /dev/i2c-6 is root-only; use sudo for socket
clients too. XAUTHORITY passes the SSH user's forwarding cookie to the sink.

`--output DIR` holds autofocus.jsonl (states, requested/actual DAC, timestamps,
scores and rejected samples), commands.json (pipeline/ISP SHA), gst-bus.log,
and search/locked/snapshot images. I420 files are repacked without row padding;
PGM files are unmodified luma. Convert I420 on the workstation with ffmpeg using
the corresponding JSON dimensions and BT.709 limited range.

The sensor must be streaming before lens open; never run another RAW/Argus
capture against the same camera. Stop this program before other camera tools.
AE/AWB are temporarily locked during a search and restored afterwards. On
shutdown the worker cancels movement and closes the lens before stopping Gst.
