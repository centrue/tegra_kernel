#!/usr/bin/env bash
# Package capture tools alongside the custom kernel without applying ISP tuning.
set -Eeuo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
output=${1:-"$root/out/ov5647-builtin/deb"}
mkdir -p "$output"
output=$(realpath "$output")
version="0.3.0+g$(git -C "$root" rev-parse --short HEAD)"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/DEBIAN" "$stage/opt/imx708-tools" "$stage/usr/share/doc/imx708-camera-tools"
for file in "$root"/tools/imx708/*.py "$root"/tools/imx708/check-lens.sh "$root"/tools/imx708/capture-isp.sh; do
    install -m 0755 "$file" "$stage/opt/imx708-tools/"
done
install -m 0644 "$root/isp/imx708/README.md" "$stage/usr/share/doc/imx708-camera-tools/README.md"
if [[ -d "$root/isp/imx708/experiments" ]]; then
    mkdir -p "$stage/usr/share/imx708/isp/experiments"
    for file in "$root"/isp/imx708/experiments/*.isp "$root"/isp/imx708/experiments/*.json; do
        [[ -f $file ]] || continue
        install -m 0644 "$file" "$stage/usr/share/imx708/isp/experiments/"
    done
fi
if [[ -f "$root/isp/imx708/COPYING" ]]; then
    install -m 0644 "$root/isp/imx708/COPYING" "$stage/usr/share/doc/imx708-camera-tools/copyright"
fi
cat > "$stage/DEBIAN/control" <<EOF
Package: imx708-camera-tools
Version: $version
Architecture: all
Maintainer: Jetson camera workspace
Depends: python3, python3-gi, python3-gst-1.0, gir1.2-gstreamer-1.0, gir1.2-gst-plugins-base-1.0, v4l-utils, gstreamer1.0-tools, gstreamer1.0-x
Section: video
Priority: optional
Description: IMX708 continuous autofocus, RAW diagnostics and measured ISP tools
 Live preview uses Python GStreamer bindings; RAW tools use the standard library.
 Fit/score are workstation
 tools requiring NumPy/Pillow. No ISP override is installed automatically.
EOF
git -C "$root" rev-parse HEAD > "$stage/opt/imx708-tools/source-revision.txt"
dpkg-deb --root-owner-group --build "$stage" "$output/imx708-camera-tools_${version}_all.deb"
