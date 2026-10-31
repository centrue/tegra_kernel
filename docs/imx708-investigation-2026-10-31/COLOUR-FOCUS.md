# IMX708 色彩与失焦：调查与修复设计

2026-10-31。范围为定位问题并准备下一轮修复。本轮没有替换驱动、安装 ISP override 或写镜头位置。只读取参考、运行短时 Argus 采集和读取硬件寄存器。

## 本机和板端证据

### 色彩：必须区分 RAW 预览与 ISP 输出

此前 `tmp/convert_raw.py` 将 RG10 的每个 2×2 RGGB 单元转换为一个 RGB 像素，减黑电平 64、缩放、gamma；没有 AWB、CCM、镜头阴影校正。那张绿色 PNG 不是 NVIDIA ISP 输出，也不是校准后的颜色。逐通道白平衡与真正的色彩标定是不同步骤，不能靠拉伸直方图证明色准。

本轮实际运行：

```sh
timeout -k 3 18 gst-launch-1.0 -e nvarguscamerasrc sensor-id=0 sensor-mode=1 num-buffers=30 \
  ! 'video/x-raw(memory:NVMM),width=2304,height=1296,format=NV12,framerate=30/1' \
  ! nvvidconv ! 'video/x-raw,format=I420' \
  ! multifilesink location=/tmp/imx708-isp-%02d.i420 max-files=2
```

命令退出 0，收到 EOS，约 2.14 秒，说明这台 R32.7.6 Nano 的 Argus/ISP 通道可用；不能照搬另一个 JetPack 6.2 仓库的“没有 tuning 就不能使用 nvarguscamerasrc”结论。日志中的源配置显示 56 fps，caps 请求 30 fps；本轮没有精确验收实际输出帧率。

板端 `/var/nvidia/nvcam/settings/` 无 camera_overrides.isp；Argus 报：

- `Could not map module to ISP config string`
- `No module data found`
- `No override file found`
- `LSC surface is not based on full res!`

这证明未找到该模块对应的配置/override，仍有默认处理。ISP 输出比原 RAW 预览亮，但目视仍有紫色偏色和失焦。仅凭这张屏幕图不能量化色准；未证实 Bayer 排列错误。DT、V4L2 和转换脚本均声明 RGGB，硬件 0x0101=0，不应先盲目交换 R/B。

本轮样图：`tmp/colour-focus/isp-generic.png`，源 I420 及日志在同目录。PNG 由 ffmpeg 从 I420 解码，仅供基线比较，非后期调色。

### 增益：独立且已证实的控制公式错误

`kernel/nvidia/include/media/imx708.h` 定义 IMX708_ANALOG_GAIN_C0=516，set_gain 使用 `C0 - C0 * gain_factor / val`。这继承自旧 RidgeRun 补丁，不是本轮新增。

Raspberry Pi 官方 libcamera IMX708 helper 使用：

```text
gain_code = 1024 - 1024 / gain
gain = 1024 / (1024 - gain_code)
```

| 请求增益 | 当前寄存器码 | 按官方公式的实际增益 | 正确寄存器码 |
| --- | --- | --- | --- |
| 2x | 258 | 1.337x | 512 |
| 4x | 387 | 1.608x | 768 |
| 8x | 452 | 1.790x | 896 |
| 16x | 484 | 1.896x | 960 |

前次请求 gain=128、gain_factor=16（8x）真实回读为 0x01c4=452，与错误公式吻合。当前 DT 声称到 32x，而 header 上限 978、官方 RPi 驱动上限 960，需要同步控制范围；不能只改 516→1024 就结束。增益错误会影响亮度和 AE 收敛，但不能单独解释所有偏色。

### 对焦：驱动已 probe，但没有可用的 lens 控制路径

- `6-000c` 已绑定 dw9817，DT 的 lens-focus、v4l2_lens drivernode 均存在。
- 实际只有 nvcsi 和 imx708 两个 subdev 节点；media graph 没有 lens。无 focus_absolute 控制节点。
- dw9817_probe 调用 v4l2_async_register_subdev 后成功，不等于被 v4l2_device 接纳；4.9 v4l2-async.c 对无匹配 notifier 的 subdev 放入等待列表也返回 0。
- Tegra210 `vi/graph.c` 只沿 endpoint 图解析，本仓库没有 lens-focus 解析代码。lens 没有图端点，因此仅加 DT phandle 不能自动完成绑定。
- dw9817.c 默认内存位置为 480；真正开流期间直接 I2C_RDWR 读到 0x03/04=0x0200，即 512。CONTROL=0，STATUS=0：芯片响应且当前 active/非忙，不是已证实的掉电或总线失败。0x07 单次读取 EAGAIN，未据此断言硬件故障。
- 驱动只有 `.open/.s_power` 使 power_count 非零并设置 DAC。当前没有 lens 节点，RAW/Argus 采集也没有调用此控制路径。
- `.close` 在最后一个使用者退出时斜坡返回 512 并 standby。因此扫描必须保持 lens FD 打开；一次 set-ctrl 命令结束后再另开 capture 不能可靠保持焦点。

这足以定位软件控制缺口；尚未通过位置扫描证明马达可移动，也未定位最佳焦点。不能把“有默认480常量”当成实际对焦成功。

## 参考资料的正确用法

1. 用户提供的 [Arducam 讨论](https://forum.arducam.com/t/no-issue-details-provided-in-post/10403/) 给出借用 IMX477 override 的经验；该文件自身头部明确来自 IMX477 标定，不是 IMX708 专用。可以备份后短时 A/B，不能作为最终色准依据，尤其不能照搬 LSC。
2. [RidgeRun IMX477 override](https://raw.githubusercontent.com/RidgeRun/NVIDIA-Jetson-IMX477-RPIV3/master/li-camera-calibration-files/camera_overrides.isp) 可用于理解 R32 的文件结构、AWB/CCM/LSC 键，不应直接变成正式 IMX708 标定载荷。
3. `public_sources/ov5647-jetson/docs/isp-tuning.md` 是本轮最重要的方法参考：RAW 灰阶测白点 → ColorChecker 拟合 CCM → FusionLights/CCM override → 用 ISP 输出评估。其文件针对 OV5647 NoIR，Bayer=BGGR、黑电平16；我们的 IMX708 输入为 RGGB、此前暗场约64。需修改 colorchecker.py/read_raw，fit.py 的 --size/--black 不会自动改变 Bayer 拆分。
4. `public_sources/nvidia-jetson-toolkit/README.md` 的灰世界/百分位方法适合 RAW 诊断，不是 ISP 标定。屏幕大面积单色背景不满足灰世界假设。该库存在“fixed focus”等旧说法，与当前 Camera Module 3 的 VCM 硬件不符，应以新说明和硬件验证为准。
5. `/home/liaic/Documents/Orin_Nano/jetson-workspace/orin-kernel/patches/nvidia-jetson-imx708-rpiv3-p3768-csi0.patch` 有 DW9817 平滑运动、I2C trace 等可复用设计。当前 Orin README 是单模式最小实验，不代表其三模式/对焦集成均已验收。`test-imx708-on-target.sh` 仅写 focus control，不拍图评清晰度，也没有保持 lens FD，不能直接作为我们最终焦点验收。
6. 原始参考库还有 imx708.nito，但缺少证明它适配 Nano R32.7.6 的配置/日志；本轮没有安装，也不把二进制名字当作兼容性证明。

官方核对：[IMX708 gain helper](https://raw.githubusercontent.com/raspberrypi/libcamera/main/src/ipa/rpi/cam_helper/cam_helper_imx708.cpp)、[IMX708 registers/ranges](https://raw.githubusercontent.com/raspberrypi/linux/rpi-6.6.y/drivers/media/i2c/imx708.c)。

## 下一轮建议与验收

推荐主要路线：硬件 ISP 输出；保留软件 RAW 对照。先修手动对焦和增益，再标定颜色。先不承诺 PDAF 自动对焦，因为那还需要 PDAF 数据解析、镜头映射及 AF 算法，不是曝光控制或 lens-focus phandle 自动提供的功能。

1. 先给 DW9817 建立真实可访问的 V4L2 lens 节点。为减少首轮迭代成本，可用独立 v4l2_device 的诊断模块，解绑内置 dw9817 后绑定诊断驱动；恢复时卸载模块、重新绑定原驱动。正式源码要明确节点归属和 remove 清理，不能同时混用 async 等待注册与独立注册。此最小路线不声称实现了 Argus AF。
2. 扫描时持有 lens FD 和传感器流，锁曝光/增益，从中点周围粗扫再细扫，记录 DAC 读回、忙状态、中心 ROI 清晰度；比较扫描前/最佳焦点/恢复后的图片。设定位置后等待稳定帧，不能只凭 ACK 成功验收。
3. 修复 gain 公式及 DT 范围，并验证 2/4/8/16x 的硬件读回、固定照明下不饱和区域的 RAW 亮度响应。最小实现以官方 RPi 112..960 的码范围为参考；如要超过16x，需要额外型号/模式依据。
4. 用当前可工作的 Argus 输出做 generic 基线；短时、可恢复地测试候选 override。以日志确认文件被加载，检查 parser/LSC 警告，不能以文件“已复制”为成功证据。
5. 以真实灰卡/ColorChecker 为主要标定对象，在受控光源下测白点和 CCM。屏幕可验证 R/G/B 通道、方向及清晰度，但不能替代反射式色卡的跨光源校准。按 ov5647 仓库的生成/评分流程产出 IMX708 专属文件，不复制其数值。

所有帧、控制请求/读回、ISP配置 SHA256、重启/模块装卸信息和失败码保留在 tmp/colour-focus/。后续编译必须启用现有 ccache；可独立编译时优先模块验证，等待进程返回而不反复轮询。
