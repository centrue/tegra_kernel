# SPDX-License-Identifier: GPL-2.0-only
"""Validated live camera/post-ISP controls and reversible JSON presets."""
import hashlib
import json
import math
import os
from pathlib import Path


DEFAULTS=dict(auto_exposure=False,auto_gain=False,exposure_ms=10.,gain=2.,isp_gain=1.,
              wbmode=1,awblock=False,exposure_compensation=0.,saturation=1.,
              red=.5,green=.5,blue=.5,brightness=0.,contrast=1.,gamma=1.)
RANGES=dict(exposure_ms=(.5,66.666666),gain=(1.125,16),isp_gain=(1,8),
            exposure_compensation=(-2,2),saturation=(0,2),red=(0,1),green=(0,1),
            blue=(0,1),brightness=(-1,1),contrast=(0,2),gamma=(.1,3))


def hardware_state():
    """Read sensor registers rather than presenting requested exposure as actual."""
    try:
        from camera_io import read_register
        mode=Path('/sys/bus/i2c/devices/6-001a/of_node/mode1')
        clock=int((mode/'pix_clk_hz').read_bytes().strip(b'\0'))
        fine,coarse,gain,frame,line=[read_register('/dev/i2c-6',0x1a,r,2,2)
                                   for r in (0x0200,0x0202,0x0204,0x0340,0x0342)]
        return dict(exposure_ms=round((coarse*line+fine)/clock*1000,3),
                    gain=round(1024/(1024-gain),3),frame_ms=round(frame*line/clock*1000,3),
                    coarse_lines=coarse,frame_lines=frame)
    except (OSError,ValueError,ZeroDivisionError) as error:
        return dict(error=str(error))


class Settings:
    def __init__(self,camera,rgb,balance,gamma,worker,output,fps=15,initial=None,emit=None):
        self.camera,self.rgb,self.balance,self.gamma=camera,rgb,balance,gamma
        self.worker=worker;self.output=Path(output);self.fps=fps
        self.values=dict(DEFAULTS,**(initial or {}));self.initial=dict(self.values)
        self.emit=emit or (lambda record:None)
        self.preset=self.output/'settings.json'

    def validate(self,changes):
        if not isinstance(changes,dict) or set(changes)-(DEFAULTS.keys()|{'focus'}):
            raise ValueError('unknown or invalid settings')
        if 'focus' in changes and (type(changes['focus']) is not int or not 0<=changes['focus']<=1023):
            raise ValueError('focus must be integer0..1023')
        values=dict(self.values,**{k:v for k,v in changes.items() if k!='focus'})
        for key,value in values.items():
            if key in ('auto_exposure','auto_gain','awblock'):
                if type(value) is not bool:raise ValueError(key+' must be boolean')
            elif key=='wbmode':
                if type(value) is not int or not 0<=value<=8:raise ValueError('wbmode must be0..8')
            else:
                if type(value) not in (int,float) or not math.isfinite(value):raise ValueError(key+' must be finite')
                low,high=RANGES[key]
                if key=='exposure_ms':high=min(high,1000/self.fps)
                if not low<=value<=high:raise ValueError('%s must be%g..%g'%(key,low,high))
        return values

    def get(self):
        status=self.worker.status()
        isp=Path('/var/nvidia/nvcam/settings/camera_overrides.isp')
        return dict(self.values,focus=status['position'],af_mode=status['mode'],af_state=status['state'],
                    roi=list(getattr(self.worker,'roi',(.25,.25,.5,.5))),output=str(self.output),
                    preset_path=str(self.preset),
                    hardware=hardware_state(),
                    isp_sha256=hashlib.sha256(isp.read_bytes()).hexdigest() if isp.exists() else None)

    def apply(self,changes):
        if isinstance(changes,dict) and 'auto_exposure' in changes and 'auto_gain' not in changes:
            changes=dict(changes,auto_gain=changes['auto_exposure'])
        values=self.validate(changes)
        # Do not let an AF search retain AE/AWB ownership during manual edits.
        self.worker.command('mode','off')
        exposure=round(values['exposure_ms']*1e6)
        max_exposure=min(499000000,int(1e9/self.fps))
        properties=[(self.camera,'exposuretimerange','500000 %d'%max_exposure if values['auto_exposure'] else '%d %d'%(exposure,exposure)),
                    (self.camera,'gainrange','1.125 16' if values['auto_gain'] else '%g %g'%(values['gain'],values['gain'])),
                    (self.camera,'ispdigitalgainrange','%g %g'%(values['isp_gain'],values['isp_gain'])),
                    (self.camera,'wbmode',values['wbmode']),
                    (self.camera,'awblock',values['awblock']),
                    (self.camera,'exposurecompensation',values['exposure_compensation']),
                    (self.camera,'saturation',values['saturation']),
                    (self.rgb,'r',values['red']),(self.rgb,'g',values['green']),(self.rgb,'b',values['blue']),
                    (self.balance,'brightness',values['brightness']),
                    (self.balance,'contrast',values['contrast']),
                    (self.gamma,'gamma',values['gamma'])]
        previous=[(element,key,element.get_property(key)) for element,key,_ in properties]
        try:
            for element,key,value in properties:element.set_property(key,value)
            if 'focus' in changes:self.worker.command('focus',changes['focus'])
        except Exception as error:
            failures=[]
            for element,key,value in previous:
                try:element.set_property(key,value)
                except Exception as rollback:failures.append('%s:%s'%(key,rollback))
            raise RuntimeError('settings failed: %s; rollback errors=%s'%(error,failures)) from error
        self.values=values
        self.emit(dict(event='settings',settings=values))
        return self.get()

    def save(self):
        self.output.mkdir(parents=True,exist_ok=True)
        state=self.get()
        payload=dict(version=1,settings=dict(self.values),focus=state['focus'],roi=state['roi'],
                     isp_sha256=state['isp_sha256'])
        temporary=self.preset.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(payload,indent=2)+'\n')
        os.replace(temporary,self.preset)
        return state

    def load(self):
        payload=json.loads(self.preset.read_text())
        if payload.get('version')!=1:raise ValueError('unsupported preset version')
        self.validate(payload['settings'])
        focus=payload['focus']
        if type(focus) is not int or not 0<=focus<=1023:raise ValueError('invalid preset focus')
        roi=payload.get('roi',[.25,.25,.5,.5])
        if (len(roi)!=4 or not all(type(v) in (int,float) and math.isfinite(v) for v in roi) or
            min(roi[:2])<0 or min(roi[2:])<=0 or roi[0]+roi[2]>1 or roi[1]+roi[3]>1):
            raise ValueError('invalid preset ROI')
        self.apply(payload['settings']);self.worker.command('focus',focus)
        self.worker.command('roi',tuple(roi))
        return self.get()

    def reset(self):return self.apply(self.initial)
