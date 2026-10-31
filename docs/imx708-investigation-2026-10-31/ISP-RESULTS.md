# Nano ISP 首轮实测修正（2026-10-31）

运行内核仍为已部署的 #3；本轮只更改 ISP 文本配置与工具，不需要重编译内核。

## 改动和证据

- 用固定焦点688、固定曝光/模拟增益取得 RAW，灰块 R/G=0.43947、B/G=0.60020，白平衡增益约2.275/1/1.666。
- 设置实测 FusionLights={396,900,900,540}，开放参考流程中的AWB约束，关闭不匹配的lux prior。CCM是明确的单位矩阵，不是完整拟合；6500只是标称屏幕标签。
- mode1暗场此前测得约64 DN，本轮实验黑位偏置为64/1023。未生成任何虚构的LSC数据，LSC警告仍存在。
- Nano R32.7.6解析器拒绝FusionInitLight=0；改为4后接收，新增回归测试。加载事件实际为`Found override file [/var/nvidia/nvcam/settings/camera_overrides.isp]`，工具现识别该格式，同时要求出帧且无解析错误。
- 首轮未锁ISP数字增益时白块过曝，未据此宣称校准成功；后续固定ISP数字增益1，在10ms/2x下取得generic和候选对照。
- 直接I420→BT.709/limited-range PNG转换，未对图片进行后期颜色补偿。新的画面去掉大部分紫色底雾，黑阶可区分。

局部中性/黑位诊断：屏幕0级块RGB约(94,74,84)→(9,9,9)，64级块的RGB通道极差14→5。ROI、图像SHA256、曝光和命令保存在`isp/imx708/experiments/screen-validation.json`。拍摄间手机发生平移/转动，分别选择对应块，不能将此宣称为配准的完整色准或ΔE验证。

## 当前板端状态

- 已启用`/var/nvidia/nvcam/settings/camera_overrides.isp`。
- SHA256：224dea93b8cf292fc56dd98fe8475f3fb012b96423a10b745abb72f5f9109cf9。
- 此文件与`isp/imx708/experiments/screen-wb-black.isp`一致。原始状态（无override）标记在`~/imx708-deploy/isp-before-screen-tune/originally-absent`。
- 清除nvcam_cache后重启daemon，实际日志确认Found override file，已再次采图。
- X11预览已恢复：焦点688、曝光20ms、模拟增益4、ISP数字增益1、960×540窗口。SSH会话需持续存在。
- 图片：`tmp/colour-focus/isp-trials/locked-generic/image.png`、`tmp/colour-focus/isp-trials/locked-wb-black/image.png`、`tmp/colour-focus/tuned-final/image.png`；保留原I420、RAW及日志。
- `imx708-camera-tools` DEB包含实验文件供选择，不自动覆盖ISP配置；板端此次启用是显式操作。

## 限制和后续

这是手机屏幕场景的中性色/黑位实验，并非标定的实体ColorChecker，不能证明自然物体、不同光源、其他模式下的完整色彩准确性。全CCM、真正LSC、曝光策略及其他分辨率仍需独立验证。当前仍可能有噪声、暗角、余留色偏。RAW和Argus切换前需要释放daemon的占用；本轮遇到EBUSY后停daemon才成功取得RAW。

回退时先停预览，删除实验override和nvcam_cache_*.bin，再重启nvargus-daemon，即回到本轮之前的无override状态。保留serial_no_*.bin。若用户后来装入了别的原始override，应从备份恢复而不是删除。
