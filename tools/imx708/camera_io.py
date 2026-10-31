"""Small Linux I2C_RDWR helpers; reads bypass the kernel register cache."""
import ctypes
import fcntl
import os

class Message(ctypes.Structure):
    _fields_ = [('addr', ctypes.c_uint16), ('flags', ctypes.c_uint16),
                ('length', ctypes.c_uint16), ('buffer', ctypes.c_void_p)]

class Transfer(ctypes.Structure):
    _fields_ = [('messages', ctypes.POINTER(Message)), ('count', ctypes.c_uint32)]

def read_register(bus, chip, register, address_bytes=2, count=1):
    address = (ctypes.c_uint8 * address_bytes)(*register.to_bytes(address_bytes, 'big'))
    data = (ctypes.c_uint8 * count)()
    messages = (Message * 2)(Message(chip, 0, address_bytes, ctypes.addressof(address)),
                             Message(chip, 1, count, ctypes.addressof(data)))
    fd = os.open(bus, os.O_RDWR)
    try:
        fcntl.ioctl(fd, 0x0707, Transfer(messages, 2))
        return int.from_bytes(bytes(data), 'big')
    finally:
        os.close(fd)
