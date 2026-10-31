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

import os
import re
from Jetson import io
from Utils import dtc


def _parse_pinmux_pins(dts, path, prefix, pinmux, pins, pingroups):
    path_nodes = path.split('/')
    path_nodes[0] = '/'

    # Parse till the pinmux node
    for n in path_nodes:
        l = dts.readline()
        while not re.search(r'[\s]*%s {$' % n, l):
            l = dts.readline()

    # Parse the pin nodes here and return
    while True:
        l = dts.readline()
        if re.search(r'[\s]*};$', l):
            # End of pinmux node
            break

        # Start of a node
        res = re.match(r'[\s]*([\S]+) {$', l)
        if res is None:
            continue

        node = res.groups()[0]
        # Check whether this is a pin node
        res = re.match(r'%s-pin([0-9]+).*' % prefix, node)
        if res is None:
            # Not a pin node, so parse it out till the end
            while True:
                p = dts.readline()
                if re.search(r'[\s]*};$', p):
                    break
            continue

        pin_num = int(res.groups()[0])
        name = None
        function = None
        group = None
        label = None

        # Pin node identified; parse to the end while reading properties
        while True:
            p = dts.readline()
            if re.search(r'[\s]*};$', p):
                # End of pin node
                break

            res = re.match(r'[\s]*([\S]+) = "([\S]+)";$', p)
            if res is None:
                continue

            prop = res.groups()[0]
            value = res.groups()[1]
            if prop == 'nvidia,pins':
                name = value
            elif prop == 'nvidia,function':
                function = value
            elif prop == 'nvidia,pin-group':
                group = value
            elif prop == 'nvidia,pin-label':
                label = value

        if name is None:
            continue

        node = os.path.join(path, node, '')
        if function is None:
        # Fixed function or always on pins
            pins.add_fixed(pinmux, name, pin_num, node, label)
        else:
        # Configurable pins, associated with pin groups
            if group is None:
                group = function
            pins.add_configurable(pinmux, name, pin_num, node,
                                  function, label)
            pingroups.add(group, function, name)


def _header_parse_pinmap(dtbo, prefix, pinmux, pins, pingroups):
    if dtbo is None:
        return

    pinmux_node_path = dtc.get_prop_value(dtbo, '/__symbols__/',
                                          'jetson_io_pinmux', 0)
    if pinmux_node_path is None:
        raise RuntimeError("Node 'jetson_io_pinmux' not found in %s!" % dtbo)

    temp_file = 'jetson-io-overlay.dts'
    dtc.extract(dtbo, temp_file)
    try:
        with open(temp_file, 'r') as f:
            _parse_pinmux_pins(f, pinmux_node_path,
                               prefix, pinmux, pins, pingroups)
    except:
        raise
    finally:
        os.remove(temp_file)


class _HeaderPins(object):
    def __init__(self, header):
        self.count = header.pin_count
        self.names = {}
        self.nodes = {}
        self.labels = {}
        self.pins = {}

        for pin_num in header.static_pins.keys():
            name = 'static_pin_' + str(pin_num)
            self.names[pin_num] = name
            self.labels[name] = header.static_pins[pin_num]

    def add_fixed(self, pinmux, name, pin_num, node, label):
        if pin_num > self.count:
            raise IndexError("Invalid pin number %d!" % pin_num)
        if pin_num in self.names.keys():
            raise NameError("Duplicate definitions for pin%d!" % pin_num)

        if label is not None:
            self.labels[name] = label
        else:
            self.labels[name] = pinmux.pin_get_function(name)
        self.names[pin_num] = name
        self.nodes[name] = node

    def add_configurable(self, pinmux, name, pin_num, node, \
                         function, label):
        if pin_num > self.count:
            raise IndexError("Invalid pin number %d!" % pin_num)

        # Configurable pins have 'nvidia,function' property specified; there
        # may be more than one node with the same pin name, which allows to
        # expose more than one SFIO on the pin; io.Pin instance is created
        # only for the first node with a unique pin name and all nodes with
        # the same pin name are pushed into a list; as per user configuration
        # the node matching the selected function should be pulled out
        if pin_num not in self.names.keys():
            functions = pinmux.pin_get_all_functions(name)
            curr_func = pinmux.pin_get_function(name)
            enabled = pinmux.pin_is_enabled(name)
            self.pins[name] = io.Pin(name, enabled, curr_func, functions)
            self.names[pin_num] = name

            self.nodes[name] = {}
            self.labels[name] = {}
            self.nodes[name][function] = node
            self.labels[name][function] = label
        elif name in self.pins.keys():
            if (self.names[pin_num] != name):
                raise NameError("Mismatching name %s @ pin%d!" \
                                % (name, pin_num))
            if function in self.nodes[name].keys():
                raise NameError("Duplicate definitions for pin%d (%s)!" \
                                % (pin_num, function))
            self.nodes[name][function] = node
            self.labels[name][function] = label
        else:
            raise NameError("Invalid definition for pin%d!" % pin_num)

        if function not in self.pins[name].get_functions():
            raise NameError("Invalid function %s for pin%d %s!" \
                            % (function, pin_num, name))

    def disable(self, name):
        if name not in self.pins.keys():
            raise NameError("Unknown pin %s!" % name)
        self.pins[name].disable()

    def disable_all(self):
        for name in self.pins.keys():
            self.disable(name)

    def get_count(self):
        return self.count

    def get_function(self, name):
        if name not in self.pins.keys():
            raise NameError("Unknown pin %s!" % name)
        return self.pins[name].get_function()

    def get_functions(self, name):
        if name not in self.pins.keys():
            raise NameError("Unknown pin %s!" % name)
        return self.pins[name].get_functions()

    def get_pin_indices(self, names):
        indices = []
        for pin_num in self.names.keys():
            if self.names[pin_num] in names:
                indices.append(pin_num)
        return indices

    def get_name(self, pin_num):
        if pin_num > self.count:
            raise IndexError("Invalid pin number %d!" % pin_num)
        if pin_num in self.names.keys():
            return self.names[pin_num]
        return None

    def get_node(self, name, function):
        if name not in self.nodes.keys():
            raise NameError("Unknown pin %s!" % name)
        if function is None:
            return self.nodes[name]
        if function in self.nodes[name].keys():
            return self.nodes[name][function]
        raise NameError("No node found for pin %s, function %s!" \
                        % (name, function))

    def get_all_nodes(self, name):
        if name not in self.nodes.keys():
            raise NameError("Unknown pin %s!" % name)
        return self.nodes[name]

    def get_default_node(self, name):
        if name not in self.pins.keys():
            raise NameError("Unknown pin %s!" % name)
        if not self.is_enabled(name) and self.is_default(name):
            return list(self.nodes[name].values())[0]
        function = self.pins[name].get_default_function()
        return self.get_node(name, function)

    def get_label(self, name, function=None):
        if name not in self.labels.keys():
            raise NameError("Unknown pin %s!" % name)
        if function is None:
            return self.labels[name]
        if function not in self.labels[name].keys():
            raise NameError("Unknown function %s for pin %s!" \
                            % (function, name))
        return self.labels[name][function]

    def is_configurable(self, name):
        return name in self.pins.keys()

    def is_default(self, name):
        if name not in self.pins.keys():
            raise NameError("Unknown pin %s!" % name)
        return self.pins[name].is_default()

    def is_enabled(self, name):
        if name not in self.pins.keys():
            raise NameError("Unknown pin %s!" % name)
        return self.pins[name].is_enabled()

    def are_default(self):
        for name in self.pins.keys():
            if not self.is_default(name):
                return False
        return True

    def set_default_all(self):
        for name in self.pins.keys():
            self.pins[name].set_default()

    def set_function(self, name, function):
        if name not in self.pins.keys():
            raise NameError("Unknown pin %s!" % name)
        return self.pins[name].set_function(function)


class Header(object):
    def __init__(self, dtbo, header, preconf_pins, pinmux):
        self.prefix = header.prefix
        self.preconf_pins = preconf_pins
        self.pins = _HeaderPins(header)
        self.pingroups = io.PinGroups()
        _header_parse_pinmap(dtbo, self.prefix, pinmux,
                             self.pins, self.pingroups)

    def pin_count(self):
        return self.pins.get_count()

    def pin_get_function(self, name):
        return self.pins.get_function(name)

    def pin_set_function(self, pin, function):
        name = self.pins.get_name(pin)
        if name is None:
            raise NameError("Cannot configure pin%d!" % pin)
        return self.pins.set_function(name, function)

    def pin_get_node(self, name, function=None):
        return self.pins.get_node(name, function)

    def pin_get_all_nodes(self, name):
        return self.pins.get_all_nodes(name)

    def pin_get_default_node(self, name):
        return self.pins.get_default_node(name)

    def pin_get_label(self, pin):
        name = self.pins.get_name(pin)
        if name is None:
            return 'NA'
        if not self.pins.is_configurable(name):
            return self.pins.get_label(name)
        if self.pins.is_enabled(name):
            function = self.pins.get_function(name)
            label = self.pins.get_label(name, function)
            if label is not None:
                return label
            if self.pingroups.pin_is_group(name, function):
                return self.pingroups.get_group(name, function)
            return function
        return 'unused'

    def pin_configured_by_dt(self, pin):
        if self.preconf_pins and (pin in self.preconf_pins):
            return True
        return False

    def pin_is_default(self, name):
        return self.pins.is_default(name)

    def pin_is_enabled(self, name):
        return self.pins.is_enabled(name)

    def pins_are_default(self):
        return self.pins.are_default()

    def pins_set_default(self):
        self.pins.set_default_all()

    def pins_reset(self):
        self.pins.disable_all()

    def pingroups_available(self):
        return sorted(self.pingroups.get_available())

    def pingroup_enable(self, group):
        pins = self.pingroups.get_pins(group)
        function = self.pingroups.get_function(group)
        for pin in pins:
            current = self.pins.get_function(pin)
            if current == function:
                continue
            if self.pins.is_enabled(pin):
                group = self.pingroups.get_group(pin, current)
                if group is not None:
                    self.pingroup_disable(current)
        for pin in pins:
            self.pins.set_function(pin, function)

    def pingroup_disable(self, group):
        pins = self.pingroups.get_pins(group)
        for pin in pins:
            self.pins.disable(pin)

    def pingroup_is_enabled(self, group):
        pins = self.pingroups.get_pins(group)
        function = self.pingroups.get_function(group)
        for pin in pins:
            if self.pins.get_function(pin) != function:
                return False
        return True

    def pingroup_get_pins(self, group):
        pins = self.pingroups.get_pins(group)
        indices = self.pins.get_pin_indices(pins)
        return ','.join(map(str, sorted(indices)))
