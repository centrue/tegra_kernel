#!/usr/bin/env bash
# Source-level contract for the Raspberry Pi non-HDR modes and DW9817 lens.

set -Eeuo pipefail

KERNEL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE_TABLE="$KERNEL_ROOT/kernel/nvidia/drivers/media/i2c/imx708_mode_tbls.h"
SENSOR="$KERNEL_ROOT/kernel/nvidia/drivers/media/i2c/imx708.c"
FOCUSER="$KERNEL_ROOT/kernel/nvidia/drivers/media/i2c/dw9817.c"

expect_fixed() {
	local file="$1" text="$2"
	grep -F -- "$text" "$file" >/dev/null || {
		echo "missing source contract in ${file#$KERNEL_ROOT/}: $text" >&2
		exit 1
	}
}

expect_fixed "$MODE_TABLE" 'imx708_mode_4608x2592_14fps[]'
expect_fixed "$MODE_TABLE" 'imx708_mode_2304x1296_56fps[]'
expect_fixed "$MODE_TABLE" 'imx708_mode_1536x864_120fps[]'
expect_fixed "$MODE_TABLE" 'IMX708_MODE_2304X1296_56FPS'
expect_fixed "$MODE_TABLE" 'IMX708_MODE_1536X864_120FPS'
expect_fixed "$MODE_TABLE" '{{2304, 1296}, imx708_56fps'
expect_fixed "$MODE_TABLE" '{{1536, 864}, imx708_120fps'

# Raspberry Pi 2x2 binning and cropped 2x2 mode selectors.
expect_fixed "$MODE_TABLE" '{0x0900, 0x01}'
expect_fixed "$MODE_TABLE" '{0x0901, 0x22}'
# The 1536x864 mode uses Raspberry Pi's centered 3072x1728 analog crop.
expect_fixed "$MODE_TABLE" '{0x0344, 0x03}'
expect_fixed "$MODE_TABLE" '{0x0346, 0x01}'
expect_fixed "$MODE_TABLE" '{0x0348, 0x0e}'
expect_fixed "$MODE_TABLE" '{0x034a, 0x08}'
expect_fixed "$MODE_TABLE" '{0x040c, 0x06}'
expect_fixed "$MODE_TABLE" '{0x040e, 0x03}'

expect_fixed "$SENSOR" 'IMX708_MODE_2304X1296_56FPS'
expect_fixed "$SENSOR" 'IMX708_MODE_1536X864_120FPS'
expect_fixed "$SENSOR" 'imx708_default_frame_length[s_data->mode]'

expect_fixed "$FOCUSER" '#define DW9817_IDLE_POSITION 512'
expect_fixed "$FOCUSER" '#define DW9817_DEFAULT_POSITION 480'
expect_fixed "$FOCUSER" '#define DW9817_RAMP_STEP 16'
expect_fixed "$FOCUSER" 'V4L2_CID_FOCUS_ABSOLUTE'
expect_fixed "$FOCUSER" '.compatible = "dongwoon,dw9817-vcm"'
expect_fixed "$FOCUSER" 'dw9817_ramp'
expect_fixed "$FOCUSER" 'DW9817_CONTROL_STANDBY'

echo "IMX708 modes and DW9817 source contract: PASS"
