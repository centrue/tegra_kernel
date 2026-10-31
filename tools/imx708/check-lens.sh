#!/usr/bin/env bash
set -euo pipefail
for entry in /sys/class/video4linux/v4l-subdev*; do
    [[ -f "$entry/name" ]] || continue
    name=$(cat "$entry/name")
    [[ "$name" == *dw9817* ]] || continue
    node="/dev/${entry##*/}"
    v4l2-ctl -d "$node" --list-ctrls | grep -q focus_absolute
    echo "$node"
    exit 0
done
echo 'DW9817 focus_absolute node is missing' >&2
exit 1
