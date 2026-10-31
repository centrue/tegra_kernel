#!/usr/bin/env python3
"""Run on the Nano under ssh -Y; display Argus video on the client X server.

GStreamer renders with ximagesink (no remote GLX context required).
The lens FD is kept open throughout preview. Optional Unix socket commands:
focus N, status, stop. Socket permissions are restricted to the current user.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--focus',type=int,default=688)
    ap.add_argument('--width',type=int,default=960)
    ap.add_argument('--height',type=int,default=540)
    ap.add_argument('--fps',type=int,default=15)
    ap.add_argument('--exposure-ns',type=int,help='fixed sensor exposure; omit for AE')
    ap.add_argument('--gain',type=float,help='fixed sensor analog gain; omit for AE')
    ap.add_argument('--isp-digital-gain',type=float,help='fixed ISP gain for controlled comparisons')
    ap.add_argument('--socket',default='/tmp/imx708-preview.sock')
    ap.add_argument('--output',type=Path,default=Path.home()/'imx708-deploy/x11-preview')
    args=ap.parse_args()
    if not os.environ.get('DISPLAY'):
        ap.error('DISPLAY is empty; connect using ssh -Y')
    if not 0<=args.focus<=1023 or min(args.width,args.height,args.fps)<1:
        ap.error('invalid focus, size or fps')
    if any(v is not None and v<=0 for v in (args.exposure_ns,args.gain,args.isp_digital_gain)):
        ap.error('exposure and gains must be positive')
    path=Path(args.socket)
    if path.exists():
        ap.error('control socket already exists; stop the previous preview first')
    args.output.mkdir(parents=True,exist_ok=True)
    running=True
    def stop(*_):
        nonlocal running
        running=False
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    signal.signal(signal.SIGHUP,stop)
    lens=None;producer=player=None;logger=None
    with tempfile.TemporaryDirectory(prefix='imx708-x11-') as tmp:
        logs=[open(args.output/'argus.log','wb')]
        server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            server.bind(args.socket);os.chmod(args.socket,0o600);server.listen(2)
            controls=[]
            if args.exposure_ns is not None:
                controls.append('exposuretimerange=%d %d'%(args.exposure_ns,args.exposure_ns))
            if args.gain is not None:
                controls.append('gainrange=%g %g'%(args.gain,args.gain))
            if args.isp_digital_gain is not None:
                controls.append('ispdigitalgainrange=%g %g'%(args.isp_digital_gain,args.isp_digital_gain))
            gst=['gst-launch-1.0','-e','nvarguscamerasrc','sensor-id=0','sensor-mode=1',*controls,
                 '!','video/x-raw(memory:NVMM),width=2304,height=1296,format=NV12,framerate=%d/1'%args.fps,
                 '!','nvvidconv','!',
                 'video/x-raw,format=I420,width=%d,height=%d,colorimetry=bt709'%(args.width,args.height),
                 '!','videoconvert','!','ximagesink','name=imx708_preview',
                 'display='+os.environ['DISPLAY'],'sync=false','force-aspect-ratio=true']
            # nvbuf_utils must use headless NVIDIA EGL, not the forwarded X
            # display. Only ximagesink receives the remote X display explicitly.
            env=dict(os.environ)
            env.pop('DISPLAY',None)
            producer=subprocess.Popen(gst,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env)
            player=producer
            connected=threading.Event()
            def record_output():
                for line in producer.stdout:
                    logs[0].write(line);logs[0].flush()
                    if b'Producer has connected' in line:
                        connected.set()
            logger=threading.Thread(target=record_output,daemon=True)
            logger.start()
            if not connected.wait(15):
                raise RuntimeError('Argus did not start streaming; see '+str(args.output))
            if producer.poll() is not None or player.poll() is not None:
                raise RuntimeError('preview pipeline failed; see '+str(args.output))
            node=next('/dev/'+p.parent.name for p in Path('/sys/class/video4linux').glob('v4l-subdev*/name')
                      if 'dw9817' in p.read_text())
            lens=os.open(node,os.O_RDWR)
            def focus(value):
                if not 0<=value<=1023:raise ValueError('focus must be0..1023')
                fcntl.ioctl(lens,0xc008561c,struct.pack('Ii',0x009a090a,value))
                args.focus=value
            focus(args.focus)
            (args.output/'commands.json').write_text(json.dumps(dict(gst=gst,display=os.environ['DISPLAY']),indent=2)+'\n')
            print('READY: IMX708 Nano - X11; focus=%d; control=%s'%(args.focus,args.socket),flush=True)
            while running and producer.poll() is None and player.poll() is None:
                if select.select([server],[],[],.5)[0]:
                    connection,_=server.accept()
                    with connection:
                        connection.settimeout(2)
                        try:
                            command=connection.recv(128).decode().strip().split()
                            if len(command)==2 and command[0]=='focus':focus(int(command[1]))
                            elif command==['stop']:running=False
                            elif command!=['status']:raise ValueError('use focus N, status or stop')
                            connection.sendall(('focus=%d\n'%args.focus).encode())
                        except (OSError,ValueError) as error:
                            connection.sendall(('ERROR: %s\n'%error).encode())
        finally:
            if lens is not None:os.close(lens)
            for proc in set([player,producer]):
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                    try:proc.wait(timeout=4)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait()
            server.close()
            path.unlink(missing_ok=True)
            if logger is not None:logger.join(timeout=2)
            for log in logs:log.close()

if __name__=='__main__':main()
