# SPDX-License-Identifier: GPL-2.0-only
"""Contrast AF policy for I420 luma; no GStreamer or hardware dependency.

Controller is owned by one worker. feed/tick return a requested DAC or None;
the owner must execute it and call moved only after successful hardware I/O.
Times are monotonic seconds, including the mapped capture time of each frame.
"""
import math
import statistics


def measure_luma(data, width, height, stride=None, offset=0,
                 roi=(.25, .25, .5, .5)):
    stride = width if stride is None else stride
    if min(width, height) < 16 or stride < width or offset < 0:
        raise ValueError('invalid luma dimensions/stride/offset')
    if len(data) < offset+(height-1)*stride+width:
        raise ValueError('truncated luma plane')
    x, y, w, h = roi
    if (not all(math.isfinite(v) for v in roi) or min(x, y) < 0 or
            min(w, h) <= 0 or x+w > 1 or y+h > 1):
        raise ValueError('ROI must be normalized x,y,width,height within0..1')
    x0, y0, x1, y1 = int(x*width), int(y*height), int((x+w)*width), int((y+h)*height)
    if min(x1-x0, y1-y0) < 12:
        raise ValueError('ROI must cover at least12x12 pixels')
    total = total_square = count = saturated = energy = 0
    # Sparse non-power-of-two sampling avoids aligning every sample with a
    # repetitive screen pattern; integer moments avoid statistics' slow exact
    # rational arithmetic on the Nano's Cortex-A57.
    for yy in range(y0, y1-2, 3):
        row = offset+yy*stride
        for xx in range(x0, x1-2, 3):
            p = row+xx
            value = data[p]
            total += value
            total_square += value*value
            count += 1
            saturated += value >= 250
            energy += (value-data[p+2])**2+(value-data[p+2*stride])**2
    mean = total/count
    variance = max(0., total_square/count-mean*mean)
    signal = max(mean-16, 4.)
    clipped = saturated/count
    # Small normalized image distinguishes motion/scene change from brightness.
    thumbnail = tuple((data[offset+int(y0+(j+.5)*(y1-y0)/12)*stride+
                            int(x0+(i+.5)*(x1-x0)/16)]-mean)/signal
                      for j in range(12) for i in range(16))
    reason = ('dark' if mean < 20 else 'saturated' if mean > 240 or clipped > .05
              else 'low texture' if variance < 9 else '')
    return dict(score=energy/(2*count*signal**2), mean=mean,
                variance=variance, clipped=clipped, thumbnail=thumbnail,
                valid=not reason, reason=reason)


def distance(a, b):
    return sum(abs(x-y) for x, y in zip(a, b))/len(a)


def choose_peak(points, prominence=.1):
    points = sorted(dict(points).items())
    if len(points) < 3:
        return None
    best = max(points, key=lambda p: p[1])
    left = [s for p, s in points if p < best[0]]
    right = [s for p, s in points if p > best[0]]
    if not left or not right or best[1] <= 0:
        return None
    if best[1] < (1+prominence)*max(min(left), min(right)):
        return None
    return best


def positions(low, high, step):
    result = list(range(low, high+1, step))
    if result[-1] != high:
        result.append(high)
    return result


class Controller:
    def __init__(self, position=688, limits=(352, 928), emit=None):
        if not 0 <= limits[0] < limits[1] <= 1023 or limits[1]-limits[0] < 64:
            raise ValueError('AF range must span at least64 DAC within0..1023')
        self.position = self.good_position = position
        self.limits = limits
        self.emit = emit or (lambda record: None)
        self.mode, self.state, self.reason = 'off', 'MANUAL', ''
        self.samples, self.records, self.points = [], [], []
        self.locked_score = None
        self.previous = self.locked_thumbnail = None
        self.stable_since = self.trigger_since = None
        self.next_retry = self.last_monitor = self.last_search = -math.inf
        self.last_sequence = -1
        self.move_time, self.move_sequence = -math.inf, -1
        self.deadline = math.inf
        self.await_move = False
        self.last_measurement = None

    @property
    def searching(self):
        return self.state in ('COARSE', 'FINE', 'VERIFY')

    def status(self):
        return dict(mode=self.mode, state=self.state, position=self.position,
                    score=self.locked_score, reason=self.reason)

    def event(self, kind, **values):
        self.emit(dict(event=kind, **self.status(), **values))

    def set_mode(self, mode, now):
        if mode not in ('off', 'once', 'continuous'):
            raise ValueError('mode must be off, once or continuous')
        self.mode = mode
        self.state = 'MANUAL' if mode == 'off' else 'WAITING'
        self.reason = ''
        self.await_move = False
        self.samples = []
        self.stable_since = self.trigger_since = None
        self.previous = None
        self.next_retry = now
        self.event('mode')

    def manual(self, position):
        if not 0 <= position <= 1023:
            raise ValueError('focus must be0..1023')
        self.set_mode('off', 0.)
        self.good_position = position
        self.locked_score = None
        self.await_move = True
        return position

    def error(self, reason):
        self.mode, self.state, self.reason = 'off', 'ERROR', str(reason)
        self.await_move = False
        self.event('error')

    def moved(self, position, now, sequence):
        self.position = position
        self.move_time, self.move_sequence = now, sequence
        self.settle_frames = 2
        self.samples = []
        self.await_move = False

    def _request(self, position):
        self.await_move = True
        return position

    def _begin(self, now, local=False):
        self.local = local
        self.records = []
        self.reference_mean = None
        self.last_search = now
        self.total_deadline = now+(40 if local else 25)
        self.deadline = min(self.total_deadline, now+12) if local else self.total_deadline
        return self._coarse(local)

    def _coarse(self, local):
        low, high = self.limits
        if local:
            low, high = max(low, self.position-64), min(high, self.position+64)
        self.local = local
        self.points = positions(low, high, 16 if local else 32)
        self.state, self.reason = 'COARSE', ''
        self.event('search', local=local, points=self.points)
        return self._request(self.points.pop(0))

    def _fail(self, now, reason):
        self.state = 'WAITING' if self.mode == 'continuous' else 'FAILED'
        self.reason = reason
        self.next_retry = now+5
        self.stable_since = self.trigger_since = None
        self.previous = None
        self.event('unconfirmed')
        return self._request(self.good_position)

    def tick(self, now):
        if self.searching and now > self.deadline:
            return self._fail(now, 'search timeout')
        return None

    def feed(self, measurement, now, sequence, captured_at):
        if sequence <= self.last_sequence:
            return None
        self.last_sequence = sequence
        action = self.tick(now)
        if action is not None:
            return action
        if self.await_move or self.mode == 'off' or self.state in ('FAILED', 'ERROR'):
            return None
        self.last_measurement = measurement
        if self.searching:
            if sequence <= self.move_sequence or captured_at < self.move_time+.15:
                return None
            if self.settle_frames:
                self.settle_frames -= 1
                return None
            self.samples.append(measurement)
            if len(self.samples) < 3:
                return None
            return self._scored(now)
        thumb = measurement['thumbnail']
        moving = self.previous is not None and distance(thumb, self.previous) > .08
        self.previous = thumb
        if not measurement['valid'] or moving:
            self.reason = measurement['reason'] if not measurement['valid'] else 'scene moving'
            self.stable_since = self.trigger_since = None
            return None
        if now-self.last_monitor < .2:
            return None
        self.last_monitor = now
        if self.stable_since is None:
            self.stable_since = now
        if now-self.stable_since < .5 or now < self.next_retry:
            return None
        if self.state == 'WAITING':
            return self._begin(now)
        if self.state == 'LOCKED':
            changed = distance(thumb, self.locked_thumbnail) > .12
            dropped = measurement['score'] < .7*self.locked_score
            if changed or dropped:
                if self.trigger_since is None:
                    self.trigger_since = now
                if now-self.trigger_since >= 1 and now-self.last_search >= 3:
                    return self._begin(now, local=True)
            else:
                self.trigger_since = None
        return None

    def _scored(self, now):
        means = [s['mean']-16 for s in self.samples]
        scores = [s['score'] for s in self.samples]
        score, mean = statistics.median(scores), statistics.median(means)
        valid = all(s['valid'] for s in self.samples)
        reason = next((s['reason'] for s in self.samples if not s['valid']), '')
        if max(means)-min(means) > .1*max(mean, 4):
            valid, reason = False, 'unstable illumination'
        if max(scores)-min(scores) > .35*max(score, 1e-8):
            valid, reason = False, 'unstable sharpness'
        if any(distance(s['thumbnail'], self.samples[0]['thumbnail']) > .08 for s in self.samples[1:]):
            valid, reason = False, 'scene moving'
        if self.reference_mean is None and valid:
            self.reference_mean = mean
        if self.reference_mean is not None and abs(mean-self.reference_mean) > .2*max(self.reference_mean, 4):
            return self._fail(now, 'illumination drift across positions')
        self.event('sample', measured_score=score, mean=mean+16, valid=valid,
                   invalid_reason=reason, sample_count=3)
        if self.state == 'VERIFY':
            if not valid or score < .9*self.candidate[1]:
                return self._fail(now, reason or 'peak confirmation failed')
            self.good_position, self.locked_score = self.position, score
            self.locked_thumbnail = self.samples[-1]['thumbnail']
            self.previous = self.locked_thumbnail
            self.state, self.reason = 'LOCKED', ''
            self.stable_since, self.trigger_since = now, None
            self.event('locked', elapsed=now-self.last_search)
            if self.mode == 'once':
                self.mode = 'off'
            return None
        if valid:
            self.records.append((self.position, score))
        if self.points:
            return self._request(self.points.pop(0))
        peak = choose_peak(self.records)
        if peak is None:
            if self.local:
                self.records = []
                self.reference_mean = None
                self.deadline = self.total_deadline
                return self._coarse(False)
            return self._fail(now, 'no bracketed prominent peak')
        if self.state == 'COARSE':
            span = 16 if self.local else 32
            self.points = positions(max(self.limits[0], peak[0]-span),
                                    min(self.limits[1], peak[0]+span), 4)
            self.state = 'FINE'
            return self._request(self.points.pop(0))
        self.candidate = peak
        self.state = 'VERIFY'
        return self._request(peak[0])
