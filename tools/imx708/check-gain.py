#!/usr/bin/env python3
"""Verify gain against hardware registers while streaming (run as root)."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import time
from camera_io import read_register

spec = importlib.util.spec_from_file_location('focus_scan', Path(__file__).with_name('focus-scan.py'))
focus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(focus)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--video', default='/dev/video0')
    ap.add_argument('--bus', default='/dev/i2c-6')
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    args.width, args.height, args.mode, args.fps = 2304,1296,1,30
    args.exposure, args.gain = 5000,32
    args.output.mkdir(parents=True,exist_ok=True)
    cap = focus.Capture(args,args.output)
    results=[]
    try:
        cap.frame()
        for request, expected in [(18,114),(32,512),(64,768),(128,896),(256,960)]:
            subprocess.run(['v4l2-ctl','-d',args.video,'--set-ctrl=gain=%d'%request],check=True)
            for _ in range(8):
                raw=cap.frame()
            actual=read_register(args.bus,0x1a,0x0204,2,2)
            result=dict(request=request,expected=expected,actual=actual,match=actual==expected,
                        raw=focus.measure(raw,args.width,args.height))
            results.append(result)
            print(json.dumps(result),flush=True)
        (args.output/'gain.json').write_text(json.dumps(results,indent=2)+'\n')
    finally:
        cap.close()
    if not all(r['match'] for r in results):
        raise SystemExit('gain register verification FAILED')

if __name__=='__main__':
    main()
