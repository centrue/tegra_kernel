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

import importlib
import os


HDRS = []
_default = False


for module in sorted(os.listdir(os.path.dirname(__file__))):
    if module == '__init__.py' or not module.endswith('.py'):
        continue
    module = '.' + os.path.splitext(module)[0]
    hdr = importlib.import_module(module, 'Headers')
    if hdr.HDR.default:
        if _default:
            raise RuntimeError("More than one header set to 'default'!")
        # Default header is placed at the top of the list, so it will be
        # displayed at the top of the GUI menu and the cursor will be over
        # it. The CLI scripts can access the default header as header no. 1
        HDRS.insert(0, hdr.HDR)
        _default = True
    else:
        HDRS.append(hdr.HDR)
