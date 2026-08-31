#!/usr/bin/env bash
# Verify that IMX708 is built in and shipped through every installable kernel
# bundle produced by build-ov5647.sh.

set -Eeuo pipefail

KERNEL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_SCRIPT="$KERNEL_ROOT/build-ov5647.sh"

expect_fixed() {
	local file="$1" text="$2"
	grep -F -- "$text" "$file" >/dev/null || {
		echo "missing integration contract in ${file#$KERNEL_ROOT/}: $text" >&2
		exit 1
	}
}

expect_fixed "$KERNEL_ROOT/kernel/kernel-4.9/arch/arm64/configs/tegra_defconfig" \
	'CONFIG_VIDEO_IMX708=y'
expect_fixed "$KERNEL_ROOT/kernel/kernel-4.9/arch/arm64/configs/tegra_defconfig" \
	'CONFIG_VIDEO_DW9817=y'
expect_fixed "$KERNEL_ROOT/kernel/nvidia/drivers/media/i2c/Kconfig" \
	'config VIDEO_IMX708'
expect_fixed "$KERNEL_ROOT/kernel/nvidia/drivers/media/i2c/Kconfig" \
	'config VIDEO_DW9817'
expect_fixed "$KERNEL_ROOT/kernel/nvidia/drivers/media/i2c/Makefile" \
	'obj-$(CONFIG_VIDEO_IMX708) += imx708.o'
expect_fixed "$KERNEL_ROOT/kernel/nvidia/drivers/media/i2c/Makefile" \
	'obj-$(CONFIG_VIDEO_DW9817) += dw9817.o'
expect_fixed "$KERNEL_ROOT/hardware/nvidia/platform/t210/porg/kernel-dts/Makefile" \
	'dtbo-$(CONFIG_ARCH_TEGRA_210_SOC) += tegra210-p3448-common-imx708.dtbo'

expect_fixed "$BUILD_SCRIPT" \
	'install -m 0644 "$IMX708_DTBO" "$DTBS_PAYLOAD/boot/tegra210-p3448-common-imx708.dtbo"'
expect_fixed "$BUILD_SCRIPT" \
	'test -f "$DTBS_PAYLOAD/boot/tegra210-p3448-common-imx708.dtbo"'
expect_fixed "$BUILD_SCRIPT" \
	'DEPMOD=true modules_install'
expect_fixed "$BUILD_SCRIPT" \
	': > "$STAGE_DIR/lib/modules/$KERNEL_RELEASE/modules.builtin.modinfo"'
expect_fixed "$BUILD_SCRIPT" \
	'jetson-nano-a02-r32.7.6-imx708-ov5647-vc-mipi-stage2-${DRIVER_MODE}.tar.gz'
expect_fixed "$BUILD_SCRIPT" \
	'tests/test_imx708_overlay.sh'

echo "IMX708 kernel/package integration contract: PASS"
