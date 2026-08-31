subdir-ccflags-y += -Werror

ccflags-y += -I$(srctree)/drivers/media/platform/tegra
ccflags-y += -I$(srctree)/drivers/video/tegra/camera

vc-mipi-ccflags := -DVC_MIPI_JETSON_NANO -DVC_MIPI_L4T_32_7_6
CFLAGS_vc_mipi_camera.o += $(vc-mipi-ccflags)
CFLAGS_vc_mipi_core.o += $(vc-mipi-ccflags)
CFLAGS_vc_mipi_modules.o += $(vc-mipi-ccflags)

obj-$(CONFIG_VIDEO_IMX185) += imx185.o
obj-$(CONFIG_VIDEO_IMX185) += imx185_v1.o
obj-$(CONFIG_VIDEO_IMX477) += imx477.o
obj-$(CONFIG_VIDEO_IMX219) += imx219.o
obj-$(CONFIG_VIDEO_IMX708) += imx708.o
obj-$(CONFIG_VIDEO_DW9817) += dw9817.o
obj-$(CONFIG_VIDEO_OV5647) += ov5647.o
obj-$(CONFIG_VIDEO_IMX268) += imx268.o
obj-$(CONFIG_VIDEO_IMX274) += imx274.o
obj-$(CONFIG_VIDEO_IMX318) += imx318.o
obj-$(CONFIG_VIDEO_LC898212) += lc898212.o
obj-$(CONFIG_VIDEO_OV5693) += ov5693.o
obj-$(CONFIG_VIDEO_OV9281) += ov9281.o
obj-$(CONFIG_VIDEO_OV9281_SEN0634) += ov9281_sen0634.o
obj-$(CONFIG_NV_VIDEO_VC_MIPI) += vc_mipi_camera.o vc_mipi_core.o vc_mipi_modules.o
obj-$(CONFIG_VIDEO_OV10823) += ov10823.o
obj-$(CONFIG_VIDEO_OV23850) += ov23850.o
obj-$(CONFIG_I2C_IOEXPANDER_PCA9570) += pca9570.o
obj-$(CONFIG_VIDEO_TC358840) += tc358840.o
obj-$(CONFIG_VIDEO_LT6911UXC) +=lt6911uxc.o
obj-$(CONFIG_I2C_IOEXPANDER_SER_MAX9295) += max9295.o
obj-$(CONFIG_I2C_IOEXPANDER_DESER_MAX9296) += max9296.o
obj-$(CONFIG_VIDEO_IMX390) += imx390.o
