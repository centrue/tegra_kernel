# SPDX-License-Identifier: GPL-2.0-only
"""IMX708 modes verified by the running Nano's VIDIOC_ENUM_FRAMESIZES."""
SENSOR_MODES=(
    dict(index=0,resolution='4608x2592',width=4608,height=2592,max_fps=14),
    dict(index=1,resolution='2304x1296',width=2304,height=1296,max_fps=56),
    dict(index=2,resolution='1536x864',width=1536,height=864,max_fps=120),
)
RESOLUTIONS={mode['resolution']:mode for mode in SENSOR_MODES}
