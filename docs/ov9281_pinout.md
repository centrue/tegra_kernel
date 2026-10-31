| Pin | SEN0634 规格图 | Jetson Nano CSI | 结果        |
| --: | ----------- | --------------- | --------- |
|   1 | GND         | GND             | ✅         |
|   2 | MDN0        | CSI D0-         | ✅         |
|   3 | MDP0        | CSI D0+         | ✅         |
|   4 | GND         | GND             | ✅         |
|   5 | MDN1        | CSI D1-         | ✅         |
|   6 | MDP1        | CSI D1+         | ✅         |
|   7 | GND         | GND             | ✅         |
|   8 | MCN         | CSI CLK-        | ✅         |
|   9 | MCP         | CSI CLK+        | ✅         |
|  10 | GND         | GND             | ✅         |
|  11 | PWDN        | CAM_PWDN        | ✅         |
|  12 | GPIO（模组侧 NC） | CAM_MCLK   | ⚠️ 物理不连接；模组使用板载时钟 |
|  13 | SIO-C       | CAM_I2C_SCL     | ✅         |
|  14 | SIO-D       | CAM_I2C_SDA     | ✅         |
|  15 | 3.3V        | 3.3V            | ✅         |

补充确认：SEN0634 使用板载 24 MHz 振荡器，Nano 的 CAM_MCLK 只在 R32.7.6
设备树中作为旧 camera framework 所需的非空 clock handle 声明，不代表该时钟
物理送入模组。Pin 11 是 PWDN，低电平保持工作；Pin 12 是模组侧 NC；设备树覆盖层不应把它声明为
`reset-gpios` 或 `pwdn-gpios`。图像接口为 2-lane CSI-2，I2C 地址为 `0x60`。
