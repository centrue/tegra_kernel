// SPDX-License-Identifier: GPL-2.0
/*
 * DFRobot SEN0634 OV9281 camera sensor driver for Jetson Nano / L4T R32.7.6.
 *
 * This driver is deliberately separate from the legacy nvidia,ov9281 driver.
 * The SEN0634 has an onboard 24 MHz oscillator, uses two CSI lanes, and keeps
 * its PWDN input low through the carrier-board GPIO hog.  The clock handle
 * required by camera_common_s_power() is therefore framework-only; no sensor
 * clock is physically connected to Nano CAM_MCLK on this module.
 */

#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/regmap.h>

#include <media/camera_common.h>
#include <media/tegracam_core.h>
#include <media/tegra-v4l2-camera.h>

#include "ov9281_sen0634_mode_tbls.h"

#define OV9281_SEN0634_CHIP_ID_REG_H	0x300a
#define OV9281_SEN0634_CHIP_ID_REG_L	0x300b
#define OV9281_SEN0634_CHIP_ID		0x9281

#define OV9281_SEN0634_STREAM_REG	0x0100
#define OV9281_SEN0634_AGAIN_REG	0x3509
#define OV9281_SEN0634_EXPOSURE_H	0x3500
#define OV9281_SEN0634_EXPOSURE_M	0x3501
#define OV9281_SEN0634_EXPOSURE_L	0x3502
#define OV9281_SEN0634_VTS_H		0x380e
#define OV9281_SEN0634_VTS_L		0x380f
#define OV9281_SEN0634_GROUP_HOLD	0x3208
#define OV9281_SEN0634_TEST_PATTERN	0x5e00

#define OV9281_SEN0634_PIXEL_CLOCK	160000000ULL
#define OV9281_SEN0634_LINE_LENGTH	1530U
#define OV9281_SEN0634_DEFAULT_VTS	1822U
#define OV9281_SEN0634_MIN_VTS	910U
#define OV9281_SEN0634_MAX_VTS	0xffffU
#define OV9281_SEN0634_MIN_COARSE	16U
#define OV9281_SEN0634_COARSE_DIFF	4U
#define OV9281_SEN0634_GAIN_MIN	16U
#define OV9281_SEN0634_GAIN_MAX	255U

/* 0 selects the normal image; non-zero applies 0x5e00 on the next mode set. */
static int test_mode;
module_param(test_mode, int, 0644);
MODULE_PARM_DESC(test_mode,
	"OV9281 SEN0634 internal test pattern, applied on the next stream");

struct ov9281_sen0634 {
	struct tegracam_device *tc_dev;
	struct camera_common_data *s_data;
	struct i2c_client *i2c_client;
	u32 frame_length;
};

static const struct regmap_config ov9281_sen0634_regmap_config = {
	.reg_bits = 16,
	.val_bits = 8,
	.cache_type = REGCACHE_NONE,
};

static int ov9281_sen0634_read_reg(struct camera_common_data *s_data,
					   u16 addr, u8 *val)
{
	unsigned int reg_val;
	int err;

	err = regmap_read(s_data->regmap, addr, &reg_val);
	if (!err)
		*val = reg_val & 0xff;
	return err;
}

static int ov9281_sen0634_write_reg(struct camera_common_data *s_data,
					    u16 addr, u8 val)
{
	return regmap_write(s_data->regmap, addr, val);
}

static int ov9281_sen0634_write_table(struct ov9281_sen0634 *priv,
					      const ov9281_sen0634_reg table[])
{
	return regmap_util_write_table_8(priv->s_data->regmap, table, NULL, 0,
		OV9281_SEN0634_TABLE_WAIT_MS, OV9281_SEN0634_TABLE_END);
}

static struct camera_common_pdata *ov9281_sen0634_parse_dt(
						struct tegracam_device *tc_dev)
{
	struct device *dev = tc_dev->dev;
	struct camera_common_pdata *pdata;
	int err;

	if (!dev->of_node)
		return NULL;

	pdata = devm_kzalloc(dev, sizeof(*pdata), GFP_KERNEL);
	if (!pdata)
		return NULL;

	/* R32 camera_common_s_power() unconditionally uses this clock handle. */
	err = camera_common_parse_clocks(dev, pdata);
	if (err) {
		dev_err(dev, "SEN0634 framework clock is missing: %d\n", err);
		devm_kfree(dev, pdata);
		return NULL;
	}

	return pdata;
}

static int ov9281_sen0634_power_get(struct tegracam_device *tc_dev)
{
	struct camera_common_data *s_data = tc_dev->s_data;
	struct camera_common_power_rail *pw = s_data->power;
	struct camera_common_pdata *pdata = s_data->pdata;

	if (!pdata || !pdata->mclk_name)
		return -EINVAL;

	/* This is a valid Tegra framework clock, not a physical SEN0634 input. */
	pw->mclk = devm_clk_get(tc_dev->dev, pdata->mclk_name);
	if (IS_ERR(pw->mclk)) {
		dev_err(tc_dev->dev, "unable to get framework clock %s\n",
			pdata->mclk_name);
		return PTR_ERR(pw->mclk);
	}

	pw->state = SWITCH_OFF;
	return 0;
}

static int ov9281_sen0634_power_put(struct tegracam_device *tc_dev)
{
	return 0;
}

static int ov9281_sen0634_power_on(struct camera_common_data *s_data)
{
	int err;

	/* PWDN is intentionally not touched: the base DT gpio-hog keeps it low. */
	err = regmap_write(s_data->regmap, 0x0103, 0x01);
	if (err)
		return err;

	usleep_range(10000, 11000);
	s_data->power->state = SWITCH_ON;
	return 0;
}

static int ov9281_sen0634_power_off(struct camera_common_data *s_data)
{
	regmap_write(s_data->regmap, OV9281_SEN0634_STREAM_REG, 0x00);
	usleep_range(1000, 1500);
	s_data->power->state = SWITCH_OFF;
	return 0;
}

static int ov9281_sen0634_set_group_hold(struct tegracam_device *tc_dev,
						 bool hold)
{
	struct ov9281_sen0634 *priv = tegracam_get_privdata(tc_dev);
	int err;

	if (hold)
		return ov9281_sen0634_write_reg(priv->s_data,
			OV9281_SEN0634_GROUP_HOLD, 0x00);

	err = ov9281_sen0634_write_reg(priv->s_data,
		OV9281_SEN0634_GROUP_HOLD, 0x10);
	if (err)
		return err;

	return ov9281_sen0634_write_reg(priv->s_data,
		OV9281_SEN0634_GROUP_HOLD, 0xa0);
}

static int ov9281_sen0634_set_gain(struct tegracam_device *tc_dev, s64 val)
{
	struct ov9281_sen0634 *priv = tegracam_get_privdata(tc_dev);
	u32 gain = clamp_t(u64, val, OV9281_SEN0634_GAIN_MIN,
		OV9281_SEN0634_GAIN_MAX);

	return ov9281_sen0634_write_reg(priv->s_data,
		OV9281_SEN0634_AGAIN_REG, gain);
}

static int ov9281_sen0634_set_frame_rate(struct tegracam_device *tc_dev,
						 s64 val)
{
	struct camera_common_data *s_data = tc_dev->s_data;
	struct ov9281_sen0634 *priv = tegracam_get_privdata(tc_dev);
	const struct sensor_mode_properties *mode =
		&s_data->sensor_props.sensor_modes[s_data->mode_prop_idx];
	u64 frame_length;
	int err;

	if (val <= 0 || !mode->image_properties.line_length ||
		!mode->control_properties.framerate_factor)
		return -EINVAL;

	frame_length = mode->signal_properties.pixel_clock.val *
		(u64)mode->control_properties.framerate_factor;
	frame_length /= (u64)mode->image_properties.line_length * val;
	frame_length = clamp_t(u64, frame_length, OV9281_SEN0634_MIN_VTS,
		OV9281_SEN0634_MAX_VTS);

	err = ov9281_sen0634_write_reg(s_data, OV9281_SEN0634_VTS_H,
		(frame_length >> 8) & 0xff);
	if (err)
		return err;
	err = ov9281_sen0634_write_reg(s_data, OV9281_SEN0634_VTS_L,
		frame_length & 0xff);
	if (!err)
		priv->frame_length = frame_length;
	return err;
}

static int ov9281_sen0634_set_exposure(struct tegracam_device *tc_dev,
						 s64 val)
{
	struct camera_common_data *s_data = tc_dev->s_data;
	struct ov9281_sen0634 *priv = tegracam_get_privdata(tc_dev);
	const struct sensor_mode_properties *mode =
		&s_data->sensor_props.sensor_modes[s_data->mode_prop_idx];
	u64 coarse;
	u32 max_coarse;
	int err;

	if (val <= 0 || !mode->image_properties.line_length ||
		!mode->control_properties.exposure_factor)
		return -EINVAL;

	/* val is in microseconds; exposure_factor is 1,000,000. */
	coarse = (u64)val * mode->signal_properties.pixel_clock.val;
	coarse /= mode->image_properties.line_length;
	coarse /= mode->control_properties.exposure_factor;
	coarse *= 16; /* OV9281 coarse integration unit is 1/16 line. */

	max_coarse = (priv->frame_length > OV9281_SEN0634_COARSE_DIFF ?
		(priv->frame_length - OV9281_SEN0634_COARSE_DIFF) : 1) * 16;
	coarse = clamp_t(u64, coarse, OV9281_SEN0634_MIN_COARSE, max_coarse);

	err = ov9281_sen0634_write_reg(s_data, OV9281_SEN0634_EXPOSURE_H,
		(coarse >> 16) & 0x0f);
	if (err)
		return err;
	err = ov9281_sen0634_write_reg(s_data, OV9281_SEN0634_EXPOSURE_M,
		(coarse >> 8) & 0xff);
	if (err)
		return err;
	return ov9281_sen0634_write_reg(s_data, OV9281_SEN0634_EXPOSURE_L,
		coarse & 0xff);
}

static int ov9281_sen0634_set_mode(struct tegracam_device *tc_dev)
{
	struct ov9281_sen0634 *priv = tegracam_get_privdata(tc_dev);
	int err;

	err = ov9281_sen0634_write_table(priv, ov9281_sen0634_init);
	if (err)
		return err;

	err = ov9281_sen0634_write_table(priv,
		ov9281_sen0634_mode_table[OV9281_SEN0634_MODE_1280X800]);
	if (err)
		return err;

	priv->frame_length = OV9281_SEN0634_DEFAULT_VTS;
	return regmap_write(priv->s_data->regmap, OV9281_SEN0634_TEST_PATTERN,
		test_mode ? 0x80 : 0x00);
}

static int ov9281_sen0634_start_streaming(struct tegracam_device *tc_dev)
{
	struct ov9281_sen0634 *priv = tegracam_get_privdata(tc_dev);

	return ov9281_sen0634_write_table(priv,
		ov9281_sen0634_mode_table[OV9281_SEN0634_MODE_START_STREAM]);
}

static int ov9281_sen0634_stop_streaming(struct tegracam_device *tc_dev)
{
	struct ov9281_sen0634 *priv = tegracam_get_privdata(tc_dev);

	return ov9281_sen0634_write_table(priv,
		ov9281_sen0634_mode_table[OV9281_SEN0634_MODE_STOP_STREAM]);
}

static const int ov9281_sen0634_framerates[] = { 57 };

static const struct camera_common_frmfmt ov9281_sen0634_frmfmt[] = {
	{
		.size = { 1280, 800 },
		.framerates = ov9281_sen0634_framerates,
		.num_framerates = ARRAY_SIZE(ov9281_sen0634_framerates),
		.hdr_en = false,
		.mode = OV9281_SEN0634_MODE_1280X800,
	},
};

static struct camera_common_sensor_ops ov9281_sen0634_common_ops = {
	.numfrmfmts = ARRAY_SIZE(ov9281_sen0634_frmfmt),
	.frmfmt_table = ov9281_sen0634_frmfmt,
	.power_on = ov9281_sen0634_power_on,
	.power_off = ov9281_sen0634_power_off,
	.write_reg = ov9281_sen0634_write_reg,
	.read_reg = ov9281_sen0634_read_reg,
	.parse_dt = ov9281_sen0634_parse_dt,
	.power_get = ov9281_sen0634_power_get,
	.power_put = ov9281_sen0634_power_put,
	.set_mode = ov9281_sen0634_set_mode,
	.start_streaming = ov9281_sen0634_start_streaming,
	.stop_streaming = ov9281_sen0634_stop_streaming,
};

static const u32 ov9281_sen0634_ctrl_cid_list[] = {
	TEGRA_CAMERA_CID_GAIN,
	TEGRA_CAMERA_CID_EXPOSURE,
	TEGRA_CAMERA_CID_FRAME_RATE,
	TEGRA_CAMERA_CID_GROUP_HOLD,
	TEGRA_CAMERA_CID_SENSOR_MODE_ID,
};

static const struct tegracam_ctrl_ops ov9281_sen0634_ctrl_ops = {
	.numctrls = ARRAY_SIZE(ov9281_sen0634_ctrl_cid_list),
	.ctrl_cid_list = ov9281_sen0634_ctrl_cid_list,
	.set_gain = ov9281_sen0634_set_gain,
	.set_exposure = ov9281_sen0634_set_exposure,
	.set_frame_rate = ov9281_sen0634_set_frame_rate,
	.set_group_hold = ov9281_sen0634_set_group_hold,
};

static int ov9281_sen0634_board_setup(struct ov9281_sen0634 *priv)
{
	struct camera_common_data *s_data = priv->s_data;
	struct device *dev = s_data->dev;
	u8 id_high, id_low;
	int err;

	err = camera_common_mclk_enable(s_data);
	if (err)
		return err;

	err = ov9281_sen0634_power_on(s_data);
	if (err)
		goto disable_mclk;

	err = ov9281_sen0634_read_reg(s_data,
		OV9281_SEN0634_CHIP_ID_REG_H, &id_high);
	if (!err)
		err = ov9281_sen0634_read_reg(s_data,
			OV9281_SEN0634_CHIP_ID_REG_L, &id_low);
	if (err)
		goto power_off;

	dev_info(dev, "SEN0634 OV9281 chip ID 0x%02x%02x\n", id_high, id_low);
	if (((id_high << 8) | id_low) != OV9281_SEN0634_CHIP_ID) {
		dev_err(dev, "unexpected SEN0634 OV9281 chip ID\n");
		err = -ENODEV;
	}

power_off:
	ov9281_sen0634_power_off(s_data);
disable_mclk:
	camera_common_mclk_disable(s_data);
	return err;
}

static const struct of_device_id ov9281_sen0634_of_match[] = {
	{ .compatible = "nvidia,ov9281-sen0634" },
	{ },
};
MODULE_DEVICE_TABLE(of, ov9281_sen0634_of_match);

static const struct i2c_device_id ov9281_sen0634_id[] = {
	{ "ov9281_sen0634", 0 },
	{ },
};
MODULE_DEVICE_TABLE(i2c, ov9281_sen0634_id);

static int ov9281_sen0634_probe(struct i2c_client *client,
					const struct i2c_device_id *id)
{
	struct device *dev = &client->dev;
	struct ov9281_sen0634 *priv;
	struct tegracam_device *tc_dev;
	int err;

	if (!IS_ENABLED(CONFIG_OF) || !dev->of_node)
		return -EINVAL;

	priv = devm_kzalloc(dev, sizeof(*priv), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;
	tc_dev = devm_kzalloc(dev, sizeof(*tc_dev), GFP_KERNEL);
	if (!tc_dev)
		return -ENOMEM;

	priv->i2c_client = client;
	tc_dev->client = client;
	tc_dev->dev = dev;
	strncpy(tc_dev->name, "ov9281_sen0634", sizeof(tc_dev->name));
	tc_dev->dev_regmap_config = &ov9281_sen0634_regmap_config;
	tc_dev->sensor_ops = &ov9281_sen0634_common_ops;
	tc_dev->tcctrl_ops = &ov9281_sen0634_ctrl_ops;

	err = tegracam_device_register(tc_dev);
	if (err)
		return err;

	priv->tc_dev = tc_dev;
	priv->s_data = tc_dev->s_data;
	tegracam_set_privdata(tc_dev, priv);

	err = ov9281_sen0634_board_setup(priv);
	if (err) {
		tegracam_device_unregister(tc_dev);
		return err;
	}

	err = tegracam_v4l2subdev_register(tc_dev, true);
	if (err)
		tegracam_device_unregister(tc_dev);
	return err;
}

static int ov9281_sen0634_remove(struct i2c_client *client)
{
	struct camera_common_data *s_data = to_camera_common_data(&client->dev);
	struct ov9281_sen0634 *priv = s_data->priv;

	tegracam_v4l2subdev_unregister(priv->tc_dev);
	tegracam_device_unregister(priv->tc_dev);
	return 0;
}

static struct i2c_driver ov9281_sen0634_i2c_driver = {
	.driver = {
		.name = "ov9281_sen0634",
		.owner = THIS_MODULE,
		.of_match_table = of_match_ptr(ov9281_sen0634_of_match),
	},
	.probe = ov9281_sen0634_probe,
	.remove = ov9281_sen0634_remove,
	.id_table = ov9281_sen0634_id,
};

module_i2c_driver(ov9281_sen0634_i2c_driver);

MODULE_DESCRIPTION("DFRobot SEN0634 OV9281 camera sensor");
MODULE_AUTHOR("Jetpack workspace");
MODULE_LICENSE("GPL v2");
