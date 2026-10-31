/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Dongwoon DW9817 VCM driver for Jetson Nano L4T R32.7.6.
 *
 * The register protocol and gradual movement policy follow Raspberry Pi's
 * dw9807-vcm driver.  DW9817 is the bidirectional actuator fitted to Camera
 * Module 3, so its zero-current position is the DAC midpoint (512), not 0.
 * This driver exposes manual focus only; autofocus policy belongs in
 * userspace.  Register a standalone V4L2 device: NVIDIA's 4.9 VI graph
 * does not discover the lens-focus phandle as an asynchronous subdevice.
 * Hardware sequencing follows public_sources/linux/drivers/media/i2c/
 * dw9807-vcm.c (Raspberry Pi): idle 512, default 480, 16 DAC steps/1 ms.
 */

#include <linux/delay.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/regulator/consumer.h>
#include <linux/slab.h>

#include <media/v4l2-ctrls.h>
#include <media/v4l2-device.h>
#include <media/v4l2-dev.h>
#include <media/v4l2-subdev.h>

#define DW9817_MAX_FOCUS_POSITION 1023
#define DW9817_IDLE_POSITION 512
#define DW9817_DEFAULT_POSITION 480
#define DW9817_RAMP_STEP 16
#define DW9817_RAMP_DELAY_US 1000
#define DW9817_STATUS_RETRIES 10

#define DW9817_REG_CONTROL 0x02
#define DW9817_REG_POSITION_MSB 0x03
#define DW9817_REG_STATUS 0x05

#define DW9817_CONTROL_ACTIVE 0x00
#define DW9817_CONTROL_STANDBY 0x01

#ifndef DW9817_DRIVER_NAME
#define DW9817_DRIVER_NAME "dw9817"
#endif

static bool trace;
module_param(trace, bool, 0644);
MODULE_PARM_DESC(trace, "Log power, target DAC, and hardware position");

struct dw9817 {
	struct v4l2_device v4l2_dev;
	struct v4l2_subdev subdev;
	struct v4l2_ctrl_handler ctrl_handler;
	struct regulator *vdd;
	struct mutex lock;
	u16 position;
	unsigned int power_count;
	bool removed;
};

static inline struct dw9817 *to_dw9817(struct v4l2_subdev *subdev)
{
	return container_of(subdev, struct dw9817, subdev);
}

static int dw9817_write(struct i2c_client *client, const u8 *data, int len)
{
	int err;

	err = i2c_master_send(client, data, len);
	if (err == len)
		return 0;
	if (err >= 0)
		err = -EIO;
	dev_err(&client->dev, "I2C write failed: %d\n", err);
	return err;
}

static int dw9817_read_status(struct i2c_client *client)
{
	u8 reg = DW9817_REG_STATUS;
	u8 status;
	int err;

	err = i2c_master_send(client, &reg, sizeof(reg));
	if (err != sizeof(reg))
		return err < 0 ? err : -EIO;
	err = i2c_master_recv(client, &status, sizeof(status));
	if (err != sizeof(status))
		return err < 0 ? err : -EIO;
	return status;
}

static int dw9817_set_position(struct i2c_client *client, u16 position)
{
	u8 data[3] = {
		DW9817_REG_POSITION_MSB,
		(position >> 8) & 0x03,
		position & 0xff,
	};
	int retries;
	int status;

	for (retries = 0; retries < DW9817_STATUS_RETRIES; retries++) {
		status = dw9817_read_status(client);
		if (status < 0)
			return status;
		if (status == 0) {
			int err = dw9817_write(client, data, sizeof(data));

			if (trace)
				dev_info(&client->dev, "DAC target=%u status=%d ret=%d\n",
					position, status, err);
			return err;
		}
		usleep_range(DW9817_RAMP_DELAY_US,
			DW9817_RAMP_DELAY_US + 10);
	}

	dev_warn(&client->dev, "actuator remained busy\n");
	return -EBUSY;
}

static int dw9817_ramp(struct i2c_client *client, int start, int end)
{
	int direction = start < end ? DW9817_RAMP_STEP : -DW9817_RAMP_STEP;
	int position = start;
	int err;

	for (;;) {
		position += direction;
		if (direction * (position - end) >= 0)
			position = end;
		err = dw9817_set_position(client, position);
		if (err)
			return err;
		if (position == end)
			return 0;
		usleep_range(DW9817_RAMP_DELAY_US,
			DW9817_RAMP_DELAY_US + 10);
	}
}

static int dw9817_set_power(struct dw9817 *dw, bool on)
{
	struct i2c_client *client = v4l2_get_subdevdata(&dw->subdev);
	u8 command[2] = { DW9817_REG_CONTROL, DW9817_CONTROL_ACTIVE };
	int err = 0;

	mutex_lock(&dw->lock);
	if (dw->removed) {
		err = on ? -ENODEV : 0;
		goto unlock;
	}
	if (trace)
		dev_info(&client->dev, "power=%u users=%u position=%u\n",
			on, dw->power_count, dw->position);
	if (on) {
		if (dw->power_count++ != 0)
			goto unlock;
		if (dw->vdd) {
			err = regulator_enable(dw->vdd);
			if (err)
				goto undo_count;
			usleep_range(12000, 12100);
		}
		err = dw9817_write(client, command, sizeof(command));
		if (!err)
			err = dw9817_ramp(client, DW9817_IDLE_POSITION,
				dw->position);
		if (err)
			goto disable_regulator;
	} else {
		if (WARN_ON(dw->power_count == 0)) {
			err = -EINVAL;
			goto unlock;
		}
		if (--dw->power_count != 0)
			goto unlock;
		err = dw9817_ramp(client, dw->position,
			DW9817_IDLE_POSITION);
		command[1] = DW9817_CONTROL_STANDBY;
		if (!err)
			err = dw9817_write(client, command, sizeof(command));
		if (dw->vdd) {
			int disable_err = regulator_disable(dw->vdd);

			if (!err)
				err = disable_err;
		}
	}
	goto unlock;

disable_regulator:
	if (dw->vdd)
		regulator_disable(dw->vdd);
undo_count:
	dw->power_count = 0;
unlock:
	mutex_unlock(&dw->lock);
	return err;
}

static int dw9817_s_power(struct v4l2_subdev *subdev, int on)
{
	return dw9817_set_power(to_dw9817(subdev), on != 0);
}

static int dw9817_set_ctrl(struct v4l2_ctrl *ctrl)
{
	struct dw9817 *dw = container_of(ctrl->handler,
		struct dw9817, ctrl_handler);
	struct i2c_client *client = v4l2_get_subdevdata(&dw->subdev);
	int previous;
	int err = 0;

	if (ctrl->id != V4L2_CID_FOCUS_ABSOLUTE)
		return -EINVAL;

	mutex_lock(&dw->lock);
	if (dw->removed) {
		err = -ENODEV;
		goto unlock;
	}
	previous = dw->position;
	dw->position = ctrl->val;
	if (dw->power_count)
		err = dw9817_ramp(client, previous, dw->position);
	if (err)
		dw->position = previous;
unlock:
	mutex_unlock(&dw->lock);
	return err;
}

static const struct v4l2_ctrl_ops dw9817_ctrl_ops = {
	.s_ctrl = dw9817_set_ctrl,
};

static int dw9817_open(struct v4l2_subdev *subdev,
	struct v4l2_subdev_fh *fh)
{
	return dw9817_s_power(subdev, 1);
}

static int dw9817_close(struct v4l2_subdev *subdev,
	struct v4l2_subdev_fh *fh)
{
	return dw9817_s_power(subdev, 0);
}

static const struct v4l2_subdev_core_ops dw9817_core_ops = {
	.s_power = dw9817_s_power,
};

static const struct v4l2_subdev_ops dw9817_subdev_ops = {
	.core = &dw9817_core_ops,
};

static const struct v4l2_subdev_internal_ops dw9817_internal_ops = {
	.open = dw9817_open,
	.close = dw9817_close,
};

/* The V4L2 node keeps this allocation alive until the last open FD closes. */
static void dw9817_release(struct v4l2_device *v4l2_dev)
{
	struct dw9817 *dw = container_of(v4l2_dev, struct dw9817, v4l2_dev);
	struct i2c_client *client = v4l2_get_subdevdata(&dw->subdev);

	v4l2_device_unregister(v4l2_dev);
	media_entity_cleanup(&dw->subdev.entity);
	v4l2_ctrl_handler_free(&dw->ctrl_handler);
	if (dw->vdd)
		regulator_put(dw->vdd);
	mutex_destroy(&dw->lock);
	put_device(&client->dev);
	kfree(dw);
}

static int dw9817_probe(struct i2c_client *client,
	const struct i2c_device_id *id)
{
	struct dw9817 *dw;
	int err;

	dw = kzalloc(sizeof(*dw), GFP_KERNEL);
	if (!dw)
		return -ENOMEM;

	mutex_init(&dw->lock);
	dw->position = DW9817_DEFAULT_POSITION;
	dw->vdd = regulator_get_optional(&client->dev, "VDD");
	if (IS_ERR(dw->vdd)) {
		err = PTR_ERR(dw->vdd);
		if (err == -ENODEV)
			dw->vdd = NULL;
		else
			goto destroy_mutex;
	}

	v4l2_i2c_subdev_init(&dw->subdev, client, &dw9817_subdev_ops);
	dw->subdev.flags |= V4L2_SUBDEV_FL_HAS_DEVNODE;
	dw->subdev.internal_ops = &dw9817_internal_ops;
	dw->subdev.entity.function = MEDIA_ENT_F_LENS;

	v4l2_ctrl_handler_init(&dw->ctrl_handler, 1);
	v4l2_ctrl_new_std(&dw->ctrl_handler, &dw9817_ctrl_ops,
		V4L2_CID_FOCUS_ABSOLUTE, 0, DW9817_MAX_FOCUS_POSITION, 1,
		DW9817_DEFAULT_POSITION);
	if (dw->ctrl_handler.error) {
		err = dw->ctrl_handler.error;
		goto free_ctrls;
	}
	dw->subdev.ctrl_handler = &dw->ctrl_handler;

	err = media_entity_pads_init(&dw->subdev.entity, 0, NULL);
	if (err)
		goto free_ctrls;
	err = v4l2_device_register(&client->dev, &dw->v4l2_dev);
	if (err)
		goto cleanup_entity;
	get_device(&client->dev);
	dw->v4l2_dev.release = dw9817_release;
	err = v4l2_device_register_subdev(&dw->v4l2_dev, &dw->subdev);
	if (err)
		goto unregister_device;
	err = v4l2_device_register_subdev_nodes(&dw->v4l2_dev);
	if (err)
		goto unregister_device;

	dev_info(&client->dev,
		"DW9817 actuator registered (manual focus control): /dev/v4l-subdev%d\n",
		dw->subdev.devnode->num);
	return 0;

unregister_device:
	i2c_set_clientdata(client, NULL);
	v4l2_device_unregister(&dw->v4l2_dev);
	v4l2_device_put(&dw->v4l2_dev);
	return err;
cleanup_entity:
	media_entity_cleanup(&dw->subdev.entity);
free_ctrls:
	v4l2_ctrl_handler_free(&dw->ctrl_handler);
destroy_mutex:
	if (!IS_ERR_OR_NULL(dw->vdd))
		regulator_put(dw->vdd);
	mutex_destroy(&dw->lock);
	kfree(dw);
	return err;
}

static int dw9817_remove(struct i2c_client *client)
{
	struct v4l2_subdev *subdev = i2c_get_clientdata(client);
	struct dw9817 *dw = to_dw9817(subdev);

	/* Keep sd->v4l2_dev valid for 4.9 subdev_close on an outstanding FD. */
	mutex_lock(&dw->lock);
	dw->removed = true;
	mutex_unlock(&dw->lock);
	/* Shutdown hardware while the client is still attached. */
	mutex_lock(&dw->lock);
	if (dw->power_count) {
		u8 command[] = { DW9817_REG_CONTROL, DW9817_CONTROL_STANDBY };

		dw9817_ramp(client, dw->position, DW9817_IDLE_POSITION);
		dw9817_write(client, command, sizeof(command));
		if (dw->vdd)
			regulator_disable(dw->vdd);
		dw->power_count = 0;
	}
	mutex_unlock(&dw->lock);
	video_unregister_device(subdev->devnode);
	v4l2_device_disconnect(&dw->v4l2_dev);
	i2c_set_clientdata(client, NULL);
	v4l2_device_put(&dw->v4l2_dev);
	return 0;
}

static const struct of_device_id dw9817_of_match[] = {
	{ .compatible = "dongwoon,dw9817-vcm" },
	{ }
};
MODULE_DEVICE_TABLE(of, dw9817_of_match);

static const struct i2c_device_id dw9817_id[] = {
	{ "dw9817", 0 },
	{ }
};
MODULE_DEVICE_TABLE(i2c, dw9817_id);

static struct i2c_driver dw9817_i2c_driver = {
	.driver = {
		.name = DW9817_DRIVER_NAME,
		.owner = THIS_MODULE,
		.of_match_table = of_match_ptr(dw9817_of_match),
	},
	.probe = dw9817_probe,
	.remove = dw9817_remove,
	.id_table = dw9817_id,
};
module_i2c_driver(dw9817_i2c_driver);

MODULE_AUTHOR("Intel; Raspberry Pi; Jetpack workspace R32.7.6 port");
MODULE_DESCRIPTION("Dongwoon DW9817 VCM manual focus driver");
MODULE_LICENSE("GPL v2");
