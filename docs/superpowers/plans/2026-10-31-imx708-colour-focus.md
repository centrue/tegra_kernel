# IMX708 Colour and Focus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供可验证的手动对焦、正确模拟增益，以及有实测依据的 IMX708 ISP 色彩处理。

**Architecture:** 先以独立 lens V4L2 节点和持续持有的 FD 验证 VCM；修复增益后，以 Argus 为主要输出、RAW 为标定输入，按灰卡/色卡实测生成 override。诊断模块用于缩短迭代，不宣称实现 Argus AF。

**Tech Stack:** Jetson Nano A02、L4T R32.7.6、Linux 4.9 tegracam/V4L2、DW9817、Python/NumPy、GStreamer Argus、ccache。

**Spec:** `docs/imx708-investigation-2026-10-31/COLOUR-FOCUS.md`（调查和建议设计；本轮仅准备，不实施驱动变更）。

## Global Constraints

- 保留可工作的 /dev/video0；待测版本使用独立模块身份，不覆盖内置驱动符号。
- 编译启用 `/home/liaic/ccache/jetson-kernel`；可单模块验证时不全量重编译，等待进程返回，不反复轮询。
- 不把 OV5647 NoIR 或 IMX477 的校准数值直接作为正式 IMX708 结果。
- 保留测试原图、原 RAW、配置 SHA256 和完整返回码；控制失败不能当作有效评分样本。

## Review Focus

- 未绑定或未插接的镜头：video0 继续可用，调焦工具明确报错，不伪造成功。
- lens FD 生命周期：采集前关闭导致512回退，测试应覆盖持有与关闭行为。
- 过曝、暗场、运动/屏幕频闪：不纳入最佳焦点评分或色卡拟合。
- 增益端点与模式切换：控制范围、寄存器码和实际响应一致。
- 无效 ISP 语法、缓存、不同模组：有日志、恢复旧配置、不把默认处理误判为新文件生效。

## Task 1：建立并验证镜头控制路径

**Files:** `kernel/nvidia/drivers/media/i2c/dw9817.c`；新增 `tools/imx708/focus-scan.py`、`tools/imx708/check-lens.sh`；诊断模块构建目录放 `tmp/colour-focus/module/`。

**Interfaces:** 按 sysfs name 寻找 lens 节点，使用 V4L2_CID_FOCUS_ABSOLUTE=0..1023；`focus-scan.py --lens DEVICE --video DEVICE --output DIR` 在同一 lens FD 生命周期内控制和采集。

- [ ] 运行 check-lens：按名称枚举 focus_absolute 节点。当前应失败（只有 sensor/CSI subdev），保存基线。
- [ ] 从现有 DW9817 实现构建独立诊断模块：唯一 i2c_driver 名称、独立 v4l2_device 注册和 subdev_nodes；完整失败清理和 remove，对内置驱动先解绑再测试，避免双重拥有。
- [ ] 添加可控日志：open/close、power_count、目标DAC、实际DAC、控制寄存器、忙状态及返回码。诊断读取不能隐藏原写入错误。
- [ ] 加载后复跑 check-lens；预期有可设置的 focus_absolute，拔除/未绑定时报明确错误，video0 保持正常。
- [ ] 实现扫描器：保持 lens FD 打开，固定曝光/增益，先以480为中心按32步长探索可用区，再在最高分附近按4细扫；排除空帧、饱和和光照突变。评分用固定中心ROI梯度能量并记录均值、每位置多帧方差。
- [ ] 检查设置DAC读回一致；同一静态场景下最佳位置清晰度和目视文字边缘优于512基线；关闭最后一个FD后确认回退512，再打开验证位置重施加。只有I2C ACK不算通过。
- [ ] 卸载诊断模块、重绑原驱动，确认video0仍可采集；将验证后的节点注册方案合回正式DW9817代码，复跑同样硬件测试。

## Task 2：修正模拟增益模型

**Files:** `kernel/nvidia/include/media/imx708.h`、`kernel/nvidia/drivers/media/i2c/imx708.c`、`hardware/nvidia/platform/t210/porg/kernel-dts/tegra210-p3448-common-imx708.dts`；新增 `tools/imx708/check-gain.py`。

**Interfaces:** V4L2 gain 为DT gain_factor缩放的倍数；工具保持采集，设置请求后从硬件0x0204/05读取代码，不读regcache。

- [ ] 先验证2/4/8/16x映射512/768/896/960；当前驱动应失败，保存结果。
- [ ] 将C0改为1024，按官方112..960范围裁剪。与DT控制范围同步；保留factor16时建议min_gain_val=18（1.125x），max_gain_val=256（16x），三模式一致，默认值同样合法。1.125x对应码114，不以112冒充精确1.125x。
- [ ] 添加gain请求、裁剪后的码、返回码的dev_dbg日志。验证低/高边界及模式切换；不修改与本任务无关的CSI参数。
- [ ] ccache编译待测模块或Image并部署；真实读取映射全部通过。固定照明/曝光下比较不饱和ROI响应，不以单一图像整体均值替代寄存器验证。

## Task 3：建立可复现的ISP标定工具

**Files:** 新增 `tools/imx708/capture-isp.sh`、`tools/imx708/calibrate.py`、`tools/imx708/score.py`、`isp/imx708/README.md`；参考 `public_sources/ov5647-jetson/tools/isp/`，保留原作者许可证和来源。

**Interfaces:** 统一记录size、Bayer、bit depth、stride、black、exposure、gain、focus、光源、配置SHA256。标定输出JSON包含白点、WB gains、CCM和评分；ISP生成器由该JSON产出文本。

- [ ] 用合成RGGB色块验证拆分：R/Gr/Gb/B不互换、stride处理正确、空帧/坏尺寸报错。OV5647原版BGGR/black16必须在该测试中失败；IMX708缺省RGGB/black64可由暗场实测覆盖。
- [ ] 先保存generic Argus基线；捕获工具使用trap备份和恢复override，清理nvcam_cache_*.bin，重启daemon，记录加载/解析日志与退出码；配置错误注入应恢复旧文件并报错。
- [ ] 拍灰卡和ColorChecker，锁定经Task1验证的焦点；测暗场、灰阶和18色块，避免过曝。仅屏幕场景不生成正式跨光源标定。
- [ ] 将ov5647流程改为显式RGGB/尺寸/黑电平参数，拟合白点和CCM，写FusionLights/色温矩阵；不能复制OV5647的AWB、CCM和LSC数值。先不引入未知IMX708 LSC。
- [ ] 对同场景generic/候选ISP输出评分（灰阶中性、ΔE、暗角、剪裁、噪声）；独立验证光源和场景也改善，才把文件纳入正式发布；记录未覆盖条件。

## 完成条件

节点和运动均有证据、图像清晰度明显改善；增益硬件映射正确；ISP配置确实加载且相同条件下色准评分改善；原始采集和结果可复现。自动/PDAF对焦与全光源色准不在此最小修复的完成声明内。
