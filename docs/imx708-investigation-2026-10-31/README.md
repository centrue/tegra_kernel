# IMX708 Nano 调查记录（2026-10-31）

通过 `ssh jetson@jetson-nano` 只读调查；没有安装内核、修改启动配置、操作 GPIO 或重启。原始证据见同目录 target-evidence.txt。

## 已确认：当前阻塞在驱动运行之前

- live DT 已启用 `/host1x/i2c@546c0000/imx708_a@1a`，compatible 为 `sony,imx708`，I²C client `6-001a` 已创建。这只证明设备树实例化，不证明传感器 ACK。
- `/sys/bus/i2c/drivers/imx708` 不存在，`/proc/kallsyms` 没有 imx708，`/proc/config.gz` 没有 CONFIG_VIDEO_IMX708；没有 video/subdev 节点。
- 运行内核和板端 `/boot/Image` 都带 NVIDIA 原版标识：`buildbrain@mobile-u64-5499-d7000`、`Mon Nov 4 23:41:41 PST 2024`。
- 板端 Image SHA256：`63fff7ba49e14a445e45fb26b1b48097a6af20320fb5fee014104100751eb42b`。
- 本机现有 `out/ov5647-builtin/package-root/boot/Image` 和 DEB staging Image SHA256 均为 `7ebb4e3ab6241c061d6e3091c01389aec7ef41627f8c86afe7ff083d377d3fe0`；构建标识为 `jetpack@jetson-kernel-builder`、`Mon Aug 31 06:27:00 UTC 2026`，含 `detected IMX708 sensor`。构建配置启用 CONFIG_VIDEO_IMX708=y、CONFIG_VIDEO_DW9817=y。
- 板端 modules.builtin 却含 imx708.ko（此处表示 built-in，不要求磁盘存在 .ko）。所以 `modinfo imx708` 显示 builtin 是模块索引证据，不能代表运行内核内置了驱动。
- 板端 dpkg 保存的 Image MD5 是 `aaa3fc726e7c624a0b2d245d8c30f6f8`，实际是 `5162941aa1fd62be4a4e8e5ac1292b03`；`dpkg -V nvidia-l4t-kernel` 报 Image 内容不匹配。已安装包记录与磁盘镜像不一致。

结论：当前不能归因于插接、I²C NACK 或 CSI 参数；驱动尚未运行。究竟是哪次刷写、恢复或安装导致 Image 留在原版，目前不能从这些证据确定。包版本号保持官方值，无法用版本号区分自定义载荷。

## GPIO 核查：不要误删 hog

GPIO 151（PS7/CAM1_PWDN）当前由 camera-control-output-low hog 持有，out lo。Nano imx708_power_get 会 gpio_request(151)。本仓库 `kernel/kernel-4.9/drivers/gpio/gpiolib.c` 的 __gpiod_request 明确允许接管 FLAG_IS_HOGGED，清除 hog 标志并返回成功。不能套用通用内核经验断言这里必然 EBUSY。先记录 gpio_request 返回值及接管后的方向、电平。

## Orin 对照与后续风险

对照路径：`/home/liaic/Documents/Orin_Nano/jetson-workspace/orin-kernel/source/nvidia-oot/drivers/media/i2c/nv_imx708.c`、同目录 mode tables、仓库 README；原始参考为 `/home/liaic/Documents/Orin_Nano/NVIDIA-Jetson-IMX708-RPIV3/patches_nano/patches/4.6.4_nano_imx708_v0.1.0.patch` 及 Orin 补丁。当前 Orin 是 CAM1 单模式实验实现，不应误当成三模式增强版。

1. Nano board_setup 校验 0x0000/0x0001 == 0x0301。Orin 已改为 0x0016/0x0017 == 0x0708，0x0000 只打印 module variant。Raspberry Pi 官方驱动也以 0x0016 的 0x0708 为芯片 ID。修正身份校验时应保留 module variant 输出，避免因模块版本差异误拒绝。
2. Nano 使用 REGCACHE_RBTREE，common 表内执行 0x0103 软件复位，没有清缓存；Orin 在复位后 regcache_drop_region(0, 0xffff)。断电/复位后缓存与硬件不一致是重复开流风险，不能只凭普通 regmap_read 回读宣称写入成功。
3. Nano DT 三模式都是 discontinuous_clk=yes，但驱动/表未显式写 0x3220。Orin 根据 DT 写 0x3220=1。应打印并使两端一致。
4. Nano 三模式表已有 0x030e=1、0x030f=0x2c（450 MHz link）。Orin 将其独立为 link 表，DT 用 180 MHz 等效传输像素率与等效行长 4732。Nano DT 595.2/585.6/566.4 MHz 是内部时序像素率，不能单独换成 180 MHz 而不同比例调整行长，否则帧率/曝光计算改变。应先回读 PLL、行帧长并核算，再研究 Tegra210 对速率参数的使用，不能直接复制 Tegra234 参数。
5. Nano 现有 write_reg 错误输出不覆盖 regmap_util_write_table_8 内部批量写。应给 common/mode/start/stop 表分别增加阶段名和返回码；若失败，再启用逐寄存器调试路径定位具体地址。

官方交叉核对：https://raw.githubusercontent.com/raspberrypi/linux/rpi-6.6.y/drivers/media/i2c/imx708.c

## 下一轮操作顺序

1. 在构建机器核对待安装 DEB 内 Image、配置、符号和 SHA256；板端备份现有 Image/extlinux/initrd/模块，部署来自同一构建的镜像与模块。保留可选择的原内核恢复入口。当前两条 extlinux entry 都引用 /boot/Image，只复制该文件会同时影响两条入口，应先准备独立备份路径。
2. 重启后立即核对 uname 构建标识、/proc/config.gz、/proc/kallsyms 和 `/sys/bus/i2c/drivers/imx708`；不能以 modinfo 或包版本作为验收。用 SHA256 验证板端文件等于实际部署文件。
3. 首轮只跑 probe/board_setup：确认 GPIO 接管、PWDN 低脉冲后拉高、等待 300 ms、芯片 ID 和 fine integration time，再注册 subdev。若 I²C 失败，才沿返回码、供电/复位/总线追查。
4. probe 成功后，用 `v4l2-ctl --list-devices`、`--list-formats-ext`、`--list-ctrls` 确认实际节点与格式；先测试单一低帧率 RAW 模式，带 timeout 采集少量帧并检查文件非空和帧数。之后再验证三模式、重复开关流、Argus 和对焦。

## 日志插入设计（下一次构建）

| 位置 | 建议输出 | 区分的问题 |
| --- | --- | --- |
| imx708_probe 入口 | 驱动修订号、adapter nr/name、addr、DT full_name | 新 Image 是否执行、DT 匹配 |
| tegracam_device_register 前后 | 阶段、err（包括 -EPROBE_DEFER） | DT/供电资源/框架注册 |
| parse_dt / power_get | reset GPIO、各 regulator 名称/有无、gpio_request 返回值、方向 | GPIO/provider/资源 |
| power_on/off | 进入/退出、PWDN 写前后读取、电源步骤及 err、等待时间 | 时序和电平 |
| read_reg 失败 | 寄存器地址、I²C bus/address、err | 精确失败读取 |
| board_setup | 芯片 ID 0x0016、module ID 0x0000、fine integration、各步 err、清理 err | ACK 与型号校验 |
| set_mode | mode/mode_prop_idx、分辨率、DT pix_clk/line_length、各表名及 err | 模式与表编程 |
| start/stop | 进入/返回、err，开流后真实硬件回读 | 是否真正启动 |
| 增益/曝光/帧率 | 请求值、裁剪后值、寄存器计算值、err（dev_dbg） | 控制计算 |

开流回读至少包括 0x0100、0x0112/13、0x0114、0x3220、0x030e/f、0x0340/41、0x0342/43、0x034c-f。诊断回读应绕开缓存。Orin 使用的 regmap_read_bypassed 在此 4.9 头文件不存在；可在已串行化的诊断区用 regcache_cache_bypass(true) → regmap_read → 无条件恢复 false，或明确受控的原始 I²C 读取。不要直接复制新内核 API，也不要因诊断读取失败改变原开流返回值。

probe 里关键阶段使用 dev_info/dev_err 常开；逐寄存器和控制值用 dev_dbg 或 trace_power/trace_stream 参数控制，避免每帧刷屏。Linux 4.9 DT 日志使用兼容格式（例如 np->full_name），不要盲用新内核格式/API。

正确内核启动后可临时启用已有动态日志：

```sh
sudo sh -c 'echo "file imx708.c +p" > /sys/kernel/debug/dynamic_debug/control'
sudo dmesg -w
```

需要串口输出 debug 级日志时临时 `sudo dmesg -n 8`；启动早期参数可使用 `loglevel=8 ignore_loglevel log_buf_len=4M`。目前加这些参数也不会凭空产生不存在的驱动日志。VI 超时阶段再启用 Tegra210 的 vi2_fops.c/csi2_fops.c 动态日志，记录 TEGRA_VI_CSI_ERROR_STATUS 和 CIL 状态；不要使用 Orin CamRTC 专属调试步骤。

本轮没有声称相机已修好，也没有执行新镜像的硬件验收。建议先完成镜像一致性修复，再分阶段移植上述诊断和已核实修正。
