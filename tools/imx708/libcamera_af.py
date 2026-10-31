# SPDX-License-Identifier: BSD-2-Clause
# Copyright (C) 2022-2023 Raspberry Pi Ltd
# Copyright (C) 2026 IMX708 Jetson workspace contributors
#
# Adapted from libcamera src/ipa/rpi/controller/rpi/af.cpp (CDAF branch),
# revision c682979b50695b93526adb1e66db33743484b63b.
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
"""RPi programmed CDAF scans adapted to Nano's timestamped I420 frames.

No PDAF is fabricated. Focus is in dioptres; the tuning map converts to DAC.
The scan sequence, direction reversal, early termination and quadratic peak
follow libcamera. Nano supplies software contrast rather than Pi ISP statistics.
"""
import json
import math
from pathlib import Path

from autofocus import distance


def find_peak(points, index):
    focus=points[index][0]
    if len(points)>=3:
        i=max(1,min(index,len(points)-2))
        abx=points[i-1][0]-points[i][0]
        aby=points[i-1][1]-points[i][1]
        cbx=points[i+1][0]-points[i][0]
        cby=points[i+1][1]-points[i][1]
        denom=2*(aby*cbx-cby*abx)
        if abs(denom)>=1/64 and denom*abx>0:
            delta=(aby*cbx*cbx-cby*abx*abx)/denom
            focus=points[i][0]+max(min(abx,cbx),min(max(abx,cbx),delta))
    return focus


class Controller:
    def __init__(self,position=688,limits=(352,928),emit=None,config_path=None):
        path=Path(config_path) if config_path else Path(__file__).with_name('imx708-af.json')
        self.cfg=json.loads(path.read_text())['rpi.af']
        self.speed=self.cfg['speeds']['normal']
        self.mapping=self.cfg['map']
        self.low=max(0.,self.dioptres(limits[0]))
        self.high=min(15.,self.dioptres(limits[1]))
        if not 0<=limits[0]<limits[1]<=1023 or self.high-self.low<1:
            raise ValueError('AF limits must include at least1 dioptre of tuning map')
        self.position=self.good_position=position
        self.emit=emit or (lambda record:None)
        self.mode,self.state,self.reason='off','MANUAL',''
        self.locked_score=None
        self.locked_thumbnail=None
        self.old_contrast=self.old_mean=0.
        self.change_count=0
        self.sequence=-1
        self.move_sequence=-1
        self.move_time=-math.inf
        self.await_move=False
        self.skip=0
        self.samples=[]
        self.records=[]
        self.next_retry=-math.inf

    def dac(self,focus):
        x0,y0,x1,y1=self.mapping
        return int(round(y0+(focus-x0)*(y1-y0)/(x1-x0)))

    def dioptres(self,position):
        x0,y0,x1,y1=self.mapping
        return x0+(position-y0)*(x1-x0)/(y1-y0)

    @property
    def searching(self):return self.state in ('COARSE1','COARSE2','FINE','SETTLE')

    def status(self):
        return dict(mode=self.mode,state=self.state,position=self.position,score=self.locked_score,
                    reason=self.reason,engine='libcamera-cdaf',focus_dioptres=self.dioptres(self.position))

    def event(self,kind,**values):self.emit(dict(event=kind,**self.status(),**values))

    def set_mode(self,mode,now):
        if mode not in ('off','once','continuous'):raise ValueError('invalid AF mode')
        self.mode=mode;self.state='MANUAL' if mode=='off' else 'WAITING'
        self.reason='';self.skip=self.cfg['skip_frames'];self.await_move=False
        self.next_retry=now;self.change_count=0;self.event('mode')

    def manual(self,position):
        if not 0<=position<=1023:raise ValueError('focus must be0..1023')
        self.set_mode('off',0);self.good_position=position;self.locked_score=None
        self.await_move=True
        return position

    def error(self,reason):
        self.mode,self.state,self.reason='off','ERROR',str(reason)
        self.await_move=False;self.event('error')

    def moved(self,position,now,sequence):
        self.position=position;self.move_time=now;self.move_sequence=sequence
        self.skip=self.speed['step_frames'];self.await_move=False;self.samples=[]

    def _request(self,focus):
        self.target=max(self.low,min(self.high,focus))
        self.await_move=True
        return self.dac(self.target)

    def _start(self,now):
        focus=max(self.low,min(self.high,self.dioptres(self.position)))
        coarse=self.speed['step_coarse']
        if self.mode!='continuous' or focus<=self.low+2*coarse:
            focus=self.low;self.step=coarse;self.state='COARSE2'
        elif focus>=self.high-2*coarse:
            focus=self.high;self.step=-coarse;self.state='COARSE2'
        else:
            self.step=-coarse;self.state='COARSE1'
        self.records=[];self.peak_contrast=0.;self.min_contrast=math.inf
        self.started=now;self.deadline=now+15;self.reason=''
        self.event('search',direction=self.step)
        return self._request(focus)

    def _fail(self,now,reason):
        self.state='WAITING' if self.mode=='continuous' else 'FAILED'
        self.reason=reason;self.next_retry=now+3;self.event('unconfirmed')
        self.await_move=True
        return self.good_position

    def tick(self,now):
        if self.searching and now>self.deadline:return self._fail(now,'scan timeout')
        return None

    def feed(self,m,now,sequence,captured_at):
        if sequence<=self.sequence:return None
        self.sequence=sequence
        action=self.tick(now)
        if action is not None:return action
        if self.mode=='off' or self.await_move or self.state in ('FAILED','ERROR'):return None
        if captured_at<self.move_time or sequence<=self.move_sequence:return None
        if self.skip:
            self.skip-=1;return None
        if not m['valid']:
            self.reason=m['reason'];return None
        # Recover unnormalised gradient energy, like the Pi ISP contrast FoM.
        contrast=m.get('contrast',m['score']*max(m['mean']-16,4)**2)
        if self.state=='WAITING':
            if now<self.next_retry:return None
            return self._start(now)
        if self.state=='LOCKED':
            ratio=self.speed['retrigger_ratio']
            changed=(contrast+1<ratio*self.old_contrast or self.old_contrast+1<ratio*contrast or
                     m['mean']+1<ratio*self.old_mean or self.old_mean+1<ratio*m['mean'] or
                     distance(m['thumbnail'],self.locked_thumbnail)>.15)
            if changed and self.change_count==0:
                self.change_count=1;self.previous=(contrast,m['mean'],m['thumbnail'])
            elif self.change_count:
                old,mean,thumb=self.previous
                unstable=(contrast+1<ratio*old or old+1<ratio*contrast or
                          abs(m['mean']-mean)>max(4,.2*mean) or distance(thumb,m['thumbnail'])>.08)
                self.change_count=1 if unstable else self.change_count+1
                self.previous=(contrast,m['mean'],m['thumbnail'])
                if self.change_count>=self.speed['retrigger_delay']:return self._start(now)
            return None
        if self.state=='SETTLE':
            if distance(m['thumbnail'],self.candidate_thumbnail)>.15:
                return self._fail(now,'scene changed during focus confirmation')
            ratio=self.speed['contrast_ratio']
            if contrast<ratio*self.peak_contrast or self.min_contrast>ratio*self.peak_contrast:
                return self._fail(now,'flat curve or peak confirmation failed')
            self.good_position=self.position;self.locked_score=contrast
            self.old_contrast=contrast;self.old_mean=m['mean'];self.locked_thumbnail=m['thumbnail']
            self.change_count=0;self.state='LOCKED';self.reason=''
            self.event('locked',elapsed=now-self.started)
            if self.mode=='once':self.mode='off'
            return None
        return self._scan(contrast,m,now)

    def _scan(self,contrast,m,now):
        focus=self.dioptres(self.position)
        self.records.append((focus,contrast,m['thumbnail']))
        index=max(range(len(self.records)),key=lambda i:self.records[i][1])
        peak=self.records[index][1]
        self.peak_contrast=max(self.peak_contrast,contrast)
        self.min_contrast=min(self.min_contrast,contrast)
        self.event('sample',measured_score=contrast,mean=m['mean'],valid=True)
        at_limit=(self.step>=0 and focus>=self.high-.02) or (self.step<=0 and focus<=self.low+.02)
        end=(at_limit or self.state=='FINE' and len(self.records)>=3 or
             contrast<self.speed['contrast_ratio']*peak)
        if not end:return self._request(focus+self.step)
        estimate=find_peak(self.records,index)
        if self.state=='COARSE1' and self.records[0][1]>=self.speed['contrast_ratio']*peak:
            self.step=-self.step;self.state='COARSE2';self.records=[]
            self.event('reverse',direction=self.step)
            return self._request(focus)
        if self.state=='FINE' or self.speed['step_fine']<=0:
            self.candidate_thumbnail=self.records[index][2]
            self.state='SETTLE';self.event('settle',estimated_dioptres=estimate)
            return self._request(estimate)
        fine=self.speed['step_fine']
        start=estimate+fine if self.step>=0 else estimate-fine
        self.step=-fine if self.step>=0 else fine
        self.state='FINE';self.records=[]
        self.event('fine',estimated_dioptres=estimate)
        return self._request(start)
