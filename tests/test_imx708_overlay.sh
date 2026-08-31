#!/usr/bin/env bash
# Compile and apply the Jetson Nano A02 IMX708 Jetson-IO overlay, then
# validate the resulting live-device-tree contract.

set -Eeuo pipefail

KERNEL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_ROOT="$(cd "$KERNEL_ROOT/.." && pwd)"
DT_DIR="$KERNEL_ROOT/hardware/nvidia/platform/t210/porg/kernel-dts"
SOC_INCLUDE="$KERNEL_ROOT/hardware/nvidia/soc/tegra/kernel-include"
OVERLAY="$DT_DIR/tegra210-p3448-common-imx708.dts"
BASE_DTB="$WORKSPACE_ROOT/Linux_for_Tegra/kernel/dtb/tegra210-p3448-0000-p3449-0000-a02.dtb"
CROSS_GCC="$WORKSPACE_ROOT/gcc/bin/aarch64-linux-gnu-gcc"

for command in dtc fdtoverlay fdtget mktemp; do
	command -v "$command" >/dev/null || {
		echo "missing host command: $command" >&2
		exit 1
	}
done
[[ -x "$CROSS_GCC" ]] || { echo "missing cross compiler: $CROSS_GCC" >&2; exit 1; }
[[ -f "$OVERLAY" ]] || { echo "missing IMX708 overlay: $OVERLAY" >&2; exit 1; }
[[ -f "$BASE_DTB" ]] || { echo "missing A02 base DTB: $BASE_DTB" >&2; exit 1; }

TEST_DIR="$(mktemp -d)"
trap 'rm -rf -- "$TEST_DIR"' EXIT
CPP_DTS="$TEST_DIR/imx708.preprocessed.dts"
DTBO="$TEST_DIR/imx708.dtbo"
MERGED_DTB="$TEST_DIR/a02-imx708.dtb"
WARNINGS="$TEST_DIR/dtc-warnings.log"

"$CROSS_GCC" -E -nostdinc -I"$SOC_INCLUDE" -undef -D__DTS__ \
	-x assembler-with-cpp -o "$CPP_DTS" "$OVERLAY"
dtc -@ -I dts -O dtb -b 0 -i "$DT_DIR" -i "$SOC_INCLUDE" \
	-Wno-unit_address_vs_reg -o "$DTBO" "$CPP_DTS" 2>"$WARNINGS"
[[ ! -s "$WARNINGS" ]] || {
	cat "$WARNINGS" >&2
	echo "IMX708 overlay emitted dtc warnings" >&2
	exit 1
}
fdtoverlay -i "$BASE_DTB" -o "$MERGED_DTB" "$DTBO"

expect_string() {
	local blob="$1" path="$2" property="$3" expected="$4"
	local actual
	actual="$(fdtget -t s "$blob" "$path" "$property")"
	[[ "$actual" == "$expected" ]] || {
		echo "$path/$property: expected '$expected', got '$actual'" >&2
		exit 1
	}
}

symbol_path() {
	fdtget -t s "$MERGED_DTB" /__symbols__ "$1"
}

expect_status() {
	local symbol="$1" expected="$2"
	expect_string "$MERGED_DTB" "$(symbol_path "$symbol")" status "$expected"
}

expect_string "$DTBO" / overlay-name "Camera IMX708"
expect_string "$DTBO" / jetson-header-name "Jetson Nano CSI Connector"
expect_string "$DTBO" / compatible "nvidia,p3449-0000-a02+p3448-0000-a02"

expect_status imx708_single_cam0 okay
expect_status imx219_single_cam0 disabled
expect_status imx477_single_cam0 disabled
expect_status imx708_dw9817 okay
expect_status cam_module0_drivernode1 okay

SENSOR="$(symbol_path imx708_single_cam0)"
expect_string "$MERGED_DTB" "$SENSOR" compatible "sony,imx708"
[[ "$(fdtget -t x "$MERGED_DTB" "$SENSOR" reg)" == 1a ]] || {
	echo "IMX708 sensor address is not 0x1a" >&2
	exit 1
}
expect_string "$MERGED_DTB" "$SENSOR" sensor_model imx708

VCM="$(symbol_path imx708_dw9817)"
expect_string "$MERGED_DTB" "$VCM" compatible "dongwoon,dw9817-vcm"
[[ "$(fdtget -t x "$MERGED_DTB" "$VCM" reg)" == c ]] || {
	echo "DW9817 actuator address is not 0x0c" >&2
	exit 1
}
[[ "$(fdtget -t x "$MERGED_DTB" "$SENSOR" lens-focus)" == \
	"$(fdtget -t x "$MERGED_DTB" "$VCM" phandle)" ]] || {
	echo "IMX708 lens-focus does not reference DW9817" >&2
	exit 1
}

check_mode() {
	local name="$1" width="$2" height="$3" line_length="$4"
	local pixel_clock="$5" max_fps="$6" default_fps="$7"
	local mode="$SENSOR/$name"
	expect_string "$MERGED_DTB" "$mode" mclk_khz 24000
	expect_string "$MERGED_DTB" "$mode" num_lanes 2
	expect_string "$MERGED_DTB" "$mode" tegra_sinterface serial_a
	expect_string "$MERGED_DTB" "$mode" active_w "$width"
	expect_string "$MERGED_DTB" "$mode" active_h "$height"
	expect_string "$MERGED_DTB" "$mode" line_length "$line_length"
	expect_string "$MERGED_DTB" "$mode" pix_clk_hz "$pixel_clock"
	expect_string "$MERGED_DTB" "$mode" pixel_phase rggb
	expect_string "$MERGED_DTB" "$mode" pixel_t bayer_rggb10
	expect_string "$MERGED_DTB" "$mode" max_framerate "$max_fps"
	expect_string "$MERGED_DTB" "$mode" default_framerate "$default_fps"
	expect_string "$MERGED_DTB" "$mode" embedded_metadata_height 2
}

check_mode mode0 4608 2592 15648 595200000 14000000 14000000
check_mode mode1 2304 1296 7824 585600000 56000000 30000000
check_mode mode2 1536 864 5216 566400000 120000000 30000000

SENSOR_OUT="$(symbol_path imx708_out0)"
CSI_IN="$(symbol_path rbpcv2_imx219_csi_in0)"
CSI_OUT="$(symbol_path rbpcv2_imx219_csi_out0)"
VI_IN="$(symbol_path rbpcv2_imx219_vi_in0)"

[[ "$(fdtget -t x "$MERGED_DTB" "$SENSOR_OUT" remote-endpoint)" == \
	"$(fdtget -t x "$MERGED_DTB" "$CSI_IN" phandle)" ]] || {
	echo "IMX708-to-NVCSI endpoint is not reciprocal" >&2
	exit 1
}
[[ "$(fdtget -t x "$MERGED_DTB" "$CSI_IN" remote-endpoint)" == \
	"$(fdtget -t x "$MERGED_DTB" "$SENSOR_OUT" phandle)" ]] || {
	echo "NVCSI-to-IMX708 endpoint is not reciprocal" >&2
	exit 1
}
[[ "$(fdtget -t x "$MERGED_DTB" "$CSI_OUT" remote-endpoint)" == \
	"$(fdtget -t x "$MERGED_DTB" "$VI_IN" phandle)" ]] || {
	echo "NVCSI-to-VI endpoint is not reciprocal" >&2
	exit 1
}
[[ "$(fdtget -t x "$MERGED_DTB" "$VI_IN" remote-endpoint)" == \
	"$(fdtget -t x "$MERGED_DTB" "$CSI_OUT" phandle)" ]] || {
	echo "VI-to-NVCSI endpoint is not reciprocal" >&2
	exit 1
}

DRIVERNODE="$(symbol_path cam_module0_drivernode0)"
expect_string "$MERGED_DTB" "$DRIVERNODE" devname "imx708 6-001a"
expect_string "$MERGED_DTB" "$DRIVERNODE" proc-device-tree \
	"/proc/device-tree/host1x/i2c@546c0000/imx708_a@1a"

LENS_DRIVERNODE="$(symbol_path cam_module0_drivernode1)"
expect_string "$MERGED_DTB" "$LENS_DRIVERNODE" pcl_id "v4l2_lens"
expect_string "$MERGED_DTB" "$LENS_DRIVERNODE" devname "dw9817 6-000c"
expect_string "$MERGED_DTB" "$LENS_DRIVERNODE" proc-device-tree \
	"/proc/device-tree/host1x/i2c@546c0000/dw9817@c"

echo "IMX708 A02 Jetson-IO overlay contract: PASS"
