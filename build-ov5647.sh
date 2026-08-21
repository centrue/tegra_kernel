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
  --ccache-dir DIR             Persistent ccache directory (default: /mnt/raid0/ccache/jetson-kernel)
  -h, --help                   Show this help

It emits the repacked nvidia-l4t-kernel / nvidia-l4t-kernel-dtbs debs into
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
	CC="$CCACHE_BIN ${CROSS_COMPILE}gcc"
	LOCALVERSION=-tegra
	'the-space=$(space)'
)

echo "OV5647 kernel build"
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
make "${MAKE_ARGS[@]}" olddefconfig
make "${MAKE_ARGS[@]}" -j"$JOBS" --output-sync=target Image modules dtbs
"$CCACHE_BIN" --show-stats | tee "$META_DIR/ccache-stats-after-compile.txt"

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
# Strip debug symbols like the shipped NVIDIA modules; the tegra_defconfig
# keeps DEBUG_INFO, so unstripped modules are ~13x larger than the official
# nvidia-l4t-kernel package (which would bloat the target rootfs and image).
make "${MAKE_ARGS[@]}" -j"$JOBS" INSTALL_MOD_PATH="$STAGE_DIR" INSTALL_MOD_STRIP=1 modules_install
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
	[[ "$(fdtget -t s "$MERGED_DTB" "$MODE_PATH" tegra_sinterface)" == serial_a ]] || \
		die "mode${mode} does not use CSI-A"
	[[ "$(fdtget -t s "$MERGED_DTB" "$MODE_PATH" pixel_t)" == bayer_bggr10 ]] || \
		die "mode${mode} is not RAW10 BGGR"
	[[ "$(fdtget -t s "$MERGED_DTB" "$MODE_PATH" pixel_phase)" == bggr ]] || \
		die "mode${mode} pixel_phase is not BGGR"
done

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

ARCHIVE="$OUTPUT/jetson-nano-a02-r32.7.6-ov5647-${DRIVER_MODE}.tar.gz"
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

# Replace the official kernel image and modules with this OV5647 build.
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

# Append the OV5647 Jetson-IO overlay to the official dtbs payload.
install -m 0644 "$DTBO" "$DTBS_PAYLOAD/boot/tegra210-p3448-common-ov5647.dtbo"

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
test -f "$DTBS_PAYLOAD/boot/tegra210-p3448-common-ov5647.dtbo"

sha256sum "$OUT_KERNEL_DEB" > "${OUT_KERNEL_DEB}.sha256"
sha256sum "$OUT_DTBS_DEB" > "${OUT_DTBS_DEB}.sha256"
echo "kernel deb: $OUT_KERNEL_DEB"
echo "dtbs deb:   $OUT_DTBS_DEB"

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

rm -f "$MERGED_DTB"
echo "build complete: $ARCHIVE"
echo "checksum: $ARCHIVE.sha256"
echo "staging tree: $PACKAGE_ROOT"
