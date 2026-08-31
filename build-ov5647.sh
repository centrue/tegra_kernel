#!/usr/bin/env bash
# Build an installable L4T R32.7.6 kernel with IMX708 + DW9817, OV5647,
# SEN0634 OV9281, and VC MIPI overlays.

set -Eeuo pipefail

# Non-interactive build shells commonly omit the administrative tool paths.
export PATH="/usr/sbin:/sbin:${PATH}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KERNEL_SRC="${SCRIPT_DIR}/kernel/kernel-4.9"
DT_DIR="${SCRIPT_DIR}/hardware/nvidia/platform/t210/porg/kernel-dts"
JETSON_IO_SOURCE="${SCRIPT_DIR}/../Linux_for_Tegra/source/public/nvidia-l4t-jetson-io-32.7.6-20241104234540/rootfs/opt/nvidia/jetson-io"
BASE_DTB="${SCRIPT_DIR}/../Linux_for_Tegra/kernel/dtb/tegra210-p3448-0000-p3449-0000-a02.dtb"
TOOLCHAIN_ROOT="${CROSS_COMPILE_AARCH64_PATH:-${SCRIPT_DIR}/../gcc}"
CROSS_COMPILE="${TOOLCHAIN_ROOT}/bin/aarch64-linux-gnu-"
DRIVER_MODE="builtin"
JOBS="$(nproc)"
OUTPUT=""
CLEAN=0
UPDATE_PACKAGE_LOCK=1
CCACHE_DIR_OVERRIDE="${JETSON_CCACHE_DIR:-${CCACHE_DIR:-/home/liaic/ccache/jetson-kernel}}"

usage() {
	cat <<'EOF'
Usage: ./build-ov5647.sh [OPTIONS]

Options:
  --driver-mode builtin|module  OV5647 linkage (default: builtin)
  --jobs N                     Parallel build jobs (default: nproc)
  --output DIR                 Output directory (default: out/ov5647-MODE)
  --clean                      Remove and recreate the selected output directory
  --no-package-lock-update     Do not install the debs into Linux_for_Tegra/kernel
                               or update tools/jetson-jammy/package-lock.tsv
  --ccache-dir DIR             Persistent ccache directory (default: /home/liaic/ccache/jetson-kernel)
  -h, --help                   Show this help

The IMX708 sensor and DW9817 manual-focus drivers are always built in. The
"Camera IMX708" Jetson-IO overlay exposes 4608x2592, 2304x1296, and 1536x864
non-HDR modes plus V4L2_CID_FOCUS_ABSOLUTE. The SEN0634 OV9281 driver is also
built in and selected by the "Camera OV9281 SEN0634" Jetson-IO overlay. VC MIPI
is always built in and includes the Stage 2 forced-model and independent I2C
address support. The DTB package also carries metadata-driven Auto Detect,
IMX296, IMX412, and IMX565 Jetson-IO profiles. It
emits the repacked nvidia-l4t-kernel / nvidia-l4t-kernel-dtbs debs into
<output>/deb/ (OUTPUT defaults to out/ov5647-MODE). The pristine NVIDIA
packages under Linux_for_Tegra/kernel/ are used as packaging templates. By
default, the resulting debs replace the two kernel package files there and
refresh their exact hashes in tools/jetson-jammy/package-lock.tsv. Use
--no-package-lock-update for a read-only package-template build. It never
flashes a device.
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
		--no-package-lock-update)
			UPDATE_PACKAGE_LOCK=0
			shift
			;;
		--ccache-dir)
			(($# >= 2)) || die "--ccache-dir requires an argument"
			CCACHE_DIR_OVERRIDE="$2"
			shift 2
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

for command in make bc flex bison dtc fdtoverlay fdtget depmod dpkg-deb tar sha256sum nm xxd cmp cp awk mktemp mv install ccache; do
	command -v "$command" >/dev/null 2>&1 || die "missing host command: $command"
done
CCACHE_BIN="$(command -v ccache)"
CCACHE_DIR="$(realpath -m "$CCACHE_DIR_OVERRIDE")"
mkdir -p "$CCACHE_DIR"
export CCACHE_DIR
export CCACHE_COMPILERCHECK=content
[[ -x "${CROSS_COMPILE}gcc" ]] || die "cross compiler not found: ${CROSS_COMPILE}gcc"
COMPILER_VERSION="$(${CROSS_COMPILE}gcc --version | head -n1)"
[[ "$COMPILER_VERSION" == *"Linaro GCC 7.3-2018.05"* && \
	"$COMPILER_VERSION" == *"7.3.1"* ]] || \
	die "expected Linaro GCC 7.3.1 2018.05, got: $COMPILER_VERSION"
[[ -d "$KERNEL_SRC" ]] || die "kernel source missing: $KERNEL_SRC"
[[ -f "$DT_DIR/tegra210-p3448-common-imx708.dts" ]] || die "IMX708 overlay source missing"
[[ -f "$DT_DIR/tegra210-p3448-common-ov5647.dts" ]] || die "OV5647 overlay source missing"
[[ -f "$DT_DIR/tegra210-p3448-common-ov9281-sen0634.dts" ]] || die "SEN0634 OV9281 overlay source missing"
[[ -f "$DT_DIR/tegra210-p3448-common-vc-mipi.dts" ]] || die "VC MIPI overlay source missing"
for profile in auto imx296 imx412 imx565; do
	[[ -f "$DT_DIR/tegra210-p3448-camera-vc-mipi-${profile}.dts" ]] || \
		die "VC MIPI ${profile} profile source missing"
done
for source in jetson-io.py config-by-hardware.py Utils/vc_profiles.py; do
	[[ -f "$JETSON_IO_SOURCE/$source" ]] || die "Jetson-IO Stage 2 source missing: $source"
done
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
	CC="$CCACHE_BIN ${CROSS_COMPILE}gcc"
	LOCALVERSION=-tegra
	'the-space=$(space)'
)

echo "IMX708 + OV5647 + VC MIPI Stage 2 kernel build"
echo "source: $SCRIPT_DIR"
echo "output: $OUTPUT"
echo "driver mode: $DRIVER_MODE"
echo "jobs: $JOBS"
echo "compiler: $COMPILER_VERSION"
echo "ccache: $CCACHE_BIN"
echo "ccache directory: $CCACHE_DIR"

make "${MAKE_ARGS[@]}" tegra_defconfig
if [[ "$DRIVER_MODE" == builtin ]]; then
	"$KERNEL_SRC/scripts/config" --file "$BUILD_DIR/.config" \
		--enable VIDEO_OV5647
else
	"$KERNEL_SRC/scripts/config" --file "$BUILD_DIR/.config" \
		--module VIDEO_OV5647
fi
"$KERNEL_SRC/scripts/config" --file "$BUILD_DIR/.config" \
	--enable VIDEO_IMX708 \
	--enable VIDEO_DW9817 \
	--enable VIDEO_OV9281_SEN0634
make "${MAKE_ARGS[@]}" olddefconfig
make "${MAKE_ARGS[@]}" -j"$JOBS" --output-sync=target Image modules dtbs
"$CCACHE_BIN" --show-stats | tee "$META_DIR/ccache-stats-after-compile.txt"

KERNEL_RELEASE="$(make "${MAKE_ARGS[@]}" -s kernelrelease)"
[[ "$KERNEL_RELEASE" == "4.9.337-tegra" ]] || \
	die "unexpected kernel release: $KERNEL_RELEASE"

CONFIG_STATE="$("$KERNEL_SRC/scripts/config" --file "$BUILD_DIR/.config" --state VIDEO_OV5647)"
IMX708_CONFIG_STATE="$("$KERNEL_SRC/scripts/config" --file "$BUILD_DIR/.config" --state VIDEO_IMX708)"
DW9817_CONFIG_STATE="$("$KERNEL_SRC/scripts/config" --file "$BUILD_DIR/.config" --state VIDEO_DW9817)"
SEN0634_CONFIG_STATE="$("$KERNEL_SRC/scripts/config" --file "$BUILD_DIR/.config" --state VIDEO_OV9281_SEN0634)"
VC_CONFIG_STATE="$("$KERNEL_SRC/scripts/config" --file "$BUILD_DIR/.config" --state NV_VIDEO_VC_MIPI)"
[[ "$IMX708_CONFIG_STATE" == y ]] || die "CONFIG_VIDEO_IMX708 is not built in"
[[ "$DW9817_CONFIG_STATE" == y ]] || die "CONFIG_VIDEO_DW9817 is not built in"
[[ "$SEN0634_CONFIG_STATE" == y ]] || die "CONFIG_VIDEO_OV9281_SEN0634 is not built in"
[[ "$VC_CONFIG_STATE" == y ]] || die "CONFIG_NV_VIDEO_VC_MIPI is not built in"
for symbol in vc_probe vc_core_init vc_mod_set_mode vc_mod_id_supported vc_core_release; do
	"${CROSS_COMPILE}nm" "$BUILD_DIR/vmlinux" | grep -E " [tT] ${symbol}$" >/dev/null ||
		die "VC MIPI symbol is absent from vmlinux: $symbol"
done
for symbol in ov9281_sen0634_probe; do
	"$CROSS_COMPILE"nm "$BUILD_DIR/vmlinux" | \
		grep -E " [tT] $symbol$" >/dev/null ||
		die "SEN0634 OV9281 symbol is absent from vmlinux: $symbol"
done
for symbol in imx708_probe; do
	"${CROSS_COMPILE}nm" "$BUILD_DIR/vmlinux" | \
		grep -E " [tT] ${symbol}$" >/dev/null ||
		die "IMX708 symbol is absent from vmlinux: $symbol"
done
for symbol in dw9817_probe; do
	"${CROSS_COMPILE}nm" "$BUILD_DIR/vmlinux" | \
		grep -E " [tT] ${symbol}$" >/dev/null ||
		die "DW9817 symbol is absent from vmlinux: $symbol"
done
"${CROSS_COMPILE}nm" "$BUILD_DIR/vmlinux" | \
	grep -E ' [dDbB] imx708_i2c_driver$' >/dev/null ||
	die "IMX708 driver data is absent from vmlinux"
"${CROSS_COMPILE}nm" "$BUILD_DIR/vmlinux" | \
	grep -E ' [dDbB] dw9817_i2c_driver$' >/dev/null ||
	die "DW9817 driver data is absent from vmlinux"
"$CROSS_COMPILE"nm "$BUILD_DIR/vmlinux" | \
	grep -E ' [dDbB] ov9281_sen0634_i2c_driver$' >/dev/null ||
	die "SEN0634 OV9281 driver data is absent from vmlinux"
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

IMX708_DTBO="$BUILD_DIR/arch/arm64/boot/dts/tegra210-p3448-common-imx708.dtbo"
OV5647_DTBO="$BUILD_DIR/arch/arm64/boot/dts/tegra210-p3448-common-ov5647.dtbo"
SEN0634_DTBO="$BUILD_DIR/arch/arm64/boot/dts/tegra210-p3448-common-ov9281-sen0634.dtbo"
VC_MIPI_DTBO="$BUILD_DIR/arch/arm64/boot/dts/tegra210-p3448-common-vc-mipi.dtbo"
VC_PROFILE_SPECS=(
	"auto|Camera VC MIPI (Custom) / Auto Detect|Auto Detect||2|0|0|1.000|1.000|60000"
	"imx296|Camera VC MIPI (Custom) / IMX296|IMX296|296|1|1440|1080|4.968|3.726|60300"
	"imx412|Camera VC MIPI (Custom) / IMX412|IMX412|412|2|4032|3040|6.250|4.712|20000"
	"imx565|Camera VC MIPI (Custom) / IMX565|IMX565|565|2|4128|3000|11.311|8.220|17000"
)
[[ -s "$IMX708_DTBO" ]] || die "IMX708 DTBO missing: $IMX708_DTBO"
[[ -s "$OV5647_DTBO" ]] || die "OV5647 DTBO missing: $OV5647_DTBO"
[[ -s "$SEN0634_DTBO" ]] || die "SEN0634 OV9281 DTBO missing: $SEN0634_DTBO"
[[ -s "$VC_MIPI_DTBO" ]] || die "VC MIPI DTBO missing: $VC_MIPI_DTBO"
for spec in "${VC_PROFILE_SPECS[@]}"; do
	IFS='|' read -r profile _ <<< "$spec"
	[[ -s "$BUILD_DIR/arch/arm64/boot/dts/tegra210-p3448-camera-vc-mipi-${profile}.dtbo" ]] || \
		die "VC MIPI ${profile} profile DTBO missing"
done

rm -rf -- "$STAGE_DIR" "$PACKAGE_ROOT"
mkdir -p "$STAGE_DIR" "$PACKAGE_ROOT/boot" \
	"$PACKAGE_ROOT/lib/modules" "$PACKAGE_ROOT/metadata/source"
# Strip debug symbols like the shipped NVIDIA modules; the tegra_defconfig
# keeps DEBUG_INFO, so unstripped modules are ~13x larger than the official
# nvidia-l4t-kernel package (which would bloat the target rootfs and image).
# Linux 4.9 predates modules.builtin.modinfo, but modern kmod warns when it is
# absent. Suppress Kbuild's convenience depmod, add the empty compatibility
# index, then run the authoritative depmod below once the module tree is final.
make "${MAKE_ARGS[@]}" -j"$JOBS" INSTALL_MOD_PATH="$STAGE_DIR" INSTALL_MOD_STRIP=1 \
	DEPMOD=true modules_install
: > "$STAGE_DIR/lib/modules/$KERNEL_RELEASE/modules.builtin.modinfo"
depmod -b "$STAGE_DIR" "$KERNEL_RELEASE"
rm -f "$STAGE_DIR/lib/modules/$KERNEL_RELEASE/build" \
	"$STAGE_DIR/lib/modules/$KERNEL_RELEASE/source"

install -m 0644 "$BUILD_DIR/arch/arm64/boot/Image" "$PACKAGE_ROOT/boot/Image"
install -m 0644 "$IMX708_DTBO" "$PACKAGE_ROOT/boot/tegra210-p3448-common-imx708.dtbo"
install -m 0644 "$OV5647_DTBO" "$PACKAGE_ROOT/boot/tegra210-p3448-common-ov5647.dtbo"
install -m 0644 "$SEN0634_DTBO" "$PACKAGE_ROOT/boot/tegra210-p3448-common-ov9281-sen0634.dtbo"
install -m 0644 "$VC_MIPI_DTBO" "$PACKAGE_ROOT/boot/tegra210-p3448-common-vc-mipi.dtbo"
for spec in "${VC_PROFILE_SPECS[@]}"; do
	IFS='|' read -r profile _ <<< "$spec"
	install -m 0644 \
		"$BUILD_DIR/arch/arm64/boot/dts/tegra210-p3448-camera-vc-mipi-${profile}.dtbo" \
		"$PACKAGE_ROOT/boot/tegra210-p3448-camera-vc-mipi-${profile}.dtbo"
done
cp -a "$STAGE_DIR/lib/modules/$KERNEL_RELEASE" "$PACKAGE_ROOT/lib/modules/"
install -m 0644 "$BUILD_DIR/.config" "$PACKAGE_ROOT/metadata/kernel.config"
install -m 0644 "$BUILD_DIR/System.map" "$PACKAGE_ROOT/metadata/System.map"
install -m 0644 "$BUILD_DIR/Module.symvers" "$PACKAGE_ROOT/metadata/Module.symvers"

SOURCE_FILES=(
	build-ov5647.sh
	tests/test_imx708_build_integration.sh
	tests/test_imx708_modes_focus_static.sh
	tests/test_imx708_overlay.sh
	hardware/nvidia/platform/t210/porg/kernel-dts/Makefile
	hardware/nvidia/platform/t210/porg/kernel-dts/tegra210-p3448-common-imx708.dts
	hardware/nvidia/platform/t210/porg/kernel-dts/tegra210-p3448-common-ov5647.dts
	hardware/nvidia/platform/t210/porg/kernel-dts/tegra210-p3448-common-ov9281-sen0634.dts
	hardware/nvidia/platform/t210/porg/kernel-dts/tegra210-p3448-common-vc-mipi.dts
	hardware/nvidia/platform/t210/porg/kernel-dts/tegra210-p3448-camera-vc-mipi-profile.dtsi
	hardware/nvidia/platform/t210/porg/kernel-dts/tegra210-p3448-camera-vc-mipi-auto.dts
	hardware/nvidia/platform/t210/porg/kernel-dts/tegra210-p3448-camera-vc-mipi-imx296.dts
	hardware/nvidia/platform/t210/porg/kernel-dts/tegra210-p3448-camera-vc-mipi-imx412.dts
	hardware/nvidia/platform/t210/porg/kernel-dts/tegra210-p3448-camera-vc-mipi-imx565.dts
	jetson-io-package/DEBIAN/control
	jetson-io-package/DEBIAN/preinst
	jetson-io-package/DEBIAN/postrm
	kernel/kernel-4.9/Documentation/media/uapi/v4l/pixfmt-y14.rst
	kernel/kernel-4.9/Documentation/media/uapi/v4l/yuv-formats.rst
	kernel/kernel-4.9/arch/arm64/configs/tegra_defconfig
	kernel/kernel-4.9/drivers/gpio/gpio-tegra.c
	kernel/kernel-4.9/drivers/media/v4l2-core/v4l2-ioctl.c
	kernel/kernel-4.9/drivers/media/v4l2-core/videobuf2-v4l2.c
	kernel/kernel-4.9/include/media/v4l2-subdev.h
	kernel/kernel-4.9/include/media/videobuf2-v4l2.h
	kernel/kernel-4.9/include/uapi/linux/media-bus-format.h
	kernel/kernel-4.9/include/uapi/linux/videodev2.h
	kernel/nvidia/drivers/media/i2c/Kconfig
	kernel/nvidia/drivers/media/i2c/Makefile
	kernel/nvidia/drivers/media/i2c/dw9817.c
	kernel/nvidia/drivers/media/i2c/imx708.c
	kernel/nvidia/drivers/media/i2c/imx708_mode_tbls.h
	kernel/nvidia/drivers/media/i2c/ov5647.c
	kernel/nvidia/drivers/media/i2c/ov5647_mode_tbls.h
	kernel/nvidia/drivers/media/i2c/ov9281.c
	kernel/nvidia/drivers/media/i2c/ov9281_mode_tbls.h
	kernel/nvidia/drivers/media/i2c/ov9281_sen0634.c
	kernel/nvidia/drivers/media/i2c/ov9281_sen0634_mode_tbls.h
	kernel/nvidia/drivers/media/i2c/vc_mipi_camera.c
	kernel/nvidia/drivers/media/i2c/vc_mipi_core.c
	kernel/nvidia/drivers/media/i2c/vc_mipi_core.h
	kernel/nvidia/drivers/media/i2c/vc_mipi_modules.c
	kernel/nvidia/drivers/media/i2c/vc_mipi_modules.h
	kernel/nvidia/drivers/media/platform/tegra/camera/camera_common.c
	kernel/nvidia/drivers/media/platform/tegra/camera/csi/csi2_fops.c
	kernel/nvidia/drivers/media/platform/tegra/camera/csi/csi4_fops.c
	kernel/nvidia/drivers/media/platform/tegra/camera/sensor_common.c
	kernel/nvidia/drivers/media/platform/tegra/camera/tegracam_ctrls.c
	kernel/nvidia/drivers/media/platform/tegra/camera/tegracam_v4l2.c
	kernel/nvidia/drivers/media/platform/tegra/camera/vi/channel.c
	kernel/nvidia/drivers/media/platform/tegra/camera/vi/vi2_fops.c
	kernel/nvidia/drivers/media/platform/tegra/camera/vi/vi2_formats.h
	kernel/nvidia/drivers/video/tegra/host/nvhost_syncpt.c
	kernel/nvidia/drivers/video/tegra/host/nvhost_syncpt.h
	kernel/nvidia/include/linux/nvhost.h
	kernel/nvidia/include/media/camera_common.h
	kernel/nvidia/include/media/imx708.h
	kernel/nvidia/include/media/mc_common.h
	kernel/nvidia/include/media/tegra-v4l2-camera.h
)
(
	cd "$SCRIPT_DIR"
	cp --parents "${SOURCE_FILES[@]}" "$PACKAGE_ROOT/metadata/source/"
)
mkdir -p "$PACKAGE_ROOT/metadata/source/Linux_for_Tegra/jetson-io/Utils"
install -m 0644 "$JETSON_IO_SOURCE/jetson-io.py" \
	"$PACKAGE_ROOT/metadata/source/Linux_for_Tegra/jetson-io/jetson-io.py"
install -m 0644 "$JETSON_IO_SOURCE/config-by-hardware.py" \
	"$PACKAGE_ROOT/metadata/source/Linux_for_Tegra/jetson-io/config-by-hardware.py"
install -m 0644 "$JETSON_IO_SOURCE/Utils/vc_profiles.py" \
	"$PACKAGE_ROOT/metadata/source/Linux_for_Tegra/jetson-io/Utils/vc_profiles.py"

if [[ "$DRIVER_MODE" == builtin ]] && \
	[[ -n "$(find "$PACKAGE_ROOT/lib/modules" -type f -name ov5647.ko -print -quit)" ]]; then
	die "built-in bundle unexpectedly contains ov5647.ko"
fi
if [[ -n "$(find "$PACKAGE_ROOT/lib/modules" -type f -name dw9817.ko -print -quit)" ]]; then
	die "built-in bundle unexpectedly contains dw9817.ko"
fi

echo "Auditing IMX708 overlay"
IMX708_OVERLAY_DTS="$PACKAGE_ROOT/metadata/tegra210-p3448-common-imx708.overlay.dts"
IMX708_OVERLAY_WARNINGS="$META_DIR/imx708-overlay-dtc-warnings.log"
IMX708_OVERLAY_CPP="$META_DIR/tegra210-p3448-common-imx708.preprocessed.dts"
IMX708_OVERLAY_AUDIT_DTBO="$META_DIR/tegra210-p3448-common-imx708.audit.dtbo"
install -m 0644 "$DT_DIR/tegra210-p3448-common-imx708.dts" "$IMX708_OVERLAY_DTS"
"${CROSS_COMPILE}gcc" -E -nostdinc \
	-I"$SCRIPT_DIR/hardware/nvidia/soc/tegra/kernel-include" \
	-undef -D__DTS__ -x assembler-with-cpp \
	-o "$IMX708_OVERLAY_CPP" "$DT_DIR/tegra210-p3448-common-imx708.dts"
"$BUILD_DIR/scripts/dtc/dtc" -@ -I dts -O dtb -b 0 \
	-i "$DT_DIR" \
	-i "$SCRIPT_DIR/hardware/nvidia/soc/tegra/kernel-include" \
	-Wno-unit_address_vs_reg -o "$IMX708_OVERLAY_AUDIT_DTBO" "$IMX708_OVERLAY_CPP" \
	2>"$IMX708_OVERLAY_WARNINGS"
[[ ! -s "$IMX708_OVERLAY_WARNINGS" ]] || {
	cat "$IMX708_OVERLAY_WARNINGS" >&2
	die "IMX708 overlay emits dtc warnings"
}
cmp -s "$IMX708_DTBO" "$IMX708_OVERLAY_AUDIT_DTBO" || \
	die "standalone IMX708 overlay audit does not match the Kbuild DTBO"

[[ "$(fdtget -t s "$IMX708_DTBO" / overlay-name)" == "Camera IMX708" ]] || \
	die "incorrect IMX708 Jetson-IO overlay name"
[[ "$(fdtget -t s "$IMX708_DTBO" / jetson-header-name)" == "Jetson Nano CSI Connector" ]] || \
	die "incorrect IMX708 Jetson-IO header name"
[[ "$(fdtget -t s "$IMX708_DTBO" / compatible)" == "nvidia,p3449-0000-a02+p3448-0000-a02" ]] || \
	die "incorrect IMX708 A02 compatibility string"

IMX708_MERGED_DTB="$META_DIR/a02-imx708-audit.dtb"
IMX708_MERGED_DTS="$PACKAGE_ROOT/metadata/a02-imx708-merged-audit.dts"
fdtoverlay -i "$BASE_DTB" -o "$IMX708_MERGED_DTB" "$IMX708_DTBO"
dtc -I dtb -O dts -o "$IMX708_MERGED_DTS" "$IMX708_MERGED_DTB" \
	2>"$META_DIR/imx708-merged-dtc-warnings.log"

imx708_symbol_path() {
	fdtget -t s "$IMX708_MERGED_DTB" /__symbols__ "$1"
}
imx708_assert_status() {
	local symbol="$1"
	local expected="$2"
	local path
	path="$(imx708_symbol_path "$symbol")"
	[[ "$(fdtget -t s "$IMX708_MERGED_DTB" "$path" status)" == "$expected" ]] || \
		die "$symbol does not have status=$expected after IMX708 overlay merge"
}

imx708_assert_status imx708_single_cam0 okay
imx708_assert_status imx708_dw9817 okay
imx708_assert_status imx219_single_cam0 disabled
imx708_assert_status imx477_single_cam0 disabled
imx708_assert_status cam_module0_drivernode1 okay

IMX708_SENSOR="$(imx708_symbol_path imx708_single_cam0)"
[[ "$(fdtget -t s "$IMX708_MERGED_DTB" "$IMX708_SENSOR" compatible)" == sony,imx708 ]] || \
	die "IMX708 compatible is incorrect"
[[ "$(fdtget -t x "$IMX708_MERGED_DTB" "$IMX708_SENSOR" reg)" == 1a ]] || \
	die "IMX708 sensor must use I2C address 0x1a"
[[ "$(fdtget -t s "$IMX708_MERGED_DTB" "$IMX708_SENSOR" sensor_model)" == imx708 ]] || \
	die "IMX708 sensor_model is incorrect"

IMX708_VCM="$(imx708_symbol_path imx708_dw9817)"
[[ "$(fdtget -t s "$IMX708_MERGED_DTB" "$IMX708_VCM" compatible)" == \
	"dongwoon,dw9817-vcm" ]] || die "IMX708 DW9817 compatible is incorrect"
[[ "$(fdtget -t x "$IMX708_MERGED_DTB" "$IMX708_VCM" reg)" == c ]] || \
	die "IMX708 DW9817 must use I2C address 0x0c"
[[ "$(fdtget -t x "$IMX708_MERGED_DTB" "$IMX708_SENSOR" lens-focus)" == \
	"$(fdtget -t x "$IMX708_MERGED_DTB" "$IMX708_VCM" phandle)" ]] || \
	die "IMX708 lens-focus does not reference DW9817"

imx708_check_mode() {
	local name="$1" width="$2" height="$3" line_length="$4"
	local pixel_clock="$5" max_fps="$6" default_fps="$7"
	local mode="$IMX708_SENSOR/$name"
	local property expected

	for property_value in \
		"mclk_khz 24000" \
		"num_lanes 2" \
		"tegra_sinterface serial_a" \
		"active_w $width" \
		"active_h $height" \
		"line_length $line_length" \
		"pix_clk_hz $pixel_clock" \
		"pixel_phase rggb" \
		"pixel_t bayer_rggb10" \
		"max_framerate $max_fps" \
		"default_framerate $default_fps" \
		"embedded_metadata_height 2"; do
		read -r property expected <<< "$property_value"
		[[ "$(fdtget -t s "$IMX708_MERGED_DTB" "$mode" "$property")" == "$expected" ]] || \
			die "IMX708 $name $property is not $expected"
	done
}

imx708_check_mode mode0 4608 2592 15648 595200000 14000000 14000000
imx708_check_mode mode1 2304 1296 7824 585600000 56000000 30000000
imx708_check_mode mode2 1536 864 5216 566400000 120000000 30000000

IMX708_ENDPOINT="$(imx708_symbol_path imx708_out0)"
IMX708_CSI_IN="$(imx708_symbol_path rbpcv2_imx219_csi_in0)"
IMX708_CSI_OUT="$(imx708_symbol_path rbpcv2_imx219_csi_out0)"
IMX708_VI_IN="$(imx708_symbol_path rbpcv2_imx219_vi_in0)"
[[ "$(fdtget -t x "$IMX708_MERGED_DTB" "$IMX708_ENDPOINT" remote-endpoint)" == \
	"$(fdtget -t x "$IMX708_MERGED_DTB" "$IMX708_CSI_IN" phandle)" ]] || \
	die "IMX708 sensor-to-NVCSI graph is not reciprocal"
[[ "$(fdtget -t x "$IMX708_MERGED_DTB" "$IMX708_CSI_IN" remote-endpoint)" == \
	"$(fdtget -t x "$IMX708_MERGED_DTB" "$IMX708_ENDPOINT" phandle)" ]] || \
	die "IMX708 NVCSI-to-sensor graph is not reciprocal"
[[ "$(fdtget -t x "$IMX708_MERGED_DTB" "$IMX708_CSI_OUT" remote-endpoint)" == \
	"$(fdtget -t x "$IMX708_MERGED_DTB" "$IMX708_VI_IN" phandle)" ]] || \
	die "IMX708 NVCSI-to-VI graph is not reciprocal"
[[ "$(fdtget -t x "$IMX708_MERGED_DTB" "$IMX708_VI_IN" remote-endpoint)" == \
	"$(fdtget -t x "$IMX708_MERGED_DTB" "$IMX708_CSI_OUT" phandle)" ]] || \
	die "IMX708 VI-to-NVCSI graph is not reciprocal"

IMX708_DRIVERNODE="$(imx708_symbol_path cam_module0_drivernode0)"
[[ "$(fdtget -t s "$IMX708_MERGED_DTB" "$IMX708_DRIVERNODE" devname)" == "imx708 6-001a" ]] || \
	die "IMX708 camera-module devname is incorrect"
[[ "$(fdtget -t s "$IMX708_MERGED_DTB" "$IMX708_DRIVERNODE" proc-device-tree)" == \
	"/proc/device-tree/host1x/i2c@546c0000/imx708_a@1a" ]] || \
	die "IMX708 camera-module proc-device-tree is incorrect"

IMX708_LENS_DRIVERNODE="$(imx708_symbol_path cam_module0_drivernode1)"
[[ "$(fdtget -t s "$IMX708_MERGED_DTB" "$IMX708_LENS_DRIVERNODE" pcl_id)" == \
	"v4l2_lens" ]] || die "IMX708 lens pcl_id is incorrect"
[[ "$(fdtget -t s "$IMX708_MERGED_DTB" "$IMX708_LENS_DRIVERNODE" devname)" == \
	"dw9817 6-000c" ]] || die "IMX708 lens devname is incorrect"
[[ "$(fdtget -t s "$IMX708_MERGED_DTB" "$IMX708_LENS_DRIVERNODE" proc-device-tree)" == \
	"/proc/device-tree/host1x/i2c@546c0000/dw9817@c" ]] || \
	die "IMX708 lens proc-device-tree is incorrect"

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
cmp -s "$OV5647_DTBO" "$OVERLAY_AUDIT_DTBO" || \
	die "standalone overlay audit does not match the Kbuild DTBO"

[[ "$(fdtget -t s "$OV5647_DTBO" / overlay-name)" == "Camera OV5647" ]] || \
	die "incorrect Jetson-IO overlay name"
[[ "$(fdtget -t s "$OV5647_DTBO" / jetson-header-name)" == "Jetson Nano CSI Connector" ]] || \
	die "incorrect Jetson-IO header name"
[[ "$(fdtget -t s "$OV5647_DTBO" / compatible)" == "nvidia,p3449-0000-a02+p3448-0000-a02" ]] || \
	die "incorrect A02 compatibility string"

MERGED_DTB="$META_DIR/a02-ov5647-audit.dtb"
MERGED_DTS="$PACKAGE_ROOT/metadata/a02-ov5647-merged-audit.dts"
fdtoverlay -i "$BASE_DTB" -o "$MERGED_DTB" "$OV5647_DTBO"
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

OV_SENSOR="$(symbol_path ov5647_single_cam0)"
[[ "$(fdtget -t s "$MERGED_DTB" "$OV_SENSOR" mclk)" == clk_out_3 ]] || \
	die "OV5647 must use the documented Tegra clk_out_3 input clock"
[[ "$(fdtget -t x "$MERGED_DTB" "$OV_SENSOR" clock-frequency)" == 16e3600 ]] || \
	die "OV5647 clock-frequency is not 24 MHz"

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
	MODE_PATH="$OV_SENSOR/mode${mode}"
	[[ "$(fdtget -t s "$MERGED_DTB" "$MODE_PATH" mclk_khz)" == 24000 ]] || \
		die "mode${mode} does not declare the documented 24 MHz input clock"
	[[ "$(fdtget -t s "$MERGED_DTB" "$MODE_PATH" num_lanes)" == 2 ]] || \
		die "mode${mode} is not two-lane"
	[[ "$(fdtget -t s "$MERGED_DTB" "$MODE_PATH" active_l)" == 0 ]] || \
		die "mode${mode} crop-left origin is not zero"
	[[ "$(fdtget -t s "$MERGED_DTB" "$MODE_PATH" active_t)" == 0 ]] || \
		die "mode${mode} crop-top origin is not zero"
	[[ "$(fdtget -t s "$MERGED_DTB" "$MODE_PATH" tegra_sinterface)" == serial_a ]] || \
		die "mode${mode} does not use CSI-A"
	[[ "$(fdtget -t s "$MERGED_DTB" "$MODE_PATH" pixel_t)" == bayer_bggr10 ]] || \
		die "mode${mode} is not RAW10 BGGR"
	[[ "$(fdtget -t s "$MERGED_DTB" "$MODE_PATH" pixel_phase)" == bggr ]] || \
		die "mode${mode} pixel_phase is not BGGR"
done

echo "Auditing SEN0634 OV9281 overlay"
SEN0634_OVERLAY_DTS="$PACKAGE_ROOT/metadata/tegra210-p3448-common-ov9281-sen0634.overlay.dts"
SEN0634_OVERLAY_CPP="$META_DIR/tegra210-p3448-common-ov9281-sen0634.preprocessed.dts"
SEN0634_OVERLAY_AUDIT_DTBO="$META_DIR/tegra210-p3448-common-ov9281-sen0634.audit.dtbo"
SEN0634_OVERLAY_WARNINGS="$META_DIR/ov9281-sen0634-overlay-dtc-warnings.log"
install -m 0644 "$DT_DIR/tegra210-p3448-common-ov9281-sen0634.dts" "$SEN0634_OVERLAY_DTS"
"$CROSS_COMPILE"gcc -E -nostdinc \
	-I"$SCRIPT_DIR/hardware/nvidia/soc/tegra/kernel-include" \
	-undef -D__DTS__ -x assembler-with-cpp \
	-o "$SEN0634_OVERLAY_CPP" "$DT_DIR/tegra210-p3448-common-ov9281-sen0634.dts"
"$BUILD_DIR/scripts/dtc/dtc" -@ -I dts -O dtb -b 0 \
	-i "$DT_DIR" \
	-i "$SCRIPT_DIR/hardware/nvidia/soc/tegra/kernel-include" \
	-Wno-unit_address_vs_reg -o "$SEN0634_OVERLAY_AUDIT_DTBO" "$SEN0634_OVERLAY_CPP" \
	2>"$SEN0634_OVERLAY_WARNINGS"
[[ ! -s "$SEN0634_OVERLAY_WARNINGS" ]] || {
	cat "$SEN0634_OVERLAY_WARNINGS" >&2
	die "SEN0634 OV9281 overlay emits dtc warnings"
}
cmp -s "$SEN0634_DTBO" "$SEN0634_OVERLAY_AUDIT_DTBO" || \
	die "standalone SEN0634 OV9281 overlay audit does not match the Kbuild DTBO"

[[ "$(fdtget -t s "$SEN0634_DTBO" / overlay-name)" == "Camera OV9281 SEN0634" ]] || \
	die "incorrect SEN0634 OV9281 Jetson-IO overlay name"
[[ "$(fdtget -t s "$SEN0634_DTBO" / jetson-header-name)" == "Jetson Nano CSI Connector" ]] || \
	die "incorrect SEN0634 OV9281 header name"
[[ "$(fdtget -t s "$SEN0634_DTBO" / compatible)" == "nvidia,p3449-0000-a02+p3448-0000-a02" ]] || \
	die "incorrect SEN0634 OV9281 A02 compatibility string"

SEN0634_MERGED_DTB="$META_DIR/a02-ov9281-sen0634-audit.dtb"
SEN0634_MERGED_DTS="$PACKAGE_ROOT/metadata/a02-ov9281-sen0634-merged-audit.dts"
fdtoverlay -i "$BASE_DTB" -o "$SEN0634_MERGED_DTB" "$SEN0634_DTBO"
dtc -I dtb -O dts -o "$SEN0634_MERGED_DTS" "$SEN0634_MERGED_DTB" \
	2>"$META_DIR/ov9281-sen0634-merged-dtc-warnings.log"

sen0634_symbol_path() {
	fdtget -t s "$SEN0634_MERGED_DTB" /__symbols__ "$1"
}
sen0634_assert_status() {
	local symbol="$1"
	local expected="$2"
	local path
	path="$(sen0634_symbol_path "$symbol")"
	[[ "$(fdtget -t s "$SEN0634_MERGED_DTB" "$path" status)" == "$expected" ]] || \
		die "$symbol does not have status=$expected after SEN0634 overlay merge"
}

sen0634_assert_status ov9281_sen0634_single_cam0 okay
sen0634_assert_status imx219_single_cam0 disabled
sen0634_assert_status imx477_single_cam0 disabled
sen0634_assert_status cam_module0_drivernode1 disabled

SEN0634_SENSOR="$(sen0634_symbol_path ov9281_sen0634_single_cam0)"
[[ "$(fdtget -t s "$SEN0634_MERGED_DTB" "$SEN0634_SENSOR" compatible)" == nvidia,ov9281-sen0634 ]] || \
	die "SEN0634 OV9281 compatible is incorrect"
[[ "$(fdtget -t x "$SEN0634_MERGED_DTB" "$SEN0634_SENSOR" reg)" == 60 ]] || \
	die "SEN0634 OV9281 sensor must use I2C address 0x60"
[[ "$(fdtget -t s "$SEN0634_MERGED_DTB" "$SEN0634_SENSOR" mclk)" == clk_out_3 ]] || \
	die "SEN0634 framework clock name is incorrect"
[[ "$(fdtget -t x "$SEN0634_MERGED_DTB" "$SEN0634_SENSOR" clock-frequency)" == 16e3600 ]] || \
	die "SEN0634 framework clock declaration is not 24 MHz"
if fdtget "$SEN0634_MERGED_DTB" "$SEN0634_SENSOR" reset-gpios >/dev/null 2>&1 || \
	fdtget "$SEN0634_MERGED_DTB" "$SEN0634_SENSOR" pwdn-gpios >/dev/null 2>&1; then
	die "SEN0634 overlay must not claim PWDN as reset-gpios/pwdn-gpios"
fi

SEN0634_MODE="$SEN0634_SENSOR/mode0"
[[ "$(fdtget -t s "$SEN0634_MERGED_DTB" "$SEN0634_MODE" mclk_khz)" == 24000 ]] || \
	die "SEN0634 mode clock is not 24 MHz"
[[ "$(fdtget -t s "$SEN0634_MERGED_DTB" "$SEN0634_MODE" num_lanes)" == 2 ]] || \
	die "SEN0634 mode is not two-lane"
[[ "$(fdtget -t s "$SEN0634_MERGED_DTB" "$SEN0634_MODE" active_w)" == 1280 ]] || \
	die "SEN0634 active width mismatch"
[[ "$(fdtget -t s "$SEN0634_MERGED_DTB" "$SEN0634_MODE" active_h)" == 800 ]] || \
	die "SEN0634 active height mismatch"
[[ "$(fdtget -t s "$SEN0634_MERGED_DTB" "$SEN0634_MODE" line_length)" == 1530 ]] || \
	die "SEN0634 line length mismatch"
[[ "$(fdtget -t s "$SEN0634_MERGED_DTB" "$SEN0634_MODE" pix_clk_hz)" == 160000000 ]] || \
	die "SEN0634 pixel clock mismatch"
[[ "$(fdtget -t s "$SEN0634_MERGED_DTB" "$SEN0634_MODE" pixel_t)" == bayer_rggb10 ]] || \
	die "SEN0634 mode is not RGGB RAW10"
[[ "$(fdtget -t s "$SEN0634_MERGED_DTB" "$SEN0634_MODE" pixel_phase)" == rggb ]] || \
	die "SEN0634 pixel phase is not RGGB"
[[ "$(fdtget -t s "$SEN0634_MERGED_DTB" "$SEN0634_MODE" max_framerate)" == 57000000 ]] || \
	die "SEN0634 maximum frame rate mismatch"

SEN0634_ENDPOINT="$(sen0634_symbol_path ov9281_sen0634_out0)"
SEN0634_CSI_IN="$(sen0634_symbol_path rbpcv2_imx219_csi_in0)"
SEN0634_CSI_OUT="$(sen0634_symbol_path rbpcv2_imx219_csi_out0)"
SEN0634_VI_IN="$(sen0634_symbol_path rbpcv2_imx219_vi_in0)"
[[ "$(fdtget -t x "$SEN0634_MERGED_DTB" "$SEN0634_ENDPOINT" remote-endpoint)" == \
	"$(fdtget -t x "$SEN0634_MERGED_DTB" "$SEN0634_CSI_IN" phandle)" ]] || \
	die "SEN0634 sensor-to-NVCSI graph is not reciprocal"
[[ "$(fdtget -t x "$SEN0634_MERGED_DTB" "$SEN0634_CSI_IN" remote-endpoint)" == \
	"$(fdtget -t x "$SEN0634_MERGED_DTB" "$SEN0634_ENDPOINT" phandle)" ]] || \
	die "SEN0634 NVCSI-to-sensor graph is not reciprocal"
[[ "$(fdtget -t x "$SEN0634_MERGED_DTB" "$SEN0634_CSI_OUT" remote-endpoint)" == \
	"$(fdtget -t x "$SEN0634_MERGED_DTB" "$SEN0634_VI_IN" phandle)" ]] || \
	die "SEN0634 NVCSI-to-VI graph is not reciprocal"
[[ "$(fdtget -t x "$SEN0634_MERGED_DTB" "$SEN0634_VI_IN" remote-endpoint)" == \
	"$(fdtget -t x "$SEN0634_MERGED_DTB" "$SEN0634_CSI_OUT" phandle)" ]] || \
	die "SEN0634 VI-to-NVCSI graph is not reciprocal"

SEN0634_DRIVERNODE="$(sen0634_symbol_path cam_module0_drivernode0)"
[[ "$(fdtget -t s "$SEN0634_MERGED_DTB" "$SEN0634_DRIVERNODE" devname)" == \
	"ov9281_sen0634 6-0060" ]] || die "SEN0634 camera-platform devname is incorrect"
[[ "$(fdtget -t s "$SEN0634_MERGED_DTB" "$SEN0634_DRIVERNODE" proc-device-tree)" == \
	"/proc/device-tree/host1x/i2c@546c0000/ov9281_sen0634_a@60" ]] || \
	die "SEN0634 camera-platform device-tree path is incorrect"

VC_OVERLAY_DTS="$PACKAGE_ROOT/metadata/tegra210-p3448-common-vc-mipi.overlay.dts"
VC_OVERLAY_CPP="$META_DIR/tegra210-p3448-common-vc-mipi.preprocessed.dts"
VC_OVERLAY_AUDIT_DTBO="$META_DIR/tegra210-p3448-common-vc-mipi.audit.dtbo"
VC_OVERLAY_WARNINGS="$META_DIR/vc-mipi-overlay-dtc-warnings.log"
install -m 0644 "$DT_DIR/tegra210-p3448-common-vc-mipi.dts" "$VC_OVERLAY_DTS"
"${CROSS_COMPILE}gcc" -E -nostdinc \
	-I"$SCRIPT_DIR/hardware/nvidia/soc/tegra/kernel-include" \
	-undef -D__DTS__ -x assembler-with-cpp \
	-o "$VC_OVERLAY_CPP" "$DT_DIR/tegra210-p3448-common-vc-mipi.dts"
"$BUILD_DIR/scripts/dtc/dtc" -@ -I dts -O dtb -b 0 \
	-i "$DT_DIR" \
	-i "$SCRIPT_DIR/hardware/nvidia/soc/tegra/kernel-include" \
	-Wno-unit_address_vs_reg -o "$VC_OVERLAY_AUDIT_DTBO" "$VC_OVERLAY_CPP" \
	2>"$VC_OVERLAY_WARNINGS"
[[ ! -s "$VC_OVERLAY_WARNINGS" ]] || {
	cat "$VC_OVERLAY_WARNINGS" >&2
	die "VC MIPI overlay emits dtc warnings"
}
cmp -s "$VC_MIPI_DTBO" "$VC_OVERLAY_AUDIT_DTBO" || \
	die "standalone VC MIPI overlay audit does not match the Kbuild DTBO"

[[ "$(fdtget -t s "$VC_MIPI_DTBO" / overlay-name)" == "Camera VC MIPI" ]] || \
	die "incorrect VC MIPI Jetson-IO overlay name"
[[ "$(fdtget -t s "$VC_MIPI_DTBO" / jetson-header-name)" == "Jetson Nano CSI Connector" ]] || \
	die "incorrect VC MIPI Jetson-IO header name"
[[ "$(fdtget -t s "$VC_MIPI_DTBO" / compatible)" == "nvidia,p3449-0000-a02+p3448-0000-a02" ]] || \
	die "incorrect VC MIPI A02 compatibility string"

VC_MERGED_DTB="$META_DIR/a02-vc-mipi-audit.dtb"
VC_MERGED_DTS="$PACKAGE_ROOT/metadata/a02-vc-mipi-merged-audit.dts"
fdtoverlay -i "$BASE_DTB" -o "$VC_MERGED_DTB" "$VC_MIPI_DTBO"
dtc -I dtb -O dts -o "$VC_MERGED_DTS" "$VC_MERGED_DTB" \
	2>"$META_DIR/vc-mipi-merged-dtc-warnings.log"

vc_symbol_path() {
	fdtget -t s "$VC_MERGED_DTB" /__symbols__ "$1"
}
vc_assert_status() {
	local symbol="$1"
	local expected="$2"
	local path
	path="$(vc_symbol_path "$symbol")"
	[[ "$(fdtget -t s "$VC_MERGED_DTB" "$path" status)" == "$expected" ]] || \
		die "$symbol does not have status=$expected after VC overlay merge"
}

vc_assert_status vc_mipi_cam0 okay
vc_assert_status imx219_single_cam0 disabled
vc_assert_status imx477_single_cam0 disabled
vc_assert_status cam_module0_drivernode1 disabled

VC_SENSOR="$(vc_symbol_path vc_mipi_cam0)"
[[ "$(fdtget -t s "$VC_MERGED_DTB" "$VC_SENSOR" compatible)" == nvidia,vc_mipi ]] || \
	die "VC MIPI sensor compatible is incorrect"
[[ "$(fdtget -t x "$VC_MERGED_DTB" "$VC_SENSOR" reg)" == 1a ]] || \
	die "VC MIPI sensor must use I2C address 0x1a"
[[ "$(fdtget -t x "$VC_MERGED_DTB" "$VC_SENSOR" vc,controller-address)" == 10 ]] || \
	die "VC MIPI controller address must be 0x10"
[[ "$(fdtget -t x "$VC_MERGED_DTB" "$VC_SENSOR" vc,sensor-address)" == 1a ]] || \
	die "VC MIPI configured sensor address must be 0x1a"
[[ "$(fdtget -t s "$VC_MERGED_DTB" "$VC_SENSOR" num_lanes)" == 2 ]] || \
	die "VC MIPI sensor is not configured for two CSI lanes"
[[ "$(fdtget -t s "$VC_MERGED_DTB" "$VC_SENSOR/mode0" tegra_sinterface)" == serial_a ]] || \
	die "VC MIPI sensor does not use CSI-A"

VC_ENDPOINT="$(vc_symbol_path vc_mipi_out0)"
VC_CSI_IN="$(vc_symbol_path rbpcv2_imx219_csi_in0)"
VC_CSI_OUT="$(vc_symbol_path rbpcv2_imx219_csi_out0)"
VC_VI_IN="$(vc_symbol_path rbpcv2_imx219_vi_in0)"
[[ "$(fdtget -t x "$VC_MERGED_DTB" "$VC_ENDPOINT" remote-endpoint)" == \
	"$(fdtget -t x "$VC_MERGED_DTB" "$VC_CSI_IN" phandle)" ]] || \
	die "VC-sensor-to-NVCSI graph is not reciprocal"
[[ "$(fdtget -t x "$VC_MERGED_DTB" "$VC_CSI_IN" remote-endpoint)" == \
	"$(fdtget -t x "$VC_MERGED_DTB" "$VC_ENDPOINT" phandle)" ]] || \
	die "VC-NVCSI-to-sensor graph is not reciprocal"
[[ "$(fdtget -t x "$VC_MERGED_DTB" "$VC_CSI_OUT" remote-endpoint)" == \
	"$(fdtget -t x "$VC_MERGED_DTB" "$VC_VI_IN" phandle)" ]] || \
	die "VC-NVCSI-to-VI graph is not reciprocal"
[[ "$(fdtget -t x "$VC_MERGED_DTB" "$VC_VI_IN" remote-endpoint)" == \
	"$(fdtget -t x "$VC_MERGED_DTB" "$VC_CSI_OUT" phandle)" ]] || \
	die "VC-VI-to-NVCSI graph is not reciprocal"

VC_DRIVERNODE="$(vc_symbol_path cam_module0_drivernode0)"
[[ "$(fdtget -t s "$VC_MERGED_DTB" "$VC_DRIVERNODE" devname)" == "vc_mipi 6-001a" ]] || \
	die "VC MIPI camera-platform devname is incorrect"
[[ "$(fdtget -t s "$VC_MERGED_DTB" "$VC_DRIVERNODE" proc-device-tree)" == \
	"/proc/device-tree/host1x/i2c@546c0000/vc_mipi@1a" ]] || \
	die "VC MIPI camera-platform device-tree path is incorrect"

echo "Auditing VC MIPI Stage 2 profile overlays"
for spec in "${VC_PROFILE_SPECS[@]}"; do
	IFS='|' read -r profile overlay_name profile_model force_id lanes active_w active_h physical_w physical_h max_framerate <<< "$spec"
	profile_dts="$DT_DIR/tegra210-p3448-camera-vc-mipi-${profile}.dts"
	profile_dtbo="$BUILD_DIR/arch/arm64/boot/dts/tegra210-p3448-camera-vc-mipi-${profile}.dtbo"
	profile_cpp="$META_DIR/tegra210-p3448-camera-vc-mipi-${profile}.preprocessed.dts"
	profile_audit_dtbo="$META_DIR/tegra210-p3448-camera-vc-mipi-${profile}.audit.dtbo"
	profile_warnings="$META_DIR/tegra210-p3448-camera-vc-mipi-${profile}.dtc-warnings.log"
	profile_merged="$META_DIR/a02-vc-mipi-${profile}-audit.dtb"

	"${CROSS_COMPILE}gcc" -E -nostdinc \
		-I"$DT_DIR" \
		-I"$SCRIPT_DIR/hardware/nvidia/soc/tegra/kernel-include" \
		-undef -D__DTS__ -x assembler-with-cpp \
		-o "$profile_cpp" "$profile_dts"
	"$BUILD_DIR/scripts/dtc/dtc" -@ -I dts -O dtb -b 0 \
		-i "$DT_DIR" \
		-i "$SCRIPT_DIR/hardware/nvidia/soc/tegra/kernel-include" \
		-Wno-unit_address_vs_reg -o "$profile_audit_dtbo" "$profile_cpp" \
		2>"$profile_warnings"
	[[ ! -s "$profile_warnings" ]] || {
		cat "$profile_warnings" >&2
		die "VC MIPI ${profile} profile emits dtc warnings"
	}
	cmp -s "$profile_dtbo" "$profile_audit_dtbo" || \
		die "VC MIPI ${profile} standalone audit differs from Kbuild"

	[[ "$(fdtget -t s "$profile_dtbo" / overlay-name)" == "$overlay_name" ]] || \
		die "VC MIPI ${profile} overlay name mismatch"
	[[ "$(fdtget -t s "$profile_dtbo" / jetson-header-name)" == "Jetson Nano CSI Connector" ]] || \
		die "VC MIPI ${profile} Jetson-IO header mismatch"
	[[ "$(fdtget -t s "$profile_dtbo" / compatible)" == "nvidia,p3449-0000-a02+p3448-0000-a02" ]] || \
		die "VC MIPI ${profile} A02 compatibility mismatch"
	[[ "$(fdtget -t s "$profile_dtbo" / vc-mipi-profile)" == custom ]] || \
		die "VC MIPI ${profile} grouping metadata mismatch"
	[[ "$(fdtget -t s "$profile_dtbo" / vc-mipi-parent)" == "Camera VC MIPI (Custom)" ]] || \
		die "VC MIPI ${profile} parent metadata mismatch"
	[[ "$(fdtget -t s "$profile_dtbo" / vc-mipi-model)" == "$profile_model" ]] || \
		die "VC MIPI ${profile} model metadata mismatch"

	fdtoverlay -i "$BASE_DTB" -o "$profile_merged" "$profile_dtbo"
	profile_sensor="$(fdtget -t s "$profile_merged" /__symbols__ vc_mipi_cam0)"
	profile_endpoint="$(fdtget -t s "$profile_merged" /__symbols__ vc_mipi_out0)"
	profile_csi_in="$(fdtget -t s "$profile_merged" /__symbols__ rbpcv2_imx219_csi_in0)"
	profile_csi_out="$(fdtget -t s "$profile_merged" /__symbols__ rbpcv2_imx219_csi_out0)"
	profile_vi_in="$(fdtget -t s "$profile_merged" /__symbols__ rbpcv2_imx219_vi_in0)"

	[[ "$(fdtget -t x "$profile_merged" "$profile_sensor" reg)" == 1a ]] || \
		die "VC MIPI ${profile} DT reg is not sensor address 0x1a"
	[[ "$(fdtget -t x "$profile_merged" "$profile_sensor" vc,controller-address)" == 10 ]] || \
		die "VC MIPI ${profile} controller address mismatch"
	[[ "$(fdtget -t x "$profile_merged" "$profile_sensor" vc,sensor-address)" == 1a ]] || \
		die "VC MIPI ${profile} sensor address mismatch"
	if [[ -n "$force_id" ]]; then
		[[ "$(fdtget -t x "$profile_merged" "$profile_sensor" vc,force-mod-id)" == "$force_id" ]] || \
			die "VC MIPI ${profile} forced module ID mismatch"
	elif fdtget "$profile_merged" "$profile_sensor" vc,force-mod-id >/dev/null 2>&1; then
		die "VC MIPI Auto Detect profile unexpectedly forces a module ID"
	fi
	[[ "$(fdtget -t s "$profile_merged" "$profile_sensor" num_lanes)" == "$lanes" ]] || \
		die "VC MIPI ${profile} sensor lane count mismatch"
	[[ "$(fdtget -t s "$profile_merged" "$profile_sensor/mode0" active_w)" == "$active_w" ]] || \
		die "VC MIPI ${profile} active width mismatch"
	[[ "$(fdtget -t s "$profile_merged" "$profile_sensor/mode0" active_h)" == "$active_h" ]] || \
		die "VC MIPI ${profile} active height mismatch"
	[[ "$(fdtget -t s "$profile_merged" "$profile_sensor" physical_w)" == "$physical_w" ]] || \
		die "VC MIPI ${profile} physical width mismatch"
	[[ "$(fdtget -t s "$profile_merged" "$profile_sensor" physical_h)" == "$physical_h" ]] || \
		die "VC MIPI ${profile} physical height mismatch"
	[[ "$(fdtget -t s "$profile_merged" "$profile_sensor/mode0" max_framerate)" == "$max_framerate" ]] || \
		die "VC MIPI ${profile} maximum frame rate mismatch"
	[[ "$(fdtget -t x "$profile_merged" "$profile_endpoint" bus-width)" == "$lanes" ]] || \
		die "VC MIPI ${profile} endpoint lane count mismatch"
	[[ "$(fdtget -t x "$profile_merged" "$profile_endpoint" remote-endpoint)" == \
		"$(fdtget -t x "$profile_merged" "$profile_csi_in" phandle)" ]] || \
		die "VC MIPI ${profile} sensor-to-NVCSI graph mismatch"
	[[ "$(fdtget -t x "$profile_merged" "$profile_csi_in" remote-endpoint)" == \
		"$(fdtget -t x "$profile_merged" "$profile_endpoint" phandle)" ]] || \
		die "VC MIPI ${profile} NVCSI-to-sensor graph mismatch"
	[[ "$(fdtget -t x "$profile_merged" "$profile_csi_out" remote-endpoint)" == \
		"$(fdtget -t x "$profile_merged" "$profile_vi_in" phandle)" ]] || \
		die "VC MIPI ${profile} NVCSI-to-VI graph mismatch"
	[[ "$(fdtget -t x "$profile_merged" "$profile_vi_in" remote-endpoint)" == \
		"$(fdtget -t x "$profile_merged" "$profile_csi_out" phandle)" ]] || \
		die "VC MIPI ${profile} VI-to-NVCSI graph mismatch"
	dtc -I dtb -O dts -o "$PACKAGE_ROOT/metadata/a02-vc-mipi-${profile}-merged-audit.dts" \
		"$profile_merged" 2>"$META_DIR/a02-vc-mipi-${profile}-merged-warnings.log"
done

python3 -m py_compile \
	"$JETSON_IO_SOURCE/jetson-io.py" \
	"$JETSON_IO_SOURCE/config-by-hardware.py" \
	"$JETSON_IO_SOURCE/Utils/vc_profiles.py"
BUILD_DIR="$BUILD_DIR" PYTHONPATH="$JETSON_IO_SOURCE" python3 - "${VC_PROFILE_SPECS[@]}" <<'PY'
import os
import sys
from Utils import vc_profiles

build_dir = os.environ['BUILD_DIR']
addons = {'Camera VC MIPI': os.path.join(
    build_dir, 'arch/arm64/boot/dts/tegra210-p3448-common-vc-mipi.dtbo')}
for spec in sys.argv[1:]:
    profile, name = spec.split('|', 2)[:2]
    addons[name] = os.path.join(
        build_dir, 'arch/arm64/boot/dts/',
        'tegra210-p3448-camera-vc-mipi-%s.dtbo' % profile)

ordinary, groups = vc_profiles.group(addons)
assert ordinary == ['Camera VC MIPI']
parent = 'Camera VC MIPI (Custom)'
assert [entry['model'] for entry in groups[parent]] == [
    'IMX296', 'IMX412', 'IMX565', 'Auto Detect']
for model in ('IMX296', 'IMX412', 'IMX565', 'Auto Detect'):
    assert vc_profiles.resolve(addons, '%s/%s' % (parent, model))
assert vc_profiles.resolve(addons, parent) is None
PY

{
	echo -e "field\tvalue"
	echo -e "kernel_release\t$KERNEL_RELEASE"
	echo -e "driver_mode\t$DRIVER_MODE"
	echo -e "compiler\t$COMPILER_VERSION"
	echo -e "ccache\t$CCACHE_BIN"
	echo -e "ccache_dir\t$CCACHE_DIR"
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

ARCHIVE="$OUTPUT/jetson-nano-a02-r32.7.6-imx708-ov5647-vc-mipi-stage2-${DRIVER_MODE}.tar.gz"
tar --sort=name --mtime="@${SOURCE_DATE_EPOCH}" --owner=0 --group=0 --numeric-owner \
	-C "$PACKAGE_ROOT" -czf "$ARCHIVE" boot lib metadata
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"

echo "Repacking kernel and DTBs as container-installable L4T debs"
L4T_KERNEL_DIR="${SCRIPT_DIR}/../Linux_for_Tegra/kernel"
KERNEL_DEB_VERSION="4.9.337-tegra-32.7.6-20241104234540"
OFFICIAL_KERNEL_DEB="${L4T_KERNEL_DIR}/nvidia-l4t-kernel_${KERNEL_DEB_VERSION}_arm64.deb"
OFFICIAL_DTBS_DEB="${L4T_KERNEL_DIR}/nvidia-l4t-kernel-dtbs_${KERNEL_DEB_VERSION}_arm64.deb"
DEB_STAGE="${OUTPUT}/deb"
KERNEL_PAYLOAD="${DEB_STAGE}/payload/nvidia-l4t-kernel"
DTBS_PAYLOAD="${DEB_STAGE}/payload/nvidia-l4t-kernel-dtbs"
OUT_KERNEL_DEB="${DEB_STAGE}/nvidia-l4t-kernel_${KERNEL_DEB_VERSION}_arm64.deb"
OUT_DTBS_DEB="${DEB_STAGE}/nvidia-l4t-kernel-dtbs_${KERNEL_DEB_VERSION}_arm64.deb"
MODULES_SOFTDEP="${DEB_STAGE}/modules.softdep"

[[ -f "$OFFICIAL_KERNEL_DEB" ]] || die "official kernel deb missing: $OFFICIAL_KERNEL_DEB"
[[ -f "$OFFICIAL_DTBS_DEB" ]] || die "official dtbs deb missing: $OFFICIAL_DTBS_DEB"

# The official packages are templates for control files, maintainer scripts,
# and the base dtb/dtbo set. Repacked debs are always written to $OUTPUT/deb/;
# after the package gates below, the default lock-update step also installs
# those exact artifacts into Linux_for_Tegra/kernel/.

rm -rf "$KERNEL_PAYLOAD" "$DTBS_PAYLOAD"
mkdir -p "$KERNEL_PAYLOAD" "$DTBS_PAYLOAD"
dpkg-deb -x "$OFFICIAL_KERNEL_DEB" "$KERNEL_PAYLOAD"
dpkg-deb -e "$OFFICIAL_KERNEL_DEB" "$KERNEL_PAYLOAD/DEBIAN"
dpkg-deb -x "$OFFICIAL_DTBS_DEB" "$DTBS_PAYLOAD"
dpkg-deb -e "$OFFICIAL_DTBS_DEB" "$DTBS_PAYLOAD/DEBIAN"

# Replace the official kernel image and modules with this camera-driver build.
if [[ -f "$KERNEL_PAYLOAD/lib/modules/$KERNEL_RELEASE/modules.softdep" ]]; then
	install -m 0644 "$KERNEL_PAYLOAD/lib/modules/$KERNEL_RELEASE/modules.softdep" \
		"$MODULES_SOFTDEP"
fi
rm -f "$KERNEL_PAYLOAD/boot/Image"
rm -rf "$KERNEL_PAYLOAD/lib/modules/$KERNEL_RELEASE"
mkdir -p "$KERNEL_PAYLOAD/boot" "$KERNEL_PAYLOAD/lib/modules"
install -m 0644 "$BUILD_DIR/arch/arm64/boot/Image" "$KERNEL_PAYLOAD/boot/Image"
cp -a "$STAGE_DIR/lib/modules/$KERNEL_RELEASE" "$KERNEL_PAYLOAD/lib/modules/"
rm -f "$KERNEL_PAYLOAD/lib/modules/$KERNEL_RELEASE/build" \
	"$KERNEL_PAYLOAD/lib/modules/$KERNEL_RELEASE/source"
if [[ -f "$MODULES_SOFTDEP" ]]; then
	install -m 0644 "$MODULES_SOFTDEP" \
		"$KERNEL_PAYLOAD/lib/modules/$KERNEL_RELEASE/modules.softdep"
fi

# Append the custom camera Jetson-IO overlays to the official dtbs payload.
install -m 0644 "$IMX708_DTBO" "$DTBS_PAYLOAD/boot/tegra210-p3448-common-imx708.dtbo"
install -m 0644 "$OV5647_DTBO" "$DTBS_PAYLOAD/boot/tegra210-p3448-common-ov5647.dtbo"
install -m 0644 "$SEN0634_DTBO" "$DTBS_PAYLOAD/boot/tegra210-p3448-common-ov9281-sen0634.dtbo"
install -m 0644 "$VC_MIPI_DTBO" "$DTBS_PAYLOAD/boot/tegra210-p3448-common-vc-mipi.dtbo"
for spec in "${VC_PROFILE_SPECS[@]}"; do
	IFS='|' read -r profile _ <<< "$spec"
	install -m 0644 \
		"$BUILD_DIR/arch/arm64/boot/dts/tegra210-p3448-camera-vc-mipi-${profile}.dtbo" \
		"$DTBS_PAYLOAD/boot/tegra210-p3448-camera-vc-mipi-${profile}.dtbo"
done

# The NVIDIA md5sums no longer match the payload; let dpkg-deb regenerate them.
rm -f "$KERNEL_PAYLOAD/DEBIAN/md5sums" "$DTBS_PAYLOAD/DEBIAN/md5sums"

rm -f "$OUT_KERNEL_DEB" "$OUT_DTBS_DEB"
# -Zxz matches the compression NVIDIA ships (gzip would make the kernel deb
# ~5x larger for identical content).
dpkg-deb --build -Zxz --root-owner-group "$KERNEL_PAYLOAD" "$OUT_KERNEL_DEB"
dpkg-deb --build -Zxz --root-owner-group "$DTBS_PAYLOAD" "$OUT_DTBS_DEB"

# Gate the repacked artifacts exactly like container-build.sh does.
[[ "$(dpkg-deb -f "$OUT_KERNEL_DEB" Package)" == nvidia-l4t-kernel ]] || \
	die "kernel deb Package mismatch"
[[ "$(dpkg-deb -f "$OUT_KERNEL_DEB" Version)" == "$KERNEL_DEB_VERSION" ]] || \
	die "kernel deb Version mismatch"
[[ "$(dpkg-deb -f "$OUT_KERNEL_DEB" Architecture)" == arm64 ]] || \
	die "kernel deb Architecture mismatch"
[[ "$(dpkg-deb -f "$OUT_DTBS_DEB" Package)" == nvidia-l4t-kernel-dtbs ]] || \
	die "dtbs deb Package mismatch"
[[ "$(dpkg-deb -f "$OUT_DTBS_DEB" Version)" == "$KERNEL_DEB_VERSION" ]] || \
	die "dtbs deb Version mismatch"
[[ "$(dpkg-deb -f "$OUT_DTBS_DEB" Architecture)" == arm64 ]] || \
	die "dtbs deb Architecture mismatch"
test -f "$KERNEL_PAYLOAD/boot/Image"
test -f "$KERNEL_PAYLOAD/lib/modules/$KERNEL_RELEASE/modules.dep"
test -f "$KERNEL_PAYLOAD/lib/modules/$KERNEL_RELEASE/modules.softdep"
test -f "$DTBS_PAYLOAD/boot/tegra210-p3448-0000-p3449-0000-a02.dtb"
test -f "$DTBS_PAYLOAD/boot/tegra210-p3448-common-imx708.dtbo"
test -f "$DTBS_PAYLOAD/boot/tegra210-p3448-common-ov5647.dtbo"
test -f "$DTBS_PAYLOAD/boot/tegra210-p3448-common-ov9281-sen0634.dtbo"
test -f "$DTBS_PAYLOAD/boot/tegra210-p3448-common-vc-mipi.dtbo"
for spec in "${VC_PROFILE_SPECS[@]}"; do
	IFS='|' read -r profile _ <<< "$spec"
	test -f "$DTBS_PAYLOAD/boot/tegra210-p3448-camera-vc-mipi-${profile}.dtbo"
done

echo "Building Jetson-IO VC MIPI Stage 2 companion package"
JETSON_IO_DEB_VERSION="32.7.6-1stage2"
JETSON_IO_PAYLOAD="${DEB_STAGE}/payload/jetson-io-vc-mipi-profiles"
OUT_JETSON_IO_DEB="${DEB_STAGE}/jetson-io-vc-mipi-profiles_${JETSON_IO_DEB_VERSION}_all.deb"
rm -rf "$JETSON_IO_PAYLOAD"
mkdir -p "$JETSON_IO_PAYLOAD/DEBIAN" \
	"$JETSON_IO_PAYLOAD/opt/nvidia/jetson-io/Utils"
install -m 0644 "$SCRIPT_DIR/jetson-io-package/DEBIAN/control" \
	"$JETSON_IO_PAYLOAD/DEBIAN/control"
install -m 0755 "$SCRIPT_DIR/jetson-io-package/DEBIAN/preinst" \
	"$JETSON_IO_PAYLOAD/DEBIAN/preinst"
install -m 0755 "$SCRIPT_DIR/jetson-io-package/DEBIAN/postrm" \
	"$JETSON_IO_PAYLOAD/DEBIAN/postrm"
install -m 0755 "$JETSON_IO_SOURCE/jetson-io.py" \
	"$JETSON_IO_PAYLOAD/opt/nvidia/jetson-io/jetson-io.py"
install -m 0755 "$JETSON_IO_SOURCE/config-by-hardware.py" \
	"$JETSON_IO_PAYLOAD/opt/nvidia/jetson-io/config-by-hardware.py"
install -m 0644 "$JETSON_IO_SOURCE/Utils/vc_profiles.py" \
	"$JETSON_IO_PAYLOAD/opt/nvidia/jetson-io/Utils/vc_profiles.py"
rm -f "$OUT_JETSON_IO_DEB"
dpkg-deb --build -Zxz --root-owner-group "$JETSON_IO_PAYLOAD" "$OUT_JETSON_IO_DEB"
[[ "$(dpkg-deb -f "$OUT_JETSON_IO_DEB" Package)" == jetson-io-vc-mipi-profiles ]] || \
	die "Jetson-IO profile package name mismatch"
[[ "$(dpkg-deb -f "$OUT_JETSON_IO_DEB" Version)" == "$JETSON_IO_DEB_VERSION" ]] || \
	die "Jetson-IO profile package version mismatch"
[[ "$(dpkg-deb -f "$OUT_JETSON_IO_DEB" Architecture)" == all ]] || \
	die "Jetson-IO profile package architecture mismatch"
test -f "$JETSON_IO_PAYLOAD/opt/nvidia/jetson-io/Utils/vc_profiles.py"

sha256sum "$OUT_KERNEL_DEB" > "${OUT_KERNEL_DEB}.sha256"
sha256sum "$OUT_DTBS_DEB" > "${OUT_DTBS_DEB}.sha256"
sha256sum "$OUT_JETSON_IO_DEB" > "${OUT_JETSON_IO_DEB}.sha256"
echo "kernel deb: $OUT_KERNEL_DEB"
echo "dtbs deb:   $OUT_DTBS_DEB"
echo "Jetson-IO Stage 2 deb: $OUT_JETSON_IO_DEB"

if ((UPDATE_PACKAGE_LOCK)); then
	PACKAGE_LOCK="${SCRIPT_DIR}/../Linux_for_Tegra/tools/jetson-jammy/package-lock.tsv"
	[[ -f "$PACKAGE_LOCK" ]] || die "package lock missing: $PACKAGE_LOCK"

	kernel_package="$(dpkg-deb -f "$OUT_KERNEL_DEB" Package)"
	kernel_version="$(dpkg-deb -f "$OUT_KERNEL_DEB" Version)"
	kernel_filename="$(basename "$OUT_KERNEL_DEB")"
	dtbs_package="$(dpkg-deb -f "$OUT_DTBS_DEB" Package)"
	dtbs_version="$(dpkg-deb -f "$OUT_DTBS_DEB" Version)"
	dtbs_filename="$(basename "$OUT_DTBS_DEB")"
	kernel_hash="$(sha256sum "$OUT_KERNEL_DEB" | awk '{print $1}')"
	dtbs_hash="$(sha256sum "$OUT_DTBS_DEB" | awk '{print $1}')"
	lock_kernel_path="kernel/${kernel_filename}"
	lock_dtbs_path="kernel/${dtbs_filename}"
	lock_tmp="$(mktemp "${PACKAGE_LOCK}.tmp.XXXXXX")"

	# The lock paths are consumed relative to Linux_for_Tegra/. Update exactly
	# the two local-L4T rows, preserving every unrelated package entry.
	if ! awk -F '\t' -v OFS='\t' \
		-v kp="$kernel_package" -v kv="$kernel_version" \
		-v kf="$lock_kernel_path" -v kh="$kernel_hash" \
		-v dp="$dtbs_package" -v dv="$dtbs_version" \
		-v df="$lock_dtbs_path" -v dh="$dtbs_hash" '
		BEGIN { kernel_rows = 0; dtbs_rows = 0 }
		/^#/ || NF == 0 { print; next }
		{
			if ($1 == "local-l4t" && $2 == kp) {
				if ($3 != kv || $4 != kf) exit 20
				$5 = kh
				kernel_rows++
			} else if ($1 == "local-l4t" && $2 == dp) {
				if ($3 != dv || $4 != df) exit 21
				$5 = dh
				dtbs_rows++
			}
			print
		}
		END {
			if (kernel_rows != 1 || dtbs_rows != 1) exit 22
		}' "$PACKAGE_LOCK" > "$lock_tmp"; then
		rm -f "$lock_tmp"
		die "package lock does not contain the expected local-L4T kernel rows"
	fi

	# Install the exact bytes whose hashes were recorded. This keeps the lock
	# consumable by fetch-package-cache.sh and container-build.sh.
	install -m 0644 "$OUT_KERNEL_DEB" \
		"${L4T_KERNEL_DIR}/${kernel_filename}"
	install -m 0644 "$OUT_DTBS_DEB" \
		"${L4T_KERNEL_DIR}/${dtbs_filename}"
	mv "$lock_tmp" "$PACKAGE_LOCK"
	echo "updated package lock: $PACKAGE_LOCK"
	echo "installed locked kernel deb: ${L4T_KERNEL_DIR}/${kernel_filename}"
	echo "installed locked dtbs deb:   ${L4T_KERNEL_DIR}/${dtbs_filename}"
else
	echo "package lock update disabled; Linux_for_Tegra/kernel/ left unchanged"
fi

rm -f "$IMX708_MERGED_DTB" "$MERGED_DTB" "$VC_MERGED_DTB" \
	"$META_DIR"/a02-vc-mipi-*-audit.dtb
echo "build complete: $ARCHIVE"
echo "checksum: $ARCHIVE.sha256"
echo "staging tree: $PACKAGE_ROOT"
