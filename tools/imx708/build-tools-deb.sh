#!/usr/bin/env bash
# Package capture tools alongside the custom kernel without applying ISP tuning.
set -Eeuo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
output=${1:-"$root/out/ov5647-builtin/deb"}
mkdir -p "$output"
output=$(realpath "$output")
version="0.2.0+g$(git -C "$root" rev-parse --short HEAD)"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/DEBIAN" "$stage/opt/imx708-tools" "$stage/usr/share/doc/imx708-camera-tools"
for file in "$root"/tools/imx708/*.py "$root"/tools/imx708/check-lens.sh "$root"/tools/imx708/capture-isp.sh; do
    install -m 0755 "$file" "$stage/opt/imx708-tools/"
done
install -m 0644 "$root/isp/imx708/README.md" "$stage/usr/share/doc/imx708-camera-tools/README.md"
if [[ -f "$root/isp/imx708/COPYING" ]]; then
    install -m 0644 "$root/isp/imx708/COPYING" "$stage/usr/share/doc/imx708-camera-tools/copyright"
fi
cat > "$stage/DEBIAN/control" <<EOF
Package: imx708-camera-tools
Version: $version
Architecture: all
Maintainer: Jetson camera workspace
Depends: python3, v4l-utils, gstreamer1.0-tools
Section: video
Priority: optional
Description: IMX708 manual focus, RAW diagnostics and measured ISP tools
 Capture programs use the Python standard library. Fit/score are workstation
 tools requiring NumPy/Pillow. No ISP override is installed automatically.
EOF
git -C "$root" rev-parse HEAD > "$stage/opt/imx708-tools/source-revision.txt"
dpkg-deb --root-owner-group --build "$stage" "$output/imx708-camera-tools_${version}_all.deb"
