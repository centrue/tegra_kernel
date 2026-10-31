#!/usr/bin/env python3

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

import argparse
from Jetson import board
import sys
import re
import os


def show_functions(jetson, functions):
    for index, function in enumerate(functions):
        pins = jetson.header.pingroup_get_pins(function)
        print("  %2d. %s (%s)" % (index + 1, function, pins))


def show_functions_all(jetson, functions):
    if not functions:
        print("  No functions are supported.")
    else:
        print("  Supported functions (pins):")
        show_functions(jetson, functions)


def show_functions_enabled(jetson, functions):
    enabled = []
    for function in functions:
        if jetson.header.pingroup_is_enabled(function):
            enabled.append(function)

    if not enabled:
        print("  No functions are enabled.")
    else:
        print("  Enabled functions (pins):")
        show_functions(jetson, enabled)


def generate_dtb(jetson, dtbos):
    fn = jetson.create_dtb_for_headers(dtbos)
    print("Configuration saved to %s." % fn)
    print("Reboot system to reconfigure.")


def check_generate_jetson_io_pinmux(jetson, header):
    if not jetson.preconf_pins_avail(header):
        return None

    jetson.set_active_header(header)
    return jetson.create_dtbo_for_header()


def configure_jetson(jetson, out, header, functions):
    dtbo = None

    if functions:
        jetson.set_active_header(header)
        available = jetson.header.pingroups_available()

        for function in functions:
            if function not in available:
                raise NameError("Function %s is not supported on %s!" \
                                % (function, header))
            jetson.header.pingroup_enable(function)

        if not jetson.header.pins_are_default():
            dtbo = jetson.create_dtbo_for_header()

    if (dtbo is None) and (out == 'dtb'):
        # Below ensures that changes made to headers (and written
        # to DTB) in earlier sessions of the tool are retained
        dtbo = check_generate_jetson_io_pinmux(jetson, header)

    if dtbo and (out == 'dtbo'):
        print("Configuration saved to %s." % dtbo)

    return dtbo


def parse_function_args(func_args, num_headers):
    funcs = [None] * num_headers

    for arg in func_args:
        res = re.match(r'([0-9]+)=(.+)', arg)
        if res:
            idx = int(res.groups()[0]) - 1
            func = res.groups()[1].split()
        else:
            idx = 0
            func = [arg]

        if (idx < 0) or (idx >= num_headers):
            raise IndexError("Invalid Header number %d!" % (idx + 1))

        if funcs[idx] is None:
            funcs[idx] = []
        funcs[idx] = funcs[idx] + func

    return funcs


def main():
    parser = argparse.ArgumentParser("Configure Jetson expansion headers")
    main = parser.add_mutually_exclusive_group(required=True)
    main.add_argument("-l", "--list", choices=['all', 'enabled'],
                      help="List supported functions")
    main.add_argument("-o", "--out", choices=['dtb', 'dtbo'],
                      help="Output DTB or DTBO file(s)")
    parser.add_argument('functions', nargs='*',
                        help="<header-num>=\"<func1> <func2>\" ...")
    args = parser.parse_args()

    jetson = board.Board()
    headers = jetson.get_board_headers()

    if len(headers) == 0:
        raise RuntimeError("Platform not supported, no headers found!")

    if args.list:
        for header in headers:
            jetson.set_active_header(header)
            available = jetson.header.pingroups_available()
            idx = headers.index(header)

            if idx == 0:
                print("Header 1 [default]: %s" % header)
            else:
                print("Header %d: %s" % (idx + 1, header))

            if args.list == 'all':
                show_functions_all(jetson, available)
            else: # 'enabled'
                show_functions_enabled(jetson, available)

        sys.exit(0)

    # functions
    if not args.functions:
        raise RuntimeError("No function list specified!")
    funcs = parse_function_args(args.functions, len(headers))

    try:
        dtbos = []
        delete_dtbos = False

        for header in headers:
            idx = headers.index(header)

            dtbo = configure_jetson(jetson, args.out, header, funcs[idx])
            if dtbo:
                dtbos.append(dtbo)

        if (args.out == 'dtb') and (len(dtbos) >= 1):
            generate_dtb(jetson, dtbos)
            delete_dtbos = True
    except:
        delete_dtbos = True
        raise
    finally:
        if delete_dtbos:
            for dtbo in dtbos:
                os.remove(dtbo)


if __name__ == '__main__':
    main()
