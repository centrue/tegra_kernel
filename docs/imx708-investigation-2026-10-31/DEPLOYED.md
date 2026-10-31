# 实际部署与验证结果

本次安装指定 out/ov5647-builtin/deb 下的 DEB 并重启，确认新内核运行。第一次真实失败是读取模块变体 ID 0x0000 后，被硬编码的 0x0301 校验拒绝。将身份校验改为 0x0016/17 的 0x0708，并增加 probe、GPIO request、上电电平、I²C 失败地址、框架/board_setup/subdev 返回码日志后，通过 ccache 增量构建、重新安装 kernel DEB，再次重启。

最终板端 /boot/Image SHA256：`c56e86000d3e0dca96c042f2e70004b265417db9282f05f8c1e8b332cd536754`，与本地 DEB staging Image 相同。运行 `#2 SMP PREEMPT Mon Aug 31 06:27:00 UTC 2026`。时间戳由现有构建脚本固定，不能只看日期区分本次修订。

`/dev/video0` 为真实字符设备，V4L2 名称为 `vi-output, imx708 6-001a`；media graph 的 sensor → nvcsi → vi 已绑定。GPIO 151 request=0，拉高读回 1，chip ID=0x0708，board_setup/subdev 注册均返回 0。未修改对焦配置、CSI 参数或 regcache；本次无需这些改动即可注册节点和采集。

实际测试：

- 2304×1296 首次采集 5 帧，约 56 fps，返回 0。
- 4608×2592、1536×864 各采集 5 帧，再切回 2304×1296，命令均返回 0。切换后的短采样有时间戳异常，不能视为稳定帧率验收。
- 2304×1296 设置 override_enable=1、30 fps、20 ms 曝光、gain=128，跳过 5 帧后采集 20 帧，持续约 30 fps；另一次 100 帧采集返回 0。
- 开流时直接 I2C_RDWR 读取硬件（绕过 regcache），确认 0x0100=1、RAW10/2 lanes、输出 2304×1296、0x3220=1、450 MHz link，曝光 0x0508、gain 0x01c4、frame length 0x09be、line length 0x1e90。工具只读取寄存器，不写传感器。

局限：RAW 像素接近黑电平，20 ms 曝光样本 min=61/max=68/mean=64.05；前一次切模式的 5 帧中有一帧全零。已询问镜头盖和照明情况，未据此宣称图像质量正常。启动日志还存在 nvgpu 错误，未在本任务中修改 GPU。Argus、对焦、长时间稳定性未验收。

构建启用 `/usr/sbin/ccache`、目录 `/home/liaic/ccache/jetson-kernel`；构建退出 0，三个现有 IMX708 静态检查通过。日志保存在 runtime/（含首次失败、安装、采集、真实寄存器和最终节点核验）。板端日志在 `~/imx708-deploy/`。原 Image/initrd/extlinux 备份在 `/boot/imx708-backup/`；安装脚本会将 DEFAULT 改回 primary，部署后已经恢复 JetsonIO。

可复用的 RAW 采集命令：

```sh
timeout -k 3 20 v4l2-ctl -d /dev/video0 \
  --set-fmt-video=width=2304,height=1296,pixelformat=RG10 \
  --set-ctrl=sensor_mode=1,bypass_mode=0,override_enable=1,frame_rate=30000000,exposure=20000,gain=128 \
  --stream-mmap --stream-skip=5 --stream-count=20 --stream-to=/tmp/imx708.raw --verbose
```
