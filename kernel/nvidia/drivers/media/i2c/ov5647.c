// SPDX-License-Identifier: GPL-2.0
/*
 * ov5647.c - OmniVision OV5647 sensor driver for Tegra210
 *
 * Tegra camera integration follows NVIDIA's tegracam v2 sensor drivers.
 * Sensor register definitions and mode timings follow the upstream OV5647
 * driver maintained by the Raspberry Pi kernel project.
 */

#include <linux/clk.h>
#include <linux/gpio.h>
#include <linux/i2c.h>
#include <linux/math64.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_device.h>
#include <linux/of_gpio.h>
#include <linux/regmap.h>
#include <linux/regulator/consumer.h>
#include <linux/slab.h>

#include <media/tegra_v4l2_camera.h>
#include <media/tegracam_core.h>

#include "ov5647_mode_tbls.h"

#define OV5647_CHIP_ID_HIGH_REG		0x300a
#define OV5647_CHIP_ID_LOW_REG		0x300b
#define OV5647_CHIP_ID_HIGH		0x56
#define OV5647_CHIP_ID_LOW		0x47

#define OV5647_GAIN_HIGH_REG		0x350a
#define OV5647_GAIN_LOW_REG		0x350b
#define OV5647_EXPOSURE_HIGH_REG	0x3500
#define OV5647_EXPOSURE_MID_REG		0x3501
#define OV5647_EXPOSURE_LOW_REG		0x3502
#define OV5647_FRAME_LENGTH_HIGH_REG	0x380e
#define OV5647_FRAME_LENGTH_LOW_REG	0x380f
#define OV5647_GROUP_ACCESS_REG		0x3208
#define OV5647_GROUP_HOLD_START		0x00
#define OV5647_GROUP_HOLD_END		0x10
#define OV5647_GROUP_HOLD_LAUNCH	0xa0

#define OV5647_MIN_GAIN			16U
#define OV5647_MAX_GAIN			1023U
#define OV5647_MIN_COARSE_EXPOSURE	4U
#define OV5647_MAX_COARSE_DIFF		4U
#define OV5647_MIN_VBLANK		24U
#define OV5647_MAX_FRAME_LENGTH		0x7fffU

static const struct of_device_id ov5647_of_match[] = {
	{ .compatible = "nvidia,ov5647" },
	{ }
};
MODULE_DEVICE_TABLE(of, ov5647_of_match);

static const u32 ov5647_ctrl_cid_list[] = {
	TEGRA_CAMERA_CID_GAIN,
	TEGRA_CAMERA_CID_EXPOSURE,
	TEGRA_CAMERA_CID_FRAME_RATE,
	TEGRA_CAMERA_CID_SENSOR_MODE_ID,
};

struct ov5647 {
	struct i2c_client *i2c_client;
	struct v4l2_subdev *subdev;
	u32 frame_length;
	struct camera_common_data *s_data;
	struct tegracam_device *tc_dev;
};

static const struct regmap_config ov5647_regmap_config = {
	.reg_bits = 16,
	.val_bits = 8,
	.cache_type = REGCACHE_RBTREE,
	.use_single_rw = true,
};

static inline int ov5647_read_reg(struct camera_common_data *s_data,
	u16 addr, u8 *val)
{
	u32 reg_val;
	int err;

	err = regmap_read(s_data->regmap, addr, &reg_val);
	if (!err)
		*val = reg_val & 0xff;

	return err;
}

static inline int ov5647_write_reg(struct camera_common_data *s_data,
	u16 addr, u8 val)
{
	int err;

	err = regmap_write(s_data->regmap, addr, val);
	if (err)
		dev_err(s_data->dev,
			"i2c write failed: register 0x%04x = 0x%02x (%d)\n",
			addr, val, err);

	return err;
}

static int ov5647_write_table(struct ov5647 *priv,
	const ov5647_reg table[])
{
	return regmap_util_write_table_8(priv->s_data->regmap, table, NULL, 0,
		OV5647_TABLE_WAIT_MS, OV5647_TABLE_END);
}

static int ov5647_set_group_hold(struct tegracam_device *tc_dev, bool val)
{
	struct camera_common_data *s_data = tc_dev->s_data;
	int err;

	if (val)
		return ov5647_write_reg(s_data, OV5647_GROUP_ACCESS_REG,
			OV5647_GROUP_HOLD_START);

	err = ov5647_write_reg(s_data, OV5647_GROUP_ACCESS_REG,
		OV5647_GROUP_HOLD_END);
	if (err)
		return err;

	return ov5647_write_reg(s_data, OV5647_GROUP_ACCESS_REG,
		OV5647_GROUP_HOLD_LAUNCH);
}

static int ov5647_set_gain(struct tegracam_device *tc_dev, s64 val)
{
	struct camera_common_data *s_data = tc_dev->s_data;
	const struct sensor_mode_properties *mode =
		&s_data->sensor_props.sensor_modes[s_data->mode_prop_idx];
	u16 gain;
	int err;

	if (val < mode->control_properties.min_gain_val)
		val = mode->control_properties.min_gain_val;
	else if (val > mode->control_properties.max_gain_val)
		val = mode->control_properties.max_gain_val;

	gain = clamp_t(u16, val, OV5647_MIN_GAIN, OV5647_MAX_GAIN);
	err = ov5647_write_reg(s_data, OV5647_GAIN_HIGH_REG,
		(gain >> 8) & 0x03);
	if (err)
		return err;

	return ov5647_write_reg(s_data, OV5647_GAIN_LOW_REG, gain & 0xff);
}

static int ov5647_set_frame_rate(struct tegracam_device *tc_dev, s64 val)
{
	struct camera_common_data *s_data = tc_dev->s_data;
	struct ov5647 *priv = tegracam_get_privdata(tc_dev);
	const struct sensor_mode_properties *mode =
		&s_data->sensor_props.sensor_modes[s_data->mode_prop_idx];
	u64 numerator;
	u64 denominator;
	u32 min_frame_length;
	u32 frame_length;
	int err;

	if (val <= 0)
		return -EINVAL;

	numerator = mode->signal_properties.pixel_clock.val *
		(u64)mode->control_properties.framerate_factor;
	denominator = (u64)mode->image_properties.line_length * (u64)val;
	frame_length = div64_u64(numerator, denominator);
	min_frame_length = mode->image_properties.height + OV5647_MIN_VBLANK;
	frame_length = clamp_t(u32, frame_length, min_frame_length,
		OV5647_MAX_FRAME_LENGTH);

	err = ov5647_write_reg(s_data, OV5647_FRAME_LENGTH_HIGH_REG,
		(frame_length >> 8) & 0xff);
	if (err)
		return err;
	err = ov5647_write_reg(s_data, OV5647_FRAME_LENGTH_LOW_REG,
		frame_length & 0xff);
	if (err)
		return err;

	priv->frame_length = frame_length;
	return 0;
}

static int ov5647_set_exposure(struct tegracam_device *tc_dev, s64 val)
{
	struct camera_common_data *s_data = tc_dev->s_data;
	struct ov5647 *priv = tegracam_get_privdata(tc_dev);
	const struct sensor_mode_properties *mode =
		&s_data->sensor_props.sensor_modes[s_data->mode_prop_idx];
	u64 numerator;
	u64 denominator;
	u32 max_coarse;
	u32 coarse;
	int err;

	if (val <= 0)
		return -EINVAL;

	numerator = (u64)val * mode->signal_properties.pixel_clock.val;
	denominator = (u64)mode->control_properties.exposure_factor *
		mode->image_properties.line_length;
	coarse = div64_u64(numerator, denominator);
	max_coarse = priv->frame_length > OV5647_MAX_COARSE_DIFF ?
		priv->frame_length - OV5647_MAX_COARSE_DIFF :
		OV5647_MIN_COARSE_EXPOSURE;
	coarse = clamp_t(u32, coarse, OV5647_MIN_COARSE_EXPOSURE,
		max_coarse);

	/* OV5647 stores whole-line exposure in bits 19:4. */
	err = ov5647_write_reg(s_data, OV5647_EXPOSURE_HIGH_REG,
		(coarse >> 12) & 0x0f);
	if (err)
		return err;
	err = ov5647_write_reg(s_data, OV5647_EXPOSURE_MID_REG,
		(coarse >> 4) & 0xff);
	if (err)
		return err;

	return ov5647_write_reg(s_data, OV5647_EXPOSURE_LOW_REG,
		(coarse & 0x0f) << 4);
}

static struct tegracam_ctrl_ops ov5647_ctrl_ops = {
	.numctrls = ARRAY_SIZE(ov5647_ctrl_cid_list),
	.ctrl_cid_list = ov5647_ctrl_cid_list,
	.set_gain = ov5647_set_gain,
	.set_exposure = ov5647_set_exposure,
	.set_frame_rate = ov5647_set_frame_rate,
	.set_group_hold = ov5647_set_group_hold,
};

static int ov5647_power_on(struct camera_common_data *s_data)
{
	struct camera_common_power_rail *pw = s_data->power;
	struct camera_common_pdata *pdata = s_data->pdata;
	struct device *dev = s_data->dev;
	int err;

	if (pdata && pdata->power_on) {
		err = pdata->power_on(pw);
		if (!err)
			pw->state = SWITCH_ON;
		return err;
	}

	if (gpio_is_valid(pw->reset_gpio))
		gpio_set_value_cansleep(pw->reset_gpio, 0);

	usleep_range(10, 20);
	if (pw->avdd) {
		err = regulator_enable(pw->avdd);
		if (err)
			goto fail;
	}
	if (pw->iovdd) {
		err = regulator_enable(pw->iovdd);
		if (err)
			goto disable_avdd;
	}
	if (pw->dvdd) {
		err = regulator_enable(pw->dvdd);
		if (err)
			goto disable_iovdd;
	}

	usleep_range(10, 20);
	if (gpio_is_valid(pw->reset_gpio))
		gpio_set_value_cansleep(pw->reset_gpio, 1);

	/* OV5647 requires at least 20 ms after power-down deassertion. */
	msleep(20);
	pw->state = SWITCH_ON;
	return 0;

disable_iovdd:
	if (pw->iovdd)
		regulator_disable(pw->iovdd);
disable_avdd:
	if (pw->avdd)
		regulator_disable(pw->avdd);
fail:
	dev_err(dev, "power-on sequence failed: %d\n", err);
	return err;
}

static int ov5647_power_off(struct camera_common_data *s_data)
{
	struct camera_common_power_rail *pw = s_data->power;
	struct camera_common_pdata *pdata = s_data->pdata;
	int err = 0;

	if (pdata && pdata->power_off) {
		err = pdata->power_off(pw);
		if (err)
			return err;
	} else {
		if (gpio_is_valid(pw->reset_gpio))
			gpio_set_value_cansleep(pw->reset_gpio, 0);
		usleep_range(10, 20);
		if (pw->dvdd)
			regulator_disable(pw->dvdd);
		if (pw->iovdd)
			regulator_disable(pw->iovdd);
		if (pw->avdd)
			regulator_disable(pw->avdd);
	}

	pw->state = SWITCH_OFF;
	return 0;
}

static int ov5647_power_put(struct tegracam_device *tc_dev)
{
	struct camera_common_power_rail *pw = tc_dev->s_data->power;

	if (!pw)
		return -EFAULT;

	if (pw->dvdd)
		devm_regulator_put(pw->dvdd);
	if (pw->avdd)
		devm_regulator_put(pw->avdd);
	if (pw->iovdd)
		devm_regulator_put(pw->iovdd);
	pw->dvdd = NULL;
	pw->avdd = NULL;
	pw->iovdd = NULL;

	if (gpio_is_valid(pw->reset_gpio))
		gpio_free(pw->reset_gpio);

	return 0;
}

static int ov5647_power_get(struct tegracam_device *tc_dev)
{
	struct device *dev = tc_dev->dev;
	struct camera_common_data *s_data = tc_dev->s_data;
	struct camera_common_power_rail *pw = s_data->power;
	struct camera_common_pdata *pdata = s_data->pdata;
	struct clk *parent;
	int err;

	if (!pdata)
		return -EFAULT;

	if (pdata->mclk_name) {
		pw->mclk = devm_clk_get(dev, pdata->mclk_name);
		if (IS_ERR(pw->mclk))
			return PTR_ERR(pw->mclk);
		if (pdata->parentclk_name) {
			parent = devm_clk_get(dev, pdata->parentclk_name);
			if (!IS_ERR(parent))
				clk_set_parent(pw->mclk, parent);
		}
	}

	if (pdata->regulators.avdd) {
		err = camera_common_regulator_get(dev, &pw->avdd,
			pdata->regulators.avdd);
		if (err)
			return err;
	}
	if (pdata->regulators.iovdd) {
		err = camera_common_regulator_get(dev, &pw->iovdd,
			pdata->regulators.iovdd);
		if (err)
			return err;
	}
	if (pdata->regulators.dvdd) {
		err = camera_common_regulator_get(dev, &pw->dvdd,
			pdata->regulators.dvdd);
		if (err)
			return err;
	}

	pw->reset_gpio = pdata->reset_gpio;
	err = gpio_request(pw->reset_gpio, "ov5647_pwdn");
	if (err)
		return err;

	pw->state = SWITCH_OFF;
	return 0;
}

static struct camera_common_pdata *ov5647_parse_dt(
	struct tegracam_device *tc_dev)
{
	struct device *dev = tc_dev->dev;
	struct device_node *np = dev->of_node;
	struct camera_common_pdata *pdata;
	int gpio;

	if (!np || !of_match_device(ov5647_of_match, dev))
		return NULL;

	pdata = devm_kzalloc(dev, sizeof(*pdata), GFP_KERNEL);
	if (!pdata)
		return NULL;

	gpio = of_get_named_gpio(np, "reset-gpios", 0);
	if (gpio == -EPROBE_DEFER)
		return ERR_PTR(-EPROBE_DEFER);
	if (!gpio_is_valid(gpio)) {
		dev_err(dev, "reset-gpios is missing or invalid\n");
		return NULL;
	}
	pdata->reset_gpio = gpio;

	if (of_property_read_string(np, "mclk", &pdata->mclk_name))
		dev_dbg(dev, "mclk property absent; assuming external clock\n");
	of_property_read_string(np, "parent-clk", &pdata->parentclk_name);
	of_property_read_string(np, "avdd-reg", &pdata->regulators.avdd);
	of_property_read_string(np, "iovdd-reg", &pdata->regulators.iovdd);
	of_property_read_string(np, "dvdd-reg", &pdata->regulators.dvdd);
	pdata->has_eeprom = of_property_read_bool(np, "has-eeprom");

	return pdata;
}

static u32 ov5647_mode_default_frame_length(u32 mode)
{
	switch (mode) {
	case OV5647_MODE_2592X1944_15FPS:
		return 0x07b0;
	case OV5647_MODE_1920X1080_30FPS:
		return 0x0450;
	case OV5647_MODE_1296X972_30FPS:
		return 0x059b;
	case OV5647_MODE_640X480_60FPS:
		return 0x01f8;
	default:
		return 0x07b0;
	}
}

static int ov5647_set_mode(struct tegracam_device *tc_dev)
{
	struct ov5647 *priv = tegracam_get_privdata(tc_dev);
	struct camera_common_data *s_data = tc_dev->s_data;
	int err;

	err = ov5647_write_table(priv,
		ov5647_mode_table[OV5647_MODE_COMMON]);
	if (err)
		return err;
	err = ov5647_write_table(priv, ov5647_mode_table[s_data->mode]);
	if (err)
		return err;

	priv->frame_length = ov5647_mode_default_frame_length(s_data->mode);
	return 0;
}

static int ov5647_start_streaming(struct tegracam_device *tc_dev)
{
	struct ov5647 *priv = tegracam_get_privdata(tc_dev);

	return ov5647_write_table(priv,
		ov5647_mode_table[OV5647_START_STREAM]);
}

static int ov5647_stop_streaming(struct tegracam_device *tc_dev)
{
	struct ov5647 *priv = tegracam_get_privdata(tc_dev);
	int err;

	err = ov5647_write_table(priv,
		ov5647_mode_table[OV5647_STOP_STREAM]);
	usleep_range(20000, 21000);
	return err;
}

static struct camera_common_sensor_ops ov5647_common_ops = {
	.numfrmfmts = ARRAY_SIZE(ov5647_frmfmt),
	.frmfmt_table = ov5647_frmfmt,
	.power_on = ov5647_power_on,
	.power_off = ov5647_power_off,
	.write_reg = ov5647_write_reg,
	.read_reg = ov5647_read_reg,
	.parse_dt = ov5647_parse_dt,
	.power_get = ov5647_power_get,
	.power_put = ov5647_power_put,
	.set_mode = ov5647_set_mode,
	.start_streaming = ov5647_start_streaming,
	.stop_streaming = ov5647_stop_streaming,
};

static int ov5647_board_setup(struct ov5647 *priv)
{
	struct camera_common_data *s_data = priv->s_data;
	struct camera_common_pdata *pdata = s_data->pdata;
	struct device *dev = s_data->dev;
	u8 id_high;
	u8 id_low;
	int power_err;
	int err;

	if (pdata->mclk_name) {
		err = camera_common_mclk_enable(s_data);
		if (err) {
			dev_err(dev, "failed to enable MCLK: %d\n", err);
			return err;
		}
	}

	err = ov5647_power_on(s_data);
	if (err)
		goto disable_mclk;

	err = ov5647_read_reg(s_data, OV5647_CHIP_ID_HIGH_REG, &id_high);
	if (err)
		goto power_off;
	err = ov5647_read_reg(s_data, OV5647_CHIP_ID_LOW_REG, &id_low);
	if (err)
		goto power_off;
	if (id_high != OV5647_CHIP_ID_HIGH || id_low != OV5647_CHIP_ID_LOW) {
		dev_err(dev, "unexpected sensor ID 0x%02x%02x (expected 0x5647)\n",
			id_high, id_low);
		err = -ENODEV;
	}

power_off:
	power_err = ov5647_power_off(s_data);
	if (!err && power_err)
		err = power_err;
disable_mclk:
	if (pdata->mclk_name)
		camera_common_mclk_disable(s_data);
	return err;
}

static int ov5647_open(struct v4l2_subdev *sd,
	struct v4l2_subdev_fh *fh)
{
	struct i2c_client *client = v4l2_get_subdevdata(sd);

	dev_dbg(&client->dev, "%s\n", __func__);
	return 0;
}

static const struct v4l2_subdev_internal_ops ov5647_subdev_internal_ops = {
	.open = ov5647_open,
};

static int ov5647_probe(struct i2c_client *client,
	const struct i2c_device_id *id)
{
	struct device *dev = &client->dev;
	struct tegracam_device *tc_dev;
	struct ov5647 *priv;
	int err;

	if (!IS_ENABLED(CONFIG_OF) || !dev->of_node)
		return -EINVAL;

	priv = devm_kzalloc(dev, sizeof(*priv), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;
	tc_dev = devm_kzalloc(dev, sizeof(*tc_dev), GFP_KERNEL);
	if (!tc_dev)
		return -ENOMEM;

	priv->i2c_client = tc_dev->client = client;
	tc_dev->dev = dev;
	strncpy(tc_dev->name, "ov5647", sizeof(tc_dev->name));
	tc_dev->name[sizeof(tc_dev->name) - 1] = '\0';
	tc_dev->dev_regmap_config = &ov5647_regmap_config;
	tc_dev->sensor_ops = &ov5647_common_ops;
	tc_dev->v4l2sd_internal_ops = &ov5647_subdev_internal_ops;
	tc_dev->tcctrl_ops = &ov5647_ctrl_ops;

	err = tegracam_device_register(tc_dev);
	if (err)
		return err;

	priv->tc_dev = tc_dev;
	priv->s_data = tc_dev->s_data;
	priv->subdev = &tc_dev->s_data->subdev;
	tegracam_set_privdata(tc_dev, priv);

	err = ov5647_board_setup(priv);
	if (err)
		goto unregister_device;

	err = tegracam_v4l2subdev_register(tc_dev, true);
	if (err)
		goto unregister_device;

	dev_info(dev, "detected OV5647 sensor\n");
	return 0;

unregister_device:
	tegracam_device_unregister(tc_dev);
	return err;
}

static int ov5647_remove(struct i2c_client *client)
{
	struct camera_common_data *s_data =
		to_camera_common_data(&client->dev);
	struct ov5647 *priv = s_data->priv;

	tegracam_v4l2subdev_unregister(priv->tc_dev);
	tegracam_device_unregister(priv->tc_dev);
	return 0;
}

static const struct i2c_device_id ov5647_id[] = {
	{ "ov5647", 0 },
	{ }
};
MODULE_DEVICE_TABLE(i2c, ov5647_id);

static struct i2c_driver ov5647_i2c_driver = {
	.driver = {
		.name = "ov5647",
		.owner = THIS_MODULE,
		.of_match_table = of_match_ptr(ov5647_of_match),
	},
	.probe = ov5647_probe,
	.remove = ov5647_remove,
	.id_table = ov5647_id,
};
module_i2c_driver(ov5647_i2c_driver);

MODULE_DESCRIPTION("Tegra camera driver for OmniVision OV5647");
MODULE_AUTHOR("NVIDIA Tegra integration; OV5647 tables from Raspberry Pi Linux");
MODULE_LICENSE("GPL v2");
