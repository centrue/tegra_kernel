#!/usr/bin/env bash
# Static contract test for the R32.7.6 SEN0634 integration.
# Run with: bash tegra_kernel/tests/test_ov9281_sen0634_static.sh

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
KERNEL="$ROOT/tegra_kernel"
I2C="$KERNEL/kernel/nvidia/drivers/media/i2c"
DT="$KERNEL/hardware/nvidia/platform/t210/porg/kernel-dts"

need_file() {
	[[ -f "$1" ]] || { echo "missing: $1" >&2; exit 1; }
}

need_text() {
	local pattern="$1" file="$2"
	rg -q --fixed-strings "$pattern" "$file" || {
		echo "missing '$pattern' in $file" >&2
		exit 1
	}
}

need_file "$I2C/ov9281_sen0634.c"
need_file "$I2C/ov9281_sen0634_mode_tbls.h"
need_file "$DT/tegra210-p3448-common-ov9281-sen0634.dts"

need_text 'config VIDEO_OV9281_SEN0634' "$I2C/Kconfig"
need_text 'obj-$(CONFIG_VIDEO_OV9281_SEN0634) += ov9281_sen0634.o' "$I2C/Makefile"
need_text 'CONFIG_VIDEO_OV9281_SEN0634=y' "$KERNEL/kernel/kernel-4.9/arch/arm64/configs/tegra_defconfig"
need_text 'tegra210-p3448-common-ov9281-sen0634.dtbo' "$DT/Makefile"

DRIVER="$I2C/ov9281_sen0634.c"
need_text 'nvidia,ov9281-sen0634' "$DRIVER"
need_text 'tegracam_device_register' "$DRIVER"
need_text 'tegracam_v4l2subdev_register' "$DRIVER"
need_text 'TEGRA_CAMERA_CID_GAIN' "$DRIVER"
need_text 'TEGRA_CAMERA_CID_EXPOSURE' "$DRIVER"
need_text 'TEGRA_CAMERA_CID_FRAME_RATE' "$DRIVER"
need_text 'TEGRA_CAMERA_CID_GROUP_HOLD' "$DRIVER"
need_text 'module_param(test_mode' "$DRIVER"
need_text '0x3031, 0x0c' "$I2C/ov9281_sen0634_mode_tbls.h"
need_text '0x030d, 0x50' "$I2C/ov9281_sen0634_mode_tbls.h"
need_text '0x030e, 0x02' "$I2C/ov9281_sen0634_mode_tbls.h"
need_text '0x380c, 0x05' "$I2C/ov9281_sen0634_mode_tbls.h"
need_text '0x380d, 0xfa' "$I2C/ov9281_sen0634_mode_tbls.h"
need_text '0x380e, 0x07' "$I2C/ov9281_sen0634_mode_tbls.h"
need_text '0x380f, 0x1e' "$I2C/ov9281_sen0634_mode_tbls.h"

OVERLAY="$DT/tegra210-p3448-common-ov9281-sen0634.dts"
need_text 'overlay-name = "Camera OV9281 SEN0634"' "$OVERLAY"
need_text 'compatible = "nvidia,p3449-0000-a02+p3448-0000-a02"' "$OVERLAY"
need_text 'compatible = "nvidia,ov9281-sen0634"' "$OVERLAY"
need_text 'reg = <0x60>' "$OVERLAY"
need_text 'mclk_khz = "24000"' "$OVERLAY"
need_text 'num_lanes = "2"' "$OVERLAY"
need_text 'pixel_phase = "rggb"' "$OVERLAY"
need_text 'pixel_t = "bayer_rggb10"' "$OVERLAY"
need_text 'devname = "ov9281_sen0634 6-0060"' "$OVERLAY"
need_text 'proc-device-tree = "/proc/device-tree/host1x/i2c@546c0000/ov9281_sen0634_a@60"' "$OVERLAY"

if rg -q --line-regexp '[[:space:]]*\(reset\|pwdn\)-gpios[[:space:]]*=' "$OVERLAY"; then
	echo "SEN0634 overlay must not claim PWDN as reset-gpios/pwdn-gpios" >&2
	exit 1
fi

need_text 'Pin 11' "$ROOT/docs/ov9281_pinout.md"
need_text 'Pin 12' "$ROOT/docs/ov9281_pinout.md"
need_text '板载 24 MHz' "$ROOT/docs/ov9281_pinout.md"

need_text 'tegra210-p3448-common-ov9281-sen0634.dtbo' "$KERNEL/build-ov5647.sh"
need_text 'OV9281 SEN0634' "$KERNEL/build-ov5647.sh"

echo "OV9281 SEN0634 static contract: PASS"
