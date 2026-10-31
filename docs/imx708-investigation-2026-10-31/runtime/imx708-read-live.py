import ctypes, fcntl, os
class Msg(ctypes.Structure):
 _fields_=[('addr',ctypes.c_uint16),('flags',ctypes.c_uint16),('len',ctypes.c_uint16),('buf',ctypes.c_void_p)]
class Data(ctypes.Structure):
 _fields_=[('msgs',ctypes.POINTER(Msg)),('nmsgs',ctypes.c_uint32)]
fd=os.open('/dev/i2c-6',os.O_RDWR)
for reg in [0x0016,0x0017,0x0100,0x0112,0x0113,0x0114,0x0202,0x0203,0x0204,0x0205,0x030e,0x030f,0x0340,0x0341,0x0342,0x0343,0x034c,0x034d,0x034e,0x034f,0x3220]:
 addr=(ctypes.c_uint8*2)(reg>>8,reg&255); val=(ctypes.c_uint8*1)()
 msgs=(Msg*2)(Msg(0x1a,0,2,ctypes.addressof(addr)),Msg(0x1a,1,1,ctypes.addressof(val)))
 try:
  fcntl.ioctl(fd,0x0707,Data(msgs,2)); print('0x%04x=0x%02x'%(reg,val[0]))
 except OSError as e: print(hex(reg),e)
os.close(fd)
