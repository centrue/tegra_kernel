/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Sony IMX708 tegracam driver for Jetson Nano L4T R32.7.6.
 *
 * The sensor programming sequence is derived from RidgeRun's JetPack 4.6.4
 * Raspberry Pi Camera Module 3 patch.  The three non-HDR mode tables are
 * synchronized with Raspberry Pi's driver while using the control, cleanup,
 * and error handling conventions of NVIDIA's R32.7.6 camera framework.
 */

#include <linux/gpio.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_device.h>
#include <linux/of_gpio.h>
#include <linux/regulator/consumer.h>
#include <linux/slab.h>

#include <media/imx708.h>
#include <media/tegra_v4l2_camera.h>
#include <media/tegracam_core.h>

#include "imx708_mode_tbls.h"

static const struct of_device_id imx708_of_match[] = {
	{ .compatible = "sony,imx708" },
	{ }
};
MODULE_DEVICE_TABLE(of, imx708_of_match);

static const u32 imx708_ctrl_cid_list[] = {
	TEGRA_CAMERA_CID_GAIN,
	TEGRA_CAMERA_CID_EXPOSURE,
	TEGRA_CAMERA_CID_FRAME_RATE,
	TEGRA_CAMERA_CID_SENSOR_MODE_ID,
};

struct imx708 {
	struct i2c_client *i2c_client;
	struct v4l2_subdev *subdev;
	u16 fine_integ_time;
	u32 frame_length;
	struct camera_common_data *s_data;
	struct tegracam_device *tc_dev;
};

static const struct regmap_config imx708_regmap_config = {
	.reg_bits = 16,
	.val_bits = 8,
	.cache_type = REGCACHE_RBTREE,
	.use_single_rw = true,
};

static int imx708_read_reg(struct camera_common_data *s_data,
	u16 addr, u8 *val)
{
	u32 reg_val;
	int err;

	err = regmap_read(s_data->regmap, addr, &reg_val);
	if (!err)
		*val = reg_val & 0xff;
	return err;
}

static int imx708_write_reg(struct camera_common_data *s_data,
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

static int imx708_write_table(struct imx708 *priv,
	const imx708_reg table[])
{
	return regmap_util_write_table_8(priv->s_data->regmap, table, NULL, 0,
		IMX708_TABLE_WAIT_MS, IMX708_TABLE_END);
}

static int imx708_write_u16(struct camera_common_data *s_data,
	u16 msb_addr, u16 value)
{
	int err;

	err = imx708_write_reg(s_data, msb_addr, value >> 8);
	if (err)
		return err;
	return imx708_write_reg(s_data, msb_addr + 1, value & 0xff);
}

static int imx708_set_group_hold(struct tegracam_device *tc_dev, bool val)
{
	return imx708_write_reg(tc_dev->s_data, IMX708_GROUP_HOLD_ADDR,
		val ? 1 : 0);
}

static int imx708_set_gain(struct tegracam_device *tc_dev, s64 val)
{
	struct camera_common_data *s_data = tc_dev->s_data;
	const struct sensor_mode_properties *mode =
		&s_data->sensor_props.sensor_modes[s_data->mode_prop_idx];
	s64 gain;

	if (val < mode->control_properties.min_gain_val)
		val = mode->control_properties.min_gain_val;
	else if (val > mode->control_properties.max_gain_val)
		val = mode->control_properties.max_gain_val;
	if (val <= 0)
		return -EINVAL;

	gain = IMX708_ANALOG_GAIN_C0 -
		((s64)IMX708_ANALOG_GAIN_C0 *
		 mode->control_properties.gain_factor / val);
	if (gain < IMX708_MIN_GAIN)
		gain = IMX708_MIN_GAIN;
	else if (gain > IMX708_MAX_GAIN)
		gain = IMX708_MAX_GAIN;

	return imx708_write_u16(s_data, IMX708_ANALOG_GAIN_ADDR_MSB,
		(u16)gain);
}

static int imx708_set_frame_rate(struct tegracam_device *tc_dev, s64 val)
{
	struct camera_common_data *s_data = tc_dev->s_data;
	struct imx708 *priv = tegracam_get_privdata(tc_dev);
	const struct sensor_mode_properties *mode =
		&s_data->sensor_props.sensor_modes[s_data->mode_prop_idx];
	u64 frame_length;
	int err;

	if (val < mode->control_properties.min_framerate)
		val = mode->control_properties.min_framerate;
	else if (val > mode->control_properties.max_framerate)
		val = mode->control_properties.max_framerate;
	if (val <= 0 || !mode->image_properties.line_length)
		return -EINVAL;

	frame_length = mode->signal_properties.pixel_clock.val *
		(u64)mode->control_properties.framerate_factor;
	frame_length /= mode->image_properties.line_length;
	frame_length /= val;
	if (frame_length < IMX708_MIN_FRAME_LENGTH)
		frame_length = IMX708_MIN_FRAME_LENGTH;
	else if (frame_length > IMX708_MAX_FRAME_LENGTH)
		frame_length = IMX708_MAX_FRAME_LENGTH;

	err = imx708_write_u16(s_data, IMX708_FRAME_LENGTH_ADDR_MSB,
		(u16)frame_length);
	if (!err)
		priv->frame_length = frame_length;
	return err;
}

static int imx708_set_exposure(struct tegracam_device *tc_dev, s64 val)
{
	struct camera_common_data *s_data = tc_dev->s_data;
	struct imx708 *priv = tegracam_get_privdata(tc_dev);
	const struct sensor_mode_properties *mode =
		&s_data->sensor_props.sensor_modes[s_data->mode_prop_idx];
	s64 fine_factor;
	s64 coarse_time;
	u32 min_coarse;
	u32 coarse_step;
	u32 max_coarse;

	if (val < mode->control_properties.min_exp_time.val)
		val = mode->control_properties.min_exp_time.val;
	else if (val > mode->control_properties.max_exp_time.val)
		val = mode->control_properties.max_exp_time.val;
	if (!mode->control_properties.exposure_factor ||
		!mode->image_properties.line_length)
		return -EINVAL;

	fine_factor = (s64)priv->fine_integ_time *
		mode->control_properties.exposure_factor /
		mode->signal_properties.pixel_clock.val;
	coarse_time = (val - fine_factor) *
		mode->signal_properties.pixel_clock.val /
		mode->control_properties.exposure_factor /
		mode->image_properties.line_length;
	min_coarse = imx708_min_coarse_exposure[s_data->mode];
	coarse_step = imx708_coarse_exposure_step[s_data->mode];
	max_coarse = priv->frame_length > IMX708_MAX_COARSE_DIFF ?
		priv->frame_length - IMX708_MAX_COARSE_DIFF :
		min_coarse;
	if (coarse_time < min_coarse)
		coarse_time = min_coarse;
	else if (coarse_time > max_coarse)
		coarse_time = max_coarse;
	coarse_time -= coarse_time % coarse_step;
	if (coarse_time < min_coarse)
		coarse_time = min_coarse;

	return imx708_write_u16(s_data, IMX708_COARSE_INTEG_TIME_ADDR_MSB,
		(u16)coarse_time);
}

static struct tegracam_ctrl_ops imx708_ctrl_ops = {
	.numctrls = ARRAY_SIZE(imx708_ctrl_cid_list),
	.ctrl_cid_list = imx708_ctrl_cid_list,
	.set_gain = imx708_set_gain,
	.set_exposure = imx708_set_exposure,
	.set_frame_rate = imx708_set_frame_rate,
	.set_group_hold = imx708_set_group_hold,
};

static int imx708_power_on(struct camera_common_data *s_data)
{
	struct camera_common_power_rail *pw = s_data->power;
	struct camera_common_pdata *pdata = s_data->pdata;
	int err;

	if (pdata && pdata->power_on) {
		err = pdata->power_on(pw);
		if (!err)
			pw->state = SWITCH_ON;
		return err;
	}

	if (gpio_is_valid(pw->reset_gpio))
		gpio_set_value_cansleep(pw->reset_gpio, 0);

	if (pw->avdd) {
		err = regulator_enable(pw->avdd);
		if (err)
			return err;
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

	if (gpio_is_valid(pw->reset_gpio))
		gpio_set_value_cansleep(pw->reset_gpio, 1);
	/* RidgeRun's sequence budgets reset release plus sensor startup. */
	usleep_range(300000, 300100);
	pw->state = SWITCH_ON;
	return 0;

disable_iovdd:
	if (pw->iovdd)
		regulator_disable(pw->iovdd);
disable_avdd:
	if (pw->avdd)
		regulator_disable(pw->avdd);
	return err;
}

static int imx708_power_off(struct camera_common_data *s_data)
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

static int imx708_power_get(struct tegracam_device *tc_dev)
{
	struct device *dev = tc_dev->dev;
	struct camera_common_data *s_data = tc_dev->s_data;
	struct camera_common_power_rail *pw = s_data->power;
	struct camera_common_pdata *pdata = s_data->pdata;
	int err = 0;

	if (!pdata)
		return -EFAULT;

	if (pdata->regulators.avdd)
		err |= camera_common_regulator_get(dev, &pw->avdd,
			pdata->regulators.avdd);
	if (pdata->regulators.iovdd)
		err |= camera_common_regulator_get(dev, &pw->iovdd,
			pdata->regulators.iovdd);
	if (pdata->regulators.dvdd)
		err |= camera_common_regulator_get(dev, &pw->dvdd,
			pdata->regulators.dvdd);
	if (err)
		return err;

	pw->reset_gpio = pdata->reset_gpio;
	if (!gpio_is_valid(pw->reset_gpio))
		return -EINVAL;
	err = gpio_request(pw->reset_gpio, "imx708_reset");
	if (err)
		return err;
	pw->state = SWITCH_OFF;
	return 0;
}

static int imx708_power_put(struct tegracam_device *tc_dev)
{
	struct camera_common_power_rail *pw = tc_dev->s_data->power;

	if (!pw)
		return -EFAULT;
	if (pw->dvdd)
		devm_regulator_put(pw->dvdd);
	if (pw->iovdd)
		devm_regulator_put(pw->iovdd);
	if (pw->avdd)
		devm_regulator_put(pw->avdd);
	pw->dvdd = NULL;
	pw->iovdd = NULL;
	pw->avdd = NULL;
	if (gpio_is_valid(pw->reset_gpio))
		gpio_free(pw->reset_gpio);
	return 0;
}

static struct camera_common_pdata *imx708_parse_dt(
	struct tegracam_device *tc_dev)
{
	struct device *dev = tc_dev->dev;
	struct device_node *np = dev->of_node;
	struct camera_common_pdata *pdata;
	int gpio;

	if (!np || !of_match_device(imx708_of_match, dev))
		return NULL;
	pdata = devm_kzalloc(dev, sizeof(*pdata), GFP_KERNEL);
	if (!pdata)
		return NULL;

	gpio = of_get_named_gpio(np, "reset-gpios", 0);
	if (gpio < 0) {
		if (gpio == -EPROBE_DEFER)
			return ERR_PTR(-EPROBE_DEFER);
		dev_err(dev, "reset-gpios not found: %d\n", gpio);
		return NULL;
	}
	pdata->reset_gpio = gpio;

	of_property_read_string(np, "avdd-reg", &pdata->regulators.avdd);
	of_property_read_string(np, "iovdd-reg", &pdata->regulators.iovdd);
	of_property_read_string(np, "dvdd-reg", &pdata->regulators.dvdd);
	pdata->has_eeprom = of_property_read_bool(np, "has-eeprom");
	return pdata;
}

static int imx708_set_mode(struct tegracam_device *tc_dev)
{
	struct imx708 *priv = tegracam_get_privdata(tc_dev);
	struct camera_common_data *s_data = tc_dev->s_data;
	int err;

	switch (s_data->mode) {
	case IMX708_MODE_4608X2592_14FPS:
	case IMX708_MODE_2304X1296_56FPS:
	case IMX708_MODE_1536X864_120FPS:
		break;
	default:
		return -EINVAL;
	}
	err = imx708_write_table(priv,
		imx708_mode_table[IMX708_MODE_COMMON]);
	if (err)
		return err;
	err = imx708_write_table(priv, imx708_mode_table[s_data->mode]);
	if (!err)
		priv->frame_length = imx708_default_frame_length[s_data->mode];
	return err;
}

static int imx708_start_streaming(struct tegracam_device *tc_dev)
{
	struct imx708 *priv = tegracam_get_privdata(tc_dev);

	return imx708_write_table(priv,
		imx708_mode_table[IMX708_START_STREAM]);
}

static int imx708_stop_streaming(struct tegracam_device *tc_dev)
{
	struct imx708 *priv = tegracam_get_privdata(tc_dev);
	int err;

	err = imx708_write_table(priv,
		imx708_mode_table[IMX708_STOP_STREAM]);
	usleep_range(20000, 21000);
	return err;
}

static struct camera_common_sensor_ops imx708_common_ops = {
	.numfrmfmts = ARRAY_SIZE(imx708_frmfmt),
	.frmfmt_table = imx708_frmfmt,
	.power_on = imx708_power_on,
	.power_off = imx708_power_off,
	.write_reg = imx708_write_reg,
	.read_reg = imx708_read_reg,
	.parse_dt = imx708_parse_dt,
	.power_get = imx708_power_get,
	.power_put = imx708_power_put,
	.set_mode = imx708_set_mode,
	.start_streaming = imx708_start_streaming,
	.stop_streaming = imx708_stop_streaming,
};

static int imx708_board_setup(struct imx708 *priv)
{
	struct camera_common_data *s_data = priv->s_data;
	struct device *dev = s_data->dev;
	u8 id_msb;
	u8 id_lsb;
	u8 fine_msb;
	u8 fine_lsb;
	int power_err;
	int err;

	/* Raspberry Pi Camera Module 3 supplies the sensor clock on-module. */
	err = imx708_power_on(s_data);
	if (err)
		return err;
	err = imx708_read_reg(s_data, IMX708_MODEL_ID_ADDR_MSB, &id_msb);
	if (err)
		goto power_off;
	err = imx708_read_reg(s_data, IMX708_MODEL_ID_ADDR_LSB, &id_lsb);
	if (err)
		goto power_off;
	if (id_msb != IMX708_MODEL_ID_VALUE_MSB ||
		id_lsb != IMX708_MODEL_ID_VALUE_LSB) {
		dev_err(dev, "unexpected sensor ID 0x%02x%02x (expected 0x0301)\n",
			id_msb, id_lsb);
		err = -ENODEV;
		goto power_off;
	}
	err = imx708_read_reg(s_data, IMX708_FINE_INTEG_TIME_ADDR_MSB,
		&fine_msb);
	if (err)
		goto power_off;
	err = imx708_read_reg(s_data, IMX708_FINE_INTEG_TIME_ADDR_LSB,
		&fine_lsb);
	if (!err)
		priv->fine_integ_time = (fine_msb << 8) | fine_lsb;

power_off:
	power_err = imx708_power_off(s_data);
	if (!err && power_err)
		err = power_err;
	return err;
}

static int imx708_open(struct v4l2_subdev *sd,
	struct v4l2_subdev_fh *fh)
{
	struct i2c_client *client = v4l2_get_subdevdata(sd);

	dev_dbg(&client->dev, "%s\n", __func__);
	return 0;
}

static const struct v4l2_subdev_internal_ops imx708_subdev_internal_ops = {
	.open = imx708_open,
};

static int imx708_probe(struct i2c_client *client,
	const struct i2c_device_id *id)
{
	struct device *dev = &client->dev;
	struct tegracam_device *tc_dev;
	struct imx708 *priv;
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
	priv->frame_length =
		imx708_default_frame_length[IMX708_MODE_4608X2592_14FPS];
	tc_dev->dev = dev;
	strncpy(tc_dev->name, "imx708", sizeof(tc_dev->name));
	tc_dev->name[sizeof(tc_dev->name) - 1] = '\0';
	tc_dev->dev_regmap_config = &imx708_regmap_config;
	tc_dev->sensor_ops = &imx708_common_ops;
	tc_dev->v4l2sd_internal_ops = &imx708_subdev_internal_ops;
	tc_dev->tcctrl_ops = &imx708_ctrl_ops;

	err = tegracam_device_register(tc_dev);
	if (err)
		return err;
	priv->tc_dev = tc_dev;
	priv->s_data = tc_dev->s_data;
	priv->subdev = &tc_dev->s_data->subdev;
	tegracam_set_privdata(tc_dev, priv);

	err = imx708_board_setup(priv);
	if (err)
		goto unregister_device;
	err = tegracam_v4l2subdev_register(tc_dev, true);
	if (err)
		goto unregister_device;

	dev_info(dev, "detected IMX708 sensor\n");
	return 0;

unregister_device:
	tegracam_device_unregister(tc_dev);
	return err;
}

static int imx708_remove(struct i2c_client *client)
{
	struct camera_common_data *s_data = to_camera_common_data(&client->dev);
	struct imx708 *priv = s_data->priv;

	tegracam_v4l2subdev_unregister(priv->tc_dev);
	tegracam_device_unregister(priv->tc_dev);
	return 0;
}

static const struct i2c_device_id imx708_id[] = {
	{ "imx708", 0 },
	{ }
};
MODULE_DEVICE_TABLE(i2c, imx708_id);

static struct i2c_driver imx708_i2c_driver = {
	.driver = {
		.name = "imx708",
		.owner = THIS_MODULE,
		.of_match_table = of_match_ptr(imx708_of_match),
	},
	.probe = imx708_probe,
	.remove = imx708_remove,
	.id_table = imx708_id,
};
module_i2c_driver(imx708_i2c_driver);

MODULE_DESCRIPTION("Tegra camera driver for Sony IMX708");
MODULE_AUTHOR("RidgeRun; Jetpack workspace R32.7.6 port");
MODULE_LICENSE("GPL v2");
