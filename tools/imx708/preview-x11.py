#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Argus X11 preview with continuous contrast AF, running on Nano under ssh -Y.

One pipeline feeds independent display and latest-frame analysis branches.
The lens stays open until shutdown. Socket: focus N, status, af
continuous|once|stop|status, snapshot, stop. --headless omits the X11 window.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import threading
import time

from af_runtime import CameraLocks, Mailbox, NanoLens, Worker, i420_bytes, luma_bytes


def shutdown_camera(worker, restore_locks, stop_pipeline):
    if worker is not None:
        worker.close()
    # Lens closure has completed before either operation can stop sensor power.
    try:
        restore_locks()
    finally:
        stop_pipeline()


def control_command(worker, command):
    words = command.split()
    if words == ['status']:
        return 'focus=%d\n' % worker.status()['position']
    if len(words) == 2 and words[0] == 'focus':
        value = int(words[1])
        if not 0 <= value <= 1023:
            raise ValueError('focus must be0..1023')
        return 'focus=%d\n' % worker.command('focus', value)['position']
    if len(words) == 2 and words[0] == 'af':
        if words[1] == 'status':
            result = worker.status()
        elif words[1] in ('continuous', 'once', 'stop'):
            result = worker.command('mode', 'off' if words[1] == 'stop' else words[1])
        else:
            raise ValueError('use af continuous, once, stop or status')
        return json.dumps(result)+'\n'
    if words == ['snapshot']:
        return json.dumps(worker.command('snapshot'))+'\n'
    raise ValueError('use focus N, status, af continuous|once|stop|status, snapshot or stop')


def build_pipeline(args, display):
    controls = []
    if args.exposure_ns is not None:
        controls.append('exposuretimerange="%d %d"' % (args.exposure_ns, args.exposure_ns))
    if args.gain is not None:
        controls.append('gainrange="%g %g"' % (args.gain, args.gain))
    if args.isp_digital_gain is not None:
        controls.append('ispdigitalgainrange="%g %g"' % (args.isp_digital_gain, args.isp_digital_gain))
    sink = ('fakesink sync=false' if args.headless else
            'videoconvert ! ximagesink name=imx708_preview display='+json.dumps(display)+
            ' sync=false force-aspect-ratio=true')
    return ('nvarguscamerasrc name=camera sensor-id=0 sensor-mode=1 '+ ' '.join(controls)+
            ' ! video/x-raw(memory:NVMM),width=2304,height=1296,format=NV12,framerate=%d/1'%args.fps+
            ' ! nvvidconv ! video/x-raw,format=I420,width=%d,height=%d,colorimetry=bt709'%(args.width,args.height)+
            ' ! tee name=t t. ! queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 leaky=downstream ! '+sink+
            ' t. ! queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 leaky=downstream'
            ' ! appsink name=analysis emit-signals=true max-buffers=1 drop=true sync=false wait-on-eos=false')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--focus', type=int, default=688)
    ap.add_argument('--width', type=int, default=960)
    ap.add_argument('--height', type=int, default=540)
    ap.add_argument('--fps', type=int, default=15)
    ap.add_argument('--exposure-ns', type=int)
    ap.add_argument('--gain', type=float)
    ap.add_argument('--isp-digital-gain', type=float)
    ap.add_argument('--autofocus', choices=('off', 'once', 'continuous'), default='off')
    ap.add_argument('--af-roi', default='.25,.25,.5,.5', help='normalized x,y,width,height')
    ap.add_argument('--af-range', default='352:928', help='minimum:maximum DAC')
    ap.add_argument('--bus', default='/dev/i2c-6')
    ap.add_argument('--headless', action='store_true')
    ap.add_argument('--socket', default='/tmp/imx708-preview.sock')
    ap.add_argument('--output', type=Path, default=Path.home()/'imx708-deploy/x11-preview')
    args = ap.parse_args()
    display = os.environ.get('DISPLAY', '')
    if not display and not args.headless:
        ap.error('DISPLAY is empty; connect using ssh -Y or use --headless')
    if not 0 <= args.focus <= 1023 or min(args.width, args.height, args.fps) < 1 or args.width%2 or args.height%2:
        ap.error('invalid focus, size or fps')
    if any(v is not None and v <= 0 for v in (args.exposure_ns,args.gain,args.isp_digital_gain)):
        ap.error('exposure and gains must be positive')
    try:
        roi = tuple(float(v) for v in args.af_roi.split(','))
        limits = tuple(int(v) for v in args.af_range.split(':'))
        from autofocus import Controller, measure_luma
        Controller(args.focus, limits)
        measure_luma(bytes([100])*args.width*args.height, args.width, args.height, roi=roi)
    except (ValueError, TypeError, IndexError) as error:
        ap.error(str(error))
    path = Path(args.socket)
    if path.exists():
        ap.error('control socket already exists; stop previous preview first')
    args.output.mkdir(parents=True, exist_ok=True)
    # Do this BEFORE Gst.init/plugin loading; only ximagesink gets forwarded X.
    os.environ.pop('DISPLAY', None)
    import gi
    gi.require_version('Gst', '1.0')
    gi.require_version('GstVideo', '1.0')
    from gi.repository import Gst, GstVideo
    Gst.init(None)
    description = build_pipeline(args, display)
    pipeline = Gst.parse_launch(description)
    camera = pipeline.get_by_name('camera')
    mailbox = Mailbox()
    log_lock = threading.Lock()
    journal = open(args.output/'autofocus.jsonl', 'a', buffering=1)
    buslog = open(args.output/'gst-bus.log', 'a', buffering=1)
    def emit(record):
        with log_lock:
            journal.write(json.dumps(record)+'\n')
        if record.get('event') in ('search','locked','unconfirmed','error'):
            print(json.dumps(record), flush=True)
    def save(label, frame, status):
        stem = '%s-%06d' % (label, frame['sequence'])
        (args.output/(stem+'.i420')).write_bytes(i420_bytes(frame))
        header = 'P5\n%d %d\n255\n' % (frame['width'],frame['height'])
        (args.output/(stem+'.pgm')).write_bytes(header.encode()+luma_bytes(frame))
        (args.output/(stem+'.json')).write_text(json.dumps(dict(status, width=frame['width'],
            height=frame['height'], pts=frame['pts'], sequence=frame['sequence'],
            captured_at=frame['captured_at']), indent=2)+'\n')
    set_locks = CameraLocks(camera)
    callback_error = []
    def new_sample(sink):
        try:
            sample = sink.emit('pull-sample')
            if sample is None:
                return Gst.FlowReturn.EOS
            buffer = sample.get_buffer()
            info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
            meta = GstVideo.buffer_get_video_meta(buffer)
            strides = list(meta.stride)[:3] if meta else list(info.stride)[:3]
            offsets = list(meta.offset)[:3] if meta else list(info.offset)[:3]
            clock = pipeline.get_clock()
            if clock is None or buffer.pts == Gst.CLOCK_TIME_NONE:
                return Gst.FlowReturn.OK
            now = time.monotonic()
            age = (clock.get_time()-pipeline.get_base_time()-buffer.pts)/Gst.SECOND
            if age < -.1 or age > 5:
                raise RuntimeError('invalid capture timestamp age=%g'%age)
            ok, mapped = buffer.map(Gst.MapFlags.READ)
            if not ok:
                raise RuntimeError('cannot map I420 frame')
            try:
                data = bytes(mapped.data)
            finally:
                buffer.unmap(mapped)
            mailbox.publish(dict(data=data,width=info.width,height=info.height,
                stride=strides[0],offset=offsets[0],strides=strides,offsets=offsets,
                pts=int(buffer.pts),captured_at=now-max(age,0)))
            return Gst.FlowReturn.OK
        except Exception as error:
            callback_error.append(str(error))
            return Gst.FlowReturn.ERROR
    analysis = pipeline.get_by_name('analysis')
    analysis.connect('new-sample', new_sample)
    running = True
    def stop(*_):
        nonlocal running
        running = False
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, stop)
    worker = None
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    isp = Path('/var/nvidia/nvcam/settings/camera_overrides.isp')
    (args.output/'commands.json').write_text(json.dumps(dict(pipeline=description,
        display=display, autofocus=args.autofocus, roi=roi, limits=limits,
        isp_sha256=hashlib.sha256(isp.read_bytes()).hexdigest() if isp.exists() else None,
        gst_version=Gst.version_string()),indent=2)+'\n')
    bus = pipeline.get_bus()
    try:
        server.bind(str(path))
        os.chmod(path,0o600)
        server.listen(4)
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('GStreamer pipeline failed to start')
        deadline = time.monotonic()+15
        while mailbox.frame is None and running:
            if callback_error:
                raise RuntimeError(callback_error[0])
            message = bus.timed_pop_filtered(int(.2*Gst.SECOND), Gst.MessageType.ERROR|Gst.MessageType.EOS)
            if message is not None:
                raise RuntimeError(str(message.parse_error() if message.type==Gst.MessageType.ERROR else 'EOS'))
            if time.monotonic() > deadline:
                raise TimeoutError('no video frames within15s')
        if not running:
            return
        worker = Worker(mailbox,NanoLens(args.bus),set_locks,args.focus,limits,roi,emit,save)
        worker.start()
        worker.command('mode',args.autofocus)
        print('READY: IMX708 Nano - X11; AF=%s; control=%s'%(args.autofocus,args.socket),flush=True)
        while running:
            if callback_error:
                raise RuntimeError(callback_error[0])
            while True:
                message = bus.pop_filtered(Gst.MessageType.ERROR|Gst.MessageType.WARNING|Gst.MessageType.EOS)
                if message is None:
                    break
                detail = (message.parse_error() if message.type==Gst.MessageType.ERROR else
                          message.parse_warning() if message.type==Gst.MessageType.WARNING else 'EOS')
                buslog.write(str(detail)+'\n')
                if message.type in (Gst.MessageType.ERROR,Gst.MessageType.EOS):
                    raise RuntimeError(str(detail))
            if select.select([server],[],[],.2)[0]:
                connection,_ = server.accept()
                with connection:
                    connection.settimeout(5)
                    try:
                        command = connection.recv(256).decode().strip()
                        if command == 'stop':
                            running = False
                            reply = 'focus=%d\n'%worker.status()['position']
                        else:
                            reply = control_command(worker,command)
                        connection.sendall(reply.encode())
                    except (OSError,ValueError,RuntimeError) as error:
                        connection.sendall(('ERROR: %s\n'%error).encode())
    finally:
        def stop_pipeline():
            pipeline.send_event(Gst.Event.new_eos())
            bus.timed_pop_filtered(int(2*Gst.SECOND), Gst.MessageType.EOS|Gst.MessageType.ERROR)
            pipeline.set_state(Gst.State.NULL)
        try:
            shutdown_camera(worker,lambda:set_locks(False),stop_pipeline)
        finally:
            server.close()
            path.unlink(missing_ok=True)
            journal.close()
            buslog.close()


if __name__ == '__main__':
    main()
