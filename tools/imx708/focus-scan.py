#!/usr/bin/env python3
"""Scan the DW9817 while holding its FD and a continuous RAW capture open.

Uses only the Python standard library on the target. Camera Module 3 shares
the sensor's enable signal: start capture BEFORE opening the lens node.
Closing the last lens FD returns the actuator to its zero-current position.
"""
import argparse
from array import array
import fcntl
import json
import os
from pathlib import Path
import select
import statistics
import struct
import subprocess
import sys
import tempfile
import time

def measure(raw, width, height, roi=None):
    if len(raw) != width * height * 2:
        raise ValueError('truncated or padded frame: supply the actual width/stride')
    values = array('H', raw)
    if sys.byteorder != 'little':
        values.byteswap()
    x0, x1 = width // 4 | 1, 3 * width // 4 - 4
    y0, y1 = (height // 4) & ~1, 3 * height // 4 - 4
    if roi is not None:
        x, y, w, h = roi
        if min(x,y) < 0 or min(w,h) < 12 or x+w>width or y+h>height:
            raise ValueError('ROI must be within the image and at least 12x12')
        x0, x1 = x | 1, x+w-4
        y0, y1 = y & ~1, y+h-4
    total = square = saturated = count = 0
    # Compare samples of the same green plane (RGGB), avoiding Bayer edges.
    for y in range(y0, y1, 4):
        for x in range(x0, x1, 4):
            p = y * width + x
            v = values[p]
            square += (v - values[p+4])**2 + (v-values[p+4*width])**2
            total += v
            saturated += v >= 1000
            count += 1
    if not count:
        raise ValueError('image too small for ROI')
    return dict(score=square/count, mean=total/count, saturated_fraction=saturated/count)

class Capture:
    def __init__(self, args, output):
        self.tmp = tempfile.TemporaryDirectory(prefix='imx708-focus-')
        self.path = Path(self.tmp.name) / 'frames'
        os.mkfifo(self.path)
        self.fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
        self.log = open(output / 'capture.log', 'wb')
        self.command = ['v4l2-ctl', '-d', args.video,
            '--set-fmt-video=width=%d,height=%d,pixelformat=RG10' % (args.width, args.height),
            '--set-ctrl=sensor_mode=%d,bypass_mode=0,override_enable=1,frame_rate=%d,exposure=%d,gain=%d' %
            (args.mode, args.fps*1000000, args.exposure, args.gain),
            '--stream-mmap', '--stream-count=0', '--stream-to='+str(self.path)]
        self.proc = subprocess.Popen(self.command, stdout=self.log, stderr=self.log)
        self.size = args.width * args.height * 2

    def frame(self):
        deadline = time.monotonic() + 12
        data = bytearray()
        while len(data) < self.size:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError('RAW capture timed out; see capture.log')
            if self.proc.poll() is not None:
                raise RuntimeError('capture stopped: %s' % self.proc.returncode)
            ready, _, _ = select.select([self.fd], [], [], min(remaining, 1))
            if not ready:
                continue
            part = os.read(self.fd, min(self.size-len(data), 1024*1024))
            if part:
                data.extend(part)
            else:
                time.sleep(0.01)
        return bytes(data)

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        os.close(self.fd)
        self.log.close()
        self.tmp.cleanup()

def main():
    from camera_io import read_register
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--lens', required=True)
    ap.add_argument('--video', default='/dev/video0')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--bus', default='/dev/i2c-6')
    ap.add_argument('--positions', default='352,384,416,448,480,512,544,576,608,640,672,704')
    ap.add_argument('--fine', action='store_true', help='scan +/-28 in steps of4 around coarse best')
    ap.add_argument('--roi', help='x,y,width,height of a textured focus target')
    ap.add_argument('--width', type=int, default=2304)
    ap.add_argument('--height', type=int, default=1296)
    ap.add_argument('--mode', type=int, default=1)
    ap.add_argument('--fps', type=int, default=30)
    ap.add_argument('--exposure', type=int, default=10000)
    ap.add_argument('--gain', type=int, default=64)
    ap.add_argument('--hold', type=int, default=0, help='hold best focus and capture for this many seconds')
    args = ap.parse_args()
    roi = tuple(int(v) for v in args.roi.split(',')) if args.roi else None
    if roi is not None and len(roi) != 4:
        ap.error('ROI needs x,y,width,height')
    positions = [int(v) for v in args.positions.split(',')]
    if not positions or any(p < 0 or p > 1023 for p in positions):
        ap.error('positions must be within 0..1023')
    args.output.mkdir(parents=True, exist_ok=True)
    cap = Capture(args, args.output)
    lens = None
    records = []
    try:
        cap.frame()  # Sensor enabled and delivering frames before lens I2C.
        lens = os.open(args.lens, os.O_RDWR)
        def scan(points):
            for position in points:
                fcntl.ioctl(lens, 0xc008561c, struct.pack('Ii', 0x009a090a, position))
                for _ in range(6):
                    cap.frame()  # Flush buffered and unsettled frames.
                dac = read_register(args.bus, 0x0c, 3, 1, 2) & 1023
                if dac != position:
                    raise RuntimeError('DAC readback %d != requested %d' % (dac, position))
                samples = []
                for _ in range(3):
                    raw = cap.frame()
                    samples.append(measure(raw, args.width, args.height, roi))
                record = dict(position=position, dac=dac, samples=samples,
                              score=statistics.median(s['score'] for s in samples))
                if any(s['mean'] <= 66 or s['saturated_fraction'] > 0.05 for s in samples):
                    record['valid'] = False
                else:
                    record['valid'] = True
                records.append(record)
                (args.output / ('focus-%04d.raw' % position)).write_bytes(raw)
                (args.output / 'scan.json').write_text(json.dumps(records, indent=2)+'\n')
                print(json.dumps(record), flush=True)
        scan(positions)
        valid = [r for r in records if r['valid']]
        if not valid:
            raise RuntimeError('no valid focus samples: check scene/exposure')
        best = max(valid, key=lambda r:r['score'])
        if args.fine:
            scan(range(max(0,best['position']-28), min(1023,best['position']+28)+1, 4))
            best = max((r for r in records if r['valid']), key=lambda r:r['score'])
        fcntl.ioctl(lens, 0xc008561c, struct.pack('Ii', 0x009a090a, best['position']))
        for _ in range(8):
            cap.frame()
        (args.output / 'best.raw').write_bytes(cap.frame())
        (args.output / 'best.json').write_text(json.dumps(dict(best=best, command=cap.command,
              width=args.width,height=args.height,exposure=args.exposure,gain=args.gain,roi=roi), indent=2)+'\n')
        print('BEST', best['position'], 'score', best['score'], flush=True)
        until = time.monotonic()+args.hold
        while time.monotonic()<until:
            cap.frame()
    finally:
        # Keep the sensor enabled until the lens has parked.
        if lens is not None:
            os.close(lens)
        cap.close()

if __name__ == '__main__':
    main()
