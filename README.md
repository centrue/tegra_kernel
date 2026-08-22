# Jetson Nano R32.7.6 kernel and OV5647 driver

This directory contains the NVIDIA L4T R32.7.6 (`4.9.337-tegra`) kernel
sources and the P3450/P3448 A02 OV5647 additions. The build is intentionally
out-of-tree: it writes only to `tegra_kernel/out/` and, unless
`--no-package-lock-update` is used, refreshes the matching kernel packages in
`Linux_for_Tegra/kernel/` and their entries in
`Linux_for_Tegra/tools/jetson-jammy/package-lock.tsv`.

The default build is a built-in OV5647 driver bundle. It does not replace a
base DTB and it does not flash a board.

## Changes in this tree

### OV5647 camera driver

The driver is in:

* `kernel/nvidia/drivers/media/i2c/ov5647.c`
* `kernel/nvidia/drivers/media/i2c/ov5647_mode_tbls.h`

It uses NVIDIA's Tegra camera framework v2, validates the sensor ID `0x5647`
at I2C address `0x36`, and implements the A02 power/reset sequence, 25 MHz
MCLK, two-lane CSI-A, RAW10 BGGR, exposure/gain/frame-length controls, and
four modes:

| Resolution | Maximum rate |
| --- | ---: |
| 2592x1944 | 15 fps |
| 1920x1080 | 30 fps |
| 1296x972 | 30 fps |
| 640x480 | 60 fps |

Reset, common, mode, start, and stop register tables are separate, so mode
selection does not accidentally start streaming. Probe failures unwind clocks,
regulators, GPIOs, and the tegracam registration.

`CONFIG_VIDEO_OV5647=y` is selected in `tegra_defconfig`; the build script
also supports `--driver-mode module` for a module-capable test build.

### Jetson-IO overlay

`hardware/nvidia/platform/t210/porg/kernel-dts/tegra210-p3448-common-ov5647.dts`
is a standalone Jetson-IO DTBO. It is compatible only with
`nvidia,p3449-0000-a02+p3448-0000-a02`, adds `ov5647@36` under `i2c7`, disables
the conflicting IMX219/IMX477 nodes, and reconnects VI/NVCSI channel 0.

The base A02 pinmux deliberately retains NVIDIA's `rsvd1`/`rsvd2` SPI
functions. SPI selection belongs to the HDR40 Jetson-IO overlay, which changes
the selected pins to `spi1` or `spi2` when a user enables them. Do not edit the
base A02 pinmux to enable SPI globally.

### GPIO/SPI ownership fix

`kernel/kernel-4.9/drivers/gpio/gpio-tegra.c` releases GPIO CNF ownership left
by the T210 boot firmware before SPI/I2S pinctrl probes. The A02 header ranges
are GPIOs `12..20`, `232`, and `76..79`. The fix also clears CNF ownership when
a GPIO is freed, while preserving the existing GPIO state save/restore logic.

This fixes the boot-firmware GPIO conflict; it does not replace Jetson-IO's
pin-function selection.

## Build

Install the host prerequisites used by the script, including `ccache`, the
Linaro GCC 7.3.1 toolchain in `../gcc`, `dtc`, `fdtoverlay`, and `depmod`.
The persistent ccache location is `/home/liaic/ccache/jetson-kernel` and can
be overridden with `--ccache-dir` or `JETSON_CCACHE_DIR`.

```bash
cd /mnt/raid0/workspace/Jetpack/tegra_kernel
./build-ov5647.sh --driver-mode builtin --jobs "$(nproc)"
```

The result is:

```text
out/ov5647-builtin/jetson-nano-a02-r32.7.6-ov5647-builtin.tar.gz
out/ov5647-builtin/deb/nvidia-l4t-kernel_4.9.337-tegra-32.7.6-20241104234540_arm64.deb
out/ov5647-builtin/deb/nvidia-l4t-kernel-dtbs_4.9.337-tegra-32.7.6-20241104234540_arm64.deb
```

The archive contains `boot/Image`, the OV5647 DTBO, the complete matching
module tree, config, `System.map`, `Module.symvers`, provenance, manifests,
and checksums. The two `.deb` files are the preferred installation artifacts.

## Install on an already-running Nano

These instructions are for a running P3450/P3448 A02 Nano already using L4T
R32.7.6. Confirm the current system before proceeding:

```bash
uname -r
dpkg-query -W -f='${Package} ${Version}\n' nvidia-l4t-core nvidia-l4t-kernel nvidia-l4t-kernel-dtbs
```

The kernel release must be `4.9.337-tegra`, and the NVIDIA packages must be
the `32.7.6-20241104234540` generation. Do not install this bundle on another
L4T release or on a non-T210 board.

### 1. Transfer the packages

Run on the build computer (replace the address and user):

```bash
cd /mnt/raid0/workspace/Jetpack
scp tegra_kernel/out/ov5647-builtin/deb/nvidia-l4t-kernel_4.9.337-tegra-32.7.6-20241104234540_arm64.deb \
    tegra_kernel/out/ov5647-builtin/deb/nvidia-l4t-kernel-dtbs_4.9.337-tegra-32.7.6-20241104234540_arm64.deb \
    jetson@JETSON_IP:/tmp/
```

The package SHA-256 values must match the two `nvidia-l4t-kernel*` rows in
`Linux_for_Tegra/tools/jetson-jammy/package-lock.tsv` before transferring or
installing them.

### 2. Back up the boot files on the Nano

Run on the Nano. Keep the printed backup directory until the new kernel has
passed physical testing:

```bash
BACKUP="/root/jetson-kernel-backup-$(date +%Y%m%d-%H%M%S)"
sudo install -d "$BACKUP"
for path in /boot/Image /boot/extlinux /boot/extlinux.conf /boot/dtb /etc/nv_boot_control.conf; do
    if [ -e "$path" ] || [ -L "$path" ]; then
        sudo cp -a "$path" "$BACKUP/"
    fi
done
sudo tar -C /boot -czf "$BACKUP/boot-files.tar.gz" Image extlinux dtb 2>/dev/null || true
printf 'Backup: %s\n' "$BACKUP"
```

### 3. Install both matching packages

Install the kernel package first and the DTB package second. The NVIDIA
maintainer scripts run `depmod`, update the extlinux entry, and install the
matching DTB/DTBO set. On T210 they do not flash a bootloader partition.

```bash
sudo dpkg -i /tmp/nvidia-l4t-kernel_4.9.337-tegra-32.7.6-20241104234540_arm64.deb
sudo dpkg -i /tmp/nvidia-l4t-kernel-dtbs_4.9.337-tegra-32.7.6-20241104234540_arm64.deb
sudo depmod -a 4.9.337-tegra
sudo sync
sudo reboot
```

Do not manually copy only `Image` and omit the module tree. Do not install the
DTBS package without its exact matching kernel package.

### 4. Verify after reboot

```bash
uname -r
test -d /sys/module/ov5647 && echo 'OV5647 driver is built in'
ls -l /boot/Image /boot/tegra210-p3448-common-ov5647.dtbo
find /lib/modules/4.9.337-tegra -type f | head
dmesg | grep -Ei 'ov5647|spi|gpio' | tail -80
```

The built-in driver normally has no `ov5647.ko`; `/sys/module/ov5647` and
probe messages are the relevant checks. Confirm the kernel release, module
tree, and DTBO before enabling the camera.

## Select OV5647 with Jetson-IO

The overlay is installed by the DTBS package but is not selected automatically.
Use the target's Jetson-IO utility interactively:

```bash
sudo /opt/nvidia/jetson-io/config-by-hardware.py -l
sudo /opt/nvidia/jetson-io/jetson-io.py
```

Choose **Jetson Nano CSI Connector** and **Camera OV5647**, save the pin/DTBO
configuration, and reboot when prompted. After reboot, verify the active
configuration and camera probe before running a capture test. Jetson-IO may
rewrite the extlinux selection, so keep the boot backup from the previous
section.

## Rollback

If the new kernel does not boot, use the serial console or another rootfs
access path and restore the saved files. Replace `BACKUP` with the directory
printed during backup:

```bash
sudo cp -a "$BACKUP/Image" /boot/
sudo cp -a "$BACKUP/extlinux" /boot/
sudo cp -a "$BACKUP/dtb" /boot/
sudo sync
sudo reboot
```

If Jetson-IO changed the boot selection, restore the saved
`extlinux/extlinux.conf` as well. Once the board is running the old kernel,
remove the new packages only if required; preserving the exact NVIDIA package
version is safer than mixing packages from another L4T release.

## Validation boundary

The build performs offline checks for the kernel release, built-in OV5647
symbols, DTBO compatibility, module staging, package contents, and SHA-256
manifests. Physical validation is still required for SPI electrical behavior,
Jetson-IO selection, OV5647 chip detection, all four capture modes, and camera
controls. The build does not flash, reboot, or modify a target board.
