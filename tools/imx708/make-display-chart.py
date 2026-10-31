#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Make an exact sRGB screen target, NOT a certified reflective ColorChecker.

Patch sRGB values from public_sources/ov5647-jetson/tools/isp/colorchecker.py
(Copyright 2026 Artur Andrzejczak, GPL-2.0). Use SVG rendering, not image AI,
so patch values, coordinates and neutral steps remain reproducible.
"""
import argparse
import json
from pathlib import Path
import subprocess

COLORS = [(115,82,68),(194,150,130),(98,122,157),(87,108,67),(133,128,177),(103,189,170),
          (214,126,44),(80,91,166),(193,90,99),(94,60,108),(157,188,64),(224,163,46),
          (56,61,150),(70,148,73),(175,54,60),(231,199,31),(187,86,149),(8,133,161),
          (243,243,242),(200,200,200),(160,160,160),(122,122,121),(85,85,85),(52,52,52)]

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,default=Path('tmp/imx708-display-chart'))
    args=ap.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    svg=['<svg xmlns="http://www.w3.org/2000/svg" width="2400" height="1600" viewBox="0 0 2400 1600">',
         '<rect width="2400" height="1600" fill="#808080"/>']
    def rect(x,y,w,h,color):
        svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{color}"/>')
    def text(x,y,value,size=28,fill='white',anchor='middle'):
        svg.append(f'<text x="{x}" y="{y}" font-family="sans-serif" font-size="{size}" text-anchor="{anchor}" fill="{fill}">{value}</text>')
    text(1200,80,'IMX708 DISPLAY TEST',54)
    text(1200,132,'sRGB SCREEN REFERENCE / NOT A CALIBRATED COLORCHECKER',26)
    for x0 in (42,2230):
        for y in range(8):
            for x in range(8):
                rect(x0+16*x,42+16*y,16,16,'white' if (x+y)%2 else 'black')
    for n,bar in enumerate([2,3,4,6,8,12,16]):
        x0=430+n*220
        rect(x0,170,180,56,'white')
        for x in range(0,180,2*bar):
            rect(x0+x,170,min(bar,180-x),56,'black')
    patches=[]
    for i,rgb in enumerate(COLORS):
        row,col=divmod(i,6)
        x,y=150+350*col,260+255*row
        rect(x,y,320,225,'#%02x%02x%02x'%rgb)
        text(x+12,y+27,str(i+1),22,'black' if sum(rgb)>430 else 'white','start')
        patches.append(dict(index=i+1,rect=[x,y,320,225],center=[x+160,y+112.5],srgb=list(rgb)))
    for i,g in enumerate([0,16,32,64,96,128,160,200,255]):
        x=150+235*i
        rect(x,1320,220,100,'#%02x%02x%02x'%(g,g,g))
        text(x+110,1458,str(g),26)
    text(1200,1518,'LANDSCAPE / STANDARD COLOR MODE / NO NIGHT SHIFT / FIX BRIGHTNESS',26)
    text(1200,1560,'Keep every patch and all four chart corners visible. Avoid glare and clipping.',24)
    svg.append('</svg>')
    svgpath=args.output.with_suffix('.svg')
    svgpath.write_text('\n'.join(svg)+'\n')
    subprocess.run(['rsvg-convert','-o',str(args.output.with_suffix('.png')),str(svgpath)],check=True)
    args.output.with_suffix('.json').write_text(json.dumps(dict(width=2400,height=1600,
        reference_kind='display',calibrated=False,patches=patches,
        note='Screen spectrum, brightness, color management and ambient reflections affect measurements.'),indent=2)+'\n')
    print(args.output.with_suffix('.png'))

if __name__=='__main__':
    main()
