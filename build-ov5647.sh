#!/usr/bin/env bash
# Build an installable L4T R32.7.6 kernel and OV5647 Jetson-IO overlay.

set -Eeuo pipefail

# Non-interactive build shells commonly omit the administrative tool paths.
export PATH="/usr/sbin:/sbin:${PATH}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KERNEL_SRC="${SCRIPT_DIR}/kernel/kernel-4.9"
DT_DIR="${SCRIPT_DIR}/hardware/nvidia/platform/t210/porg/kernel-dts"
BASE_DTB="${SCRIPT_DIR}/../Linux_for_Tegra/kernel/dtb/tegra210-p3448-0000-p3449-0000-a02.dtb"
TOOLCHAIN_ROOT="${CROSS_COMPILE_AARCH64_PATH:-${SCRIPT_DIR}/../gcc}"
CROSS_COMPILE="${TOOLCHAIN_ROOT}/bin/aarch64-linux-gnu-"
DRIVER_MODE="builtin"
JOBS="$(nproc)"
OUTPUT=""
CLEAN=0

usage() {
	cat <<'EOF'
Usage: ./build-ov5647.sh [OPTIONS]

Options:
  --driver-mode builtin|module  OV5647 linkage (default: builtin)
  --jobs N                     Parallel build jobs (default: nproc)
  --output DIR                 Output directory (default: out/ov5647-MODE)
  --clean                      Remove and recreate the selected output directory
  -h, --help                   Show this help

The script never installs into Linux_for_Tegra or a target device.
EOF
}

die() {
	echo "error: $*" >&2
	exit 1
}

while (($#)); do
	case "$1" in
		--driver-mode)
			(($# >= 2)) || die "--driver-mode requires an argument"
			DRIVER_MODE="$2"
			shift 2
			;;
		--jobs)
			(($# >= 2)) || die "--jobs requires an argument"
			JOBS="$2"
			shift 2
			;;
		--output)
			(($# >= 2)) || die "--output requires an argument"
			OUTPUT="$2"
			shift 2
			;;
		--clean)
			CLEAN=1
			shift
			;;
		-h|--help)
			usage
			exit 0
			;;
		*)
			die "unknown option: $1"
			;;
	esac
done

case "$DRIVER_MODE" in
	builtin|module) ;;
	*) die "--driver-mode must be builtin or module" ;;
esac
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || die "--jobs must be a positive integer"

if [[ -z "$OUTPUT" ]]; then
	OUTPUT="${SCRIPT_DIR}/out/ov5647-${DRIVER_MODE}"
elif [[ "$OUTPUT" != /* ]]; then
	OUTPUT="${SCRIPT_DIR}/${OUTPUT}"
fi
OUTPUT="$(realpath -m "$OUTPUT")"

[[ "$OUTPUT" != / && "$OUTPUT" != "$SCRIPT_DIR" ]] || \
	die "unsafe output directory: $OUTPUT"

if ((CLEAN)) && [[ -e "$OUTPUT" ]]; then
	[[ -f "$OUTPUT/.ov5647-build-dir" ]] || \
		die "refusing to clean unmarked directory: $OUTPUT"
	rm -rf -- "$OUTPUT"
fi

mkdir -p "$OUTPUT"
touch "$OUTPUT/.ov5647-build-dir"
BUILD_DIR="$OUTPUT/build"
STAGE_DIR="$OUTPUT/stage"
META_DIR="$OUTPUT/metadata"
PACKAGE_ROOT="$OUTPUT/package-root"
LOG_FILE="$OUTPUT/build.log"
mkdir -p "$BUILD_DIR" "$META_DIR"

exec > >(tee "$LOG_FILE") 2>&1

for command in make bc flex bison dtc fdtoverlay fdtget depmod tar sha256sum nm xxd cmp cp; do
	command -v "$command" >/dev/null 2>&1 || die "missing host command: $command"
done
[[ -x "${CROSS_COMPILE}gcc" ]] || die "cross compiler not found: ${CROSS_COMPILE}gcc"
COMPILER_VERSION="$(${CROSS_COMPILE}gcc --version | head -n1)"
[[ "$COMPILER_VERSION" == *"Linaro GCC 7.3-2018.05"* && \
	"$COMPILER_VERSION" == *"7.3.1"* ]] || \
	die "expected Linaro GCC 7.3.1 2018.05, got: $COMPILER_VERSION"
[[ -d "$KERNEL_SRC" ]] || die "kernel source missing: $KERNEL_SRC"
[[ -f "$DT_DIR/tegra210-p3448-common-ov5647.dts" ]] || die "OV5647 overlay source missing"
[[ -f "$BASE_DTB" ]] || die "shipping A02 audit DTB missing: $BASE_DTB"

SOURCE_DATE_EPOCH="$(git -C "$SCRIPT_DIR" show -s --format=%ct HEAD)"
export ARCH=arm64
export CROSS_COMPILE
export KBUILD_BUILD_USER=jetpack
export KBUILD_BUILD_HOST=jetson-kernel-builder
export KBUILD_BUILD_TIMESTAMP="$(date -u -d "@${SOURCE_DATE_EPOCH}" '+%a %b %d %H:%M:%S UTC %Y')"
export SOURCE_DATE_EPOCH

MAKE_ARGS=(
	-C "$KERNEL_SRC"
	O="$BUILD_DIR"
	ARCH=arm64
	CROSS_COMPILE="$CROSS_COMPILE"
	LOCALVERSION=-tegra
	'the-space=$(space)'
)

echo "OV5647 kernel build"
echo "source: $SCRIPT_DIR"
echo "output: $OUTPUT"
echo "driver mode: $DRIVER_MODE"
echo "jobs: $JOBS"
echo "compiler: $COMPILER_VERSION"

make "${MAKE_ARGS[@]}" tegra_defconfig
if [[ "$DRIVER_MODE" == builtin ]]; then
	"$KERNEL_SRC/scripts/config" --file "$BUILD_DIR/.config" \
		--enable VIDEO_OV5647
else
	"$KERNEL_SRC/scripts/config" --file "$BUILD_DIR/.config" \
		--module VIDEO_OV5647
fi
make "${MAKE_ARGS[@]}" olddefconfig
make "${MAKE_ARGS[@]}" -j"$JOBS" --output-sync=target Image modules dtbs

KERNEL_RELEASE="$(make "${MAKE_ARGS[@]}" -s kernelrelease)"
[[ "$KERNEL_RELEASE" == "4.9.337-tegra" ]] || \
	die "unexpected kernel release: $KERNEL_RELEASE"

CONFIG_STATE="$("$KERNEL_SRC/scripts/config" --file "$BUILD_DIR/.config" --state VIDEO_OV5647)"
if [[ "$DRIVER_MODE" == builtin ]]; then
	[[ "$CONFIG_STATE" == y ]] || die "CONFIG_VIDEO_OV5647 is not built in"
	"${CROSS_COMPILE}nm" "$BUILD_DIR/vmlinux" | \
		grep -E ' [tbd] ov5647_(probe|i2c_driver)$' >/dev/null || \
		die "OV5647 symbols are absent from vmlinux"
else
	[[ "$CONFIG_STATE" == m ]] || die "CONFIG_VIDEO_OV5647 is not modular"
	[[ -n "$(find "$BUILD_DIR" -type f -name ov5647.ko -print -quit)" ]] || \
		die "ov5647.ko was not built"
fi

DTBO="$BUILD_DIR/arch/arm64/boot/dts/tegra210-p3448-common-ov5647.dtbo"
[[ -s "$DTBO" ]] || die "OV5647 DTBO missing: $DTBO"

rm -rf -- "$STAGE_DIR" "$PACKAGE_ROOT"
mkdir -p "$STAGE_DIR" "$PACKAGE_ROOT/boot" \
	"$PACKAGE_ROOT/lib/modules" "$PACKAGE_ROOT/metadata/source"
make "${MAKE_ARGS[@]}" -j"$JOBS" INSTALL_MOD_PATH="$STAGE_DIR" modules_install
depmod -b "$STAGE_DIR" "$KERNEL_RELEASE"
rm -f "$STAGE_DIR/lib/modules/$KERNEL_RELEASE/build" \
	"$STAGE_DIR/lib/modules/$KERNEL_RELEASE/source"

install -m 0644 "$BUILD_DIR/arch/arm64/boot/Image" "$PACKAGE_ROOT/boot/Image"
install -m 0644 "$DTBO" "$PACKAGE_ROOT/boot/tegra210-p3448-common-ov5647.dtbo"
cp -a "$STAGE_DIR/lib/modules/$KERNEL_RELEASE" "$PACKAGE_ROOT/lib/modules/"
install -m 0644 "$BUILD_DIR/.config" "$PACKAGE_ROOT/metadata/kernel.config"
install -m 0644 "$BUILD_DIR/System.map" "$PACKAGE_ROOT/metadata/System.map"
install -m 0644 "$BUILD_DIR/Module.symvers" "$PACKAGE_ROOT/metadata/Module.symvers"

SOURCE_FILES=(
	build-ov5647.sh
	hardware/nvidia/platform/t210/porg/kernel-dts/Makefile
	hardware/nvidia/platform/t210/porg/kernel-dts/tegra210-p3448-common-ov5647.dts
	kernel/kernel-4.9/arch/arm64/configs/tegra_defconfig
	kernel/nvidia/drivers/media/i2c/Kconfig
	kernel/nvidia/drivers/media/i2c/Makefile
	kernel/nvidia/drivers/media/i2c/ov5647.c
	kernel/nvidia/drivers/media/i2c/ov5647_mode_tbls.h
)
(
	cd "$SCRIPT_DIR"
	cp --parents "${SOURCE_FILES[@]}" "$PACKAGE_ROOT/metadata/source/"
)

if [[ "$DRIVER_MODE" == builtin ]] && \
	[[ -n "$(find "$PACKAGE_ROOT/lib/modules" -type f -name ov5647.ko -print -quit)" ]]; then
	die "built-in bundle unexpectedly contains ov5647.ko"
fi

OVERLAY_DTS="$PACKAGE_ROOT/metadata/tegra210-p3448-common-ov5647.overlay.dts"
OVERLAY_WARNINGS="$META_DIR/overlay-dtc-warnings.log"
OVERLAY_CPP="$META_DIR/tegra210-p3448-common-ov5647.preprocessed.dts"
OVERLAY_AUDIT_DTBO="$META_DIR/tegra210-p3448-common-ov5647.audit.dtbo"
install -m 0644 "$DT_DIR/tegra210-p3448-common-ov5647.dts" "$OVERLAY_DTS"
"${CROSS_COMPILE}gcc" -E -nostdinc \
	-I"$SCRIPT_DIR/hardware/nvidia/soc/tegra/kernel-include" \
	-undef -D__DTS__ -x assembler-with-cpp \
	-o "$OVERLAY_CPP" "$DT_DIR/tegra210-p3448-common-ov5647.dts"
"$BUILD_DIR/scripts/dtc/dtc" -@ -I dts -O dtb -b 0 \
	-i "$DT_DIR" \
	-i "$SCRIPT_DIR/hardware/nvidia/soc/tegra/kernel-include" \
	-Wno-unit_address_vs_reg -o "$OVERLAY_AUDIT_DTBO" "$OVERLAY_CPP" \
	2>"$OVERLAY_WARNINGS"
[[ ! -s "$OVERLAY_WARNINGS" ]] || {
	cat "$OVERLAY_WARNINGS" >&2
	die "OV5647 overlay emits dtc warnings"
}
cmp -s "$DTBO" "$OVERLAY_AUDIT_DTBO" || \
	die "standalone overlay audit does not match the Kbuild DTBO"

[[ "$(fdtget -t s "$DTBO" / overlay-name)" == "Camera OV5647" ]] || \
	die "incorrect Jetson-IO overlay name"
[[ "$(fdtget -t s "$DTBO" / jetson-header-name)" == "Jetson Nano CSI Connector" ]] || \
	die "incorrect Jetson-IO header name"
[[ "$(fdtget -t s "$DTBO" / compatible)" == "nvidia,p3449-0000-a02+p3448-0000-a02" ]] || \
	die "incorrect A02 compatibility string"

MERGED_DTB="$META_DIR/a02-ov5647-audit.dtb"
MERGED_DTS="$PACKAGE_ROOT/metadata/a02-ov5647-merged-audit.dts"
fdtoverlay -i "$BASE_DTB" -o "$MERGED_DTB" "$DTBO"
dtc -I dtb -O dts -o "$MERGED_DTS" "$MERGED_DTB" 2>"$META_DIR/merged-dtc-warnings.log"

symbol_path() {
	fdtget -t s "$MERGED_DTB" /__symbols__ "$1"
}
assert_status() {
	local symbol="$1"
	local expected="$2"
	local path
	path="$(symbol_path "$symbol")"
	[[ "$(fdtget -t s "$MERGED_DTB" "$path" status)" == "$expected" ]] || \
		die "$symbol does not have status=$expected after overlay merge"
}

assert_status ov5647_single_cam0 okay
assert_status imx219_single_cam0 disabled
assert_status imx477_single_cam0 disabled
assert_status cam_module0_drivernode1 disabled

OV_ENDPOINT="$(symbol_path ov5647_out0)"
CSI_IN="$(symbol_path rbpcv2_imx219_csi_in0)"
CSI_OUT="$(symbol_path rbpcv2_imx219_csi_out0)"
VI_IN="$(symbol_path rbpcv2_imx219_vi_in0)"
[[ "$(fdtget -t x "$MERGED_DTB" "$OV_ENDPOINT" remote-endpoint)" == \
	"$(fdtget -t x "$MERGED_DTB" "$CSI_IN" phandle)" ]] || die "sensor-to-NVCSI graph is not reciprocal"
[[ "$(fdtget -t x "$MERGED_DTB" "$CSI_IN" remote-endpoint)" == \
	"$(fdtget -t x "$MERGED_DTB" "$OV_ENDPOINT" phandle)" ]] || die "NVCSI-to-sensor graph is not reciprocal"
[[ "$(fdtget -t x "$MERGED_DTB" "$CSI_OUT" remote-endpoint)" == \
	"$(fdtget -t x "$MERGED_DTB" "$VI_IN" phandle)" ]] || die "NVCSI-to-VI graph is not reciprocal"
[[ "$(fdtget -t x "$MERGED_DTB" "$VI_IN" remote-endpoint)" == \
	"$(fdtget -t x "$MERGED_DTB" "$CSI_OUT" phandle)" ]] || die "VI-to-NVCSI graph is not reciprocal"

for mode in 0 1 2 3; do
	MODE_PATH="$(symbol_path ov5647_single_cam0)/mode${mode}"
	[[ "$(fdtget -t s "$MERGED_DTB" "$MODE_PATH" num_lanes)" == 2 ]] || \
		die "mode${mode} is not two-lane"
	[[ "$(fdtget -t s "$MERGED_DTB" "$MODE_PATH" tegra_sinterface)" == serial_a ]] || \
		die "mode${mode} does not use CSI-A"
	[[ "$(fdtget -t s "$MERGED_DTB" "$MODE_PATH" pixel_t)" == bayer_bggr10 ]] || \
		die "mode${mode} is not RAW10 BGGR"
done

{
	echo -e "field\tvalue"
	echo -e "kernel_release\t$KERNEL_RELEASE"
	echo -e "driver_mode\t$DRIVER_MODE"
	echo -e "compiler\t$COMPILER_VERSION"
	echo -e "source_commit\t$(git -C "$SCRIPT_DIR" rev-parse HEAD)"
	echo -e "source_dirty\t$(git -C "$SCRIPT_DIR" status --porcelain | wc -l) paths"
	echo -e "source_date_epoch\t$SOURCE_DATE_EPOCH"
	echo -e "toolchain_gcc_sha256\t$(sha256sum "${CROSS_COMPILE}gcc" | awk '{print $1}')"
	echo -e "base_dtb_sha256\t$(sha256sum "$BASE_DTB" | awk '{print $1}')"
	echo -e "base_dtb_usage\taudit-only; not packaged"
} > "$PACKAGE_ROOT/metadata/provenance.tsv"
git -C "$SCRIPT_DIR" status --short > "$PACKAGE_ROOT/metadata/source-status.txt"
install -m 0644 "$LOG_FILE" "$PACKAGE_ROOT/metadata/build.log"

(
	cd "$PACKAGE_ROOT"
	find boot lib metadata -type f ! -name SHA256SUMS -print0 |
		sort -z | xargs -0 sha256sum > metadata/SHA256SUMS
	{
		echo -e "sha256\tpath"
		find boot lib metadata -type f ! -name manifest.tsv -print0 | sort -z | while IFS= read -r -d '' file; do
			printf '%s\t%s\n' "$(sha256sum "$file" | awk '{print $1}')" "$file"
		done
	} > metadata/manifest.tsv
)

ARCHIVE="$OUTPUT/jetson-nano-a02-r32.7.6-ov5647-${DRIVER_MODE}.tar.gz"
tar --sort=name --mtime="@${SOURCE_DATE_EPOCH}" --owner=0 --group=0 --numeric-owner \
	-C "$PACKAGE_ROOT" -czf "$ARCHIVE" boot lib metadata
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"

rm -f "$MERGED_DTB"
echo "build complete: $ARCHIVE"
echo "checksum: $ARCHIVE.sha256"
echo "staging tree: $PACKAGE_ROOT"
