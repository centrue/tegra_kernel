# Copyright (c) 2019-2021, NVIDIA CORPORATION. All rights reserved.
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

from Utils import fio
import os


_dt_base_path = '/sys/firmware/devicetree/base'


def prop_exists(prop):
    node = os.path.join(_dt_base_path, prop)
    return os.path.exists(node)


def read_prop(prop):
    node = os.path.join(_dt_base_path, prop)
    fio.is_readable(node)

    with open(node, 'r') as f:
        value = f.readline()

    # Return a string of values with a single space delimiter.
    # Note this is equivalent behaviour to the 'fdtget' tool.
    return ' '.join(value.split('\0')).rstrip()


def get_child_nodes(dir):
    dir_path = os.path.join(_dt_base_path, dir)

    listdir = os.listdir(dir_path)
    sub_nodes = []

    for d in listdir:
        if os.path.isdir(os.path.join(dir_path, d)):
            sub_nodes.append(d)

    return sub_nodes
