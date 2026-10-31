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


def main():
    parser = argparse.ArgumentParser(
        "Display configuration for Jetson expansion headers")
    main = parser.add_mutually_exclusive_group(required=False)
    main.add_argument("-l", "--list", help="List supported headers",
                      action='store_true')
    main.add_argument("-p", "--pin", help="Pin number")
    parser.add_argument("-n", "--header", help="Header number")
    args = parser.parse_args()

    jetson = board.Board()
    headers = jetson.get_board_headers()

    if len(headers) == 0:
        raise RuntimeError("Platform not supported, no headers found!")

    if args.pin:
        pin = int(args.pin)

    if args.header:
        hdr_idx = int(args.header) - 1
        if (hdr_idx < 0) or (hdr_idx >= len(headers)):
            raise IndexError("Invalid Header number!")
    elif args.pin:
        hdr_idx = 0

    if args.pin:
        jetson.set_active_header(headers[hdr_idx])
        pincount = jetson.header.pin_count()
        if pin < 1 or pin > pincount:
            raise IndexError("Invalid Pin number!")

        print(jetson.header.pin_get_label(pin))
        return

    for header in headers:
        idx = headers.index(header)

        if (args.header is None) or (hdr_idx == idx):
            jetson.set_active_header(header)
            pincount = jetson.header.pin_count()

            if idx == 0:
                print("Header 1 [default]: %s" % header)
            else:
                print("Header %d: %s" % (idx + 1, header))

            if not args.list:
                for index in range(pincount):
                    label = jetson.header.pin_get_label(index + 1)
                    if label != 'NA':
                        print("%3d: %s" % (index + 1, label))


if __name__ == '__main__':
    main()
