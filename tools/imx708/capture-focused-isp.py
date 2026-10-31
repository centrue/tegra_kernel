#!/usr/bin/env python3
"""Capture I420 through Argus with an actively held, verified lens position.

Starts the sensor first (Camera Module 3 shares the enable signal), then
opens the lens. The saved frame is captured before parking the lens.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import tempfile
import time
from camera_io import read_register

def find_lens():
    for entry in Path('/sys/class/video4linux').glob('v4l-subdev*'):
        if 'dw9817' in (entry/'name').read_text():
            return '/dev/'+entry.name
    raise RuntimeError('DW9817 lens node missing')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--lens',default='auto')
    ap.add_argument('--position',type=int,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--width',type=int,default=2304)
    ap.add_argument('--height',type=int,default=1296)
    ap.add_argument('--sensor',type=int,default=0)
    ap.add_argument('--mode',type=int,default=1)
    ap.add_argument('--fps',type=int,default=30)
    ap.add_argument('--exposure-ns',type=int,default=20000000)
    ap.add_argument('--gain',type=float,default=2)
    ap.add_argument('--frames',type=int,default=60)
    ap.add_argument('--argus-property',action='append',default=[])
    ap.add_argument('--bus',default='/dev/i2c-6')
    args=ap.parse_args()
    if not 0<=args.position<=1023 or min(args.width,args.height,args.fps,args.frames)<1:
        ap.error('invalid position, size, fps or frames')
    args.output.mkdir(parents=True,exist_ok=False)
    lens_path=find_lens() if args.lens=='auto' else args.lens
    with tempfile.TemporaryDirectory(prefix='imx708-isp-') as tmp:
        fifo=Path(tmp)/'frames'
        os.mkfifo(fifo)
        fd=os.open(fifo,os.O_RDONLY|os.O_NONBLOCK)
        cmd=['gst-launch-1.0','-e','-v','nvarguscamerasrc','sensor-id=%d'%args.sensor,
             'sensor-mode=%d'%args.mode,
             'exposuretimerange=%d %d'%(args.exposure_ns,args.exposure_ns),
             'gainrange=%g %g'%(args.gain,args.gain),*args.argus_property,'!',
             'video/x-raw(memory:NVMM),width=%d,height=%d,format=NV12,framerate=%d/1'%(args.width,args.height,args.fps),
             '!','nvvidconv','!','video/x-raw,format=I420,colorimetry=bt709','!',
             'filesink','location='+str(fifo)]
        lens=None
        size=args.width*args.height*3//2
        log=open(args.output/'capture.log','wb')
        proc=subprocess.Popen(cmd,stdout=log,stderr=log)
        def frame():
            data=bytearray(); deadline=time.monotonic()+15
            while len(data)<size:
                if time.monotonic()>deadline:
                    raise TimeoutError('ISP capture timed out; see capture.log')
                if proc.poll() is not None:
                    raise RuntimeError('ISP pipeline stopped: %s'%proc.returncode)
                if select.select([fd],[],[],1)[0]:
                    chunk=os.read(fd,min(size-len(data),1024*1024))
                    if chunk:data.extend(chunk)
                    else:time.sleep(.01)
            return bytes(data)
        try:
            frame()
            lens=os.open(lens_path,os.O_RDWR)
            fcntl.ioctl(lens,0xc008561c,struct.pack('Ii',0x009a090a,args.position))
            for _ in range(args.frames):
                data=frame()
            dac=read_register(args.bus,0x0c,3,1,2)&1023
            if dac!=args.position:
                raise RuntimeError('lens DAC %d != %d'%(dac,args.position))
            (args.output/'last.i420').write_bytes(data)
            record=dict(command=cmd,lens=lens_path,requested_dac=args.position,actual_dac=dac,
                        width=args.width,height=args.height,format='I420',colorimetry='bt709',
                        exposure_ns=args.exposure_ns,gain=args.gain,settling_frames=args.frames)
            (args.output/'metadata.json').write_text(json.dumps(record,indent=2)+'\n')
            print(json.dumps(record),flush=True)
        finally:
            if lens is not None:os.close(lens)
            # Close the reader before signalling, so a blocked FIFO writer wakes.
            os.close(fd)
            proc.send_signal(signal.SIGINT)
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill();proc.wait()
            log.close()

if __name__=='__main__':main()
