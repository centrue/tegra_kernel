# Copyright (c) 2021, NVIDIA CORPORATION. All rights reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

from Jetson import header_def


HDR = header_def.HeaderDef( \
            #name
            'Jetson TX1/TX2 CSI Connector',
            #prefix
            'csi',
            #pin_count
            128,
            #static_pins
            {
                #idx    :       label
                  5     :       'GND',
                  6     :       'GND',
                 11     :       'GND',
                 12     :       'GND',
                 17     :       'GND',
                 18     :       'GND',
                 23     :       'GND',
                 24     :       'GND',
                 29     :       'GND',
                 30     :       'GND',
                 35     :       'GND',
                 36     :       'GND',
                 41     :       'GND',
                 42     :       'GND',
                 47     :       'GND',
                 48     :       'GND',
                 53     :       'GND',
                 54     :       'GND',
                 69     :       'GND',
                 70     :       'GND',
                 79     :       'GND',
                 80     :       'GND',
                 81     :       '2.8V',
                 82     :       '2.8V',
                 83     :       '2.8V',
                 84     :       '3.3V',
                 99     :       'GND',
                100     :       'GND',
                101     :       '1.2V',
                102     :       '1.8V',
                108     :       '3.3V',
                109     :       '5V',
                110     :       '3.3V',
                115     :       'GND',
                116     :       'GND',
                118     :       '5V',
                120     :       '5V',
                121     :       'GND',
                122     :       'GND',
                123     :       'GND',
                124     :       'GND',
                125     :       'GND',
                126     :       'GND',
                127     :       'GND',
                128     :       'GND',
            }
        )
