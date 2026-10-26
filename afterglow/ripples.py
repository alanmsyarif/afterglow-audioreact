"""Bounded, tempo-driven ripple pulses. Shared by Free and Pro; no playback hooks."""
import numpy as np

SLOTS = 6
STYLES = {'CLASSIC': 0, 'FLOW': 1, 'CHASE': 2, 'BOUNCE': 3, 'ECHO': 4, 'COLLISION': 5}


def phase_curve(frames, beats, fps, start):
    """Integrate tempo instead of multiplying time by the current beat length."""
    frames, beats = np.asarray(frames), np.maximum(beats, .05)
    phase = np.zeros(len(frames))
    phase[1:] = np.cumsum(np.diff(frames) / fps * .5 * (1 / beats[1:] + 1 / beats[:-1]))
    return phase - np.interp(start, frames, phase)


def detect(times, amp, phase_times, phases):
    """Attack launches, with active-audio beat fills for sustained bass; silence stays empty."""
    times, amp = np.asarray(times), np.maximum(np.asarray(amp), 0)
    if not len(amp) or amp.max() <= 1e-4:
        return []
    peak = float(amp.max())
    previous = np.interp(times - .035, times, amp, left=0)
    attack = np.maximum(amp - previous, 0)
    limit = max(peak * .065, float(attack.max()) * .12, 1e-4)
    candidates = np.flatnonzero((attack >= np.r_[0, attack[:-1]]) &
                               (attack > np.r_[attack[1:], 0]) & (attack >= limit))
    chosen = []
    for index in candidates:
        t = float(times[index])
        if chosen and t - chosen[-1][0] < .10:
            if attack[index] > chosen[-1][2]:
                chosen[-1] = (t, float(amp[index]), float(attack[index]))
        else:
            chosen.append((t, float(amp[index]), float(attack[index])))
    launch = [(t, a) for t, a, _ in chosen]
    # A sustained note has no fresh attacks; fill only beat crossings with actual energy.
    beat_times = np.interp(np.arange(0, max(float(phases[-1]), 0) + 1), phases, phase_times)
    for t in beat_times:
        if t > times[-1]:
            continue
        a = float(np.interp(t, times, amp))
        p = float(np.interp(t, phase_times, phases))
        nearby = any(abs(float(np.interp(at, phase_times, phases)) - p) < .3 for at, _ in launch)
        if a > max(peak * .15, 1e-4) and not nearby:
            launch.append((float(t), a))
    return [(t, float(np.interp(t, phase_times, phases)), a) for t, a in sorted(launch)]


def lifetime(style, loop=False):
    return 2.7 if loop else {1: 1.8, 2: 2.3, 3: 2.8, 4: 2.2, 5: 1.3}.get(style, 1.8)


def envelope(distance, width, tail=True):
    """Finite smooth head, exponential trail. No residual light beyond its support."""
    edge = np.clip((distance + width * .16) / np.maximum(width * .16, 1e-5), 0, 1)
    head = edge * edge * (3 - 2 * edge)
    fall = np.exp(-np.maximum(distance, 0) / np.maximum(width * (.45 if tail else .18), 1e-5))
    end = np.clip((width * (2.5 if tail else 1) - distance) / np.maximum(width * .4, 1e-5), 0, 1)
    return head * fall * end * end * (3 - 2 * end)


def shape(age, pos, width, style, loop=False):
    """Analytic profiles mirrored exactly by materials._pulse_shader()."""
    age, pos = np.asarray(age), np.asarray(pos)
    def pulse(head, offset=0, tail=True):
        d = head - offset - pos
        if loop:
            # Signed wrap keeps a smooth leading edge across the cyclic seam.
            d = np.mod(d + width * .16, 1) - width * .16
        return envelope(d, width, tail)
    if style == 3:
        head = 1 - np.abs(1 - np.clip(age, 0, 2))
        returning = age > 1
        result = np.where(returning, envelope(pos - head, width), pulse(head))
    elif style == 5:
        head = age * .5
        result = np.maximum(envelope(head - pos, width), envelope(pos - (1 - head), width)) * (age <= 1)
        impact = np.clip(1 - np.abs(pos - .5) / np.maximum(width * 1.5, .03), 0, 1) ** 2
        result = np.maximum(result, impact * np.clip(1 - (age - 1) / .25, 0, 1) * (age >= 1))
    else:
        result = pulse(age, tail=style != 2)
        if style == 2:
            for k in range(1, 4):
                result = np.maximum(result, .82 ** k * pulse(age, k * .18, False))
        elif style == 4:
            for k in range(1, 3):
                result = np.maximum(result, .46 ** k * pulse(age, k * .15))
    life = lifetime(style, loop)
    fade = np.clip((life - age) / .35, 0, 1)
    return result * fade * (age >= 0) * (age <= life)


def slot_arrays(events, source_times, phases):
    """Stable six-slot allocation; a slot changes only at a launch, never each frame."""
    births = np.full((SLOTS, len(phases)), -1e6)
    amplitudes = np.zeros_like(births)
    for index, (at, phase, amp) in enumerate(events):
        slot = index % SLOTS
        begin = int(np.searchsorted(source_times, at))
        births[slot, begin:] = phase
        amplitudes[slot, begin:] = amp
    return births, amplitudes


def object_levels(events, source_times, phases, pos, style, width, dynamics, gain, threshold,
                  speed, sweeps, intensity=1, loop=False):
    births, amplitudes = slot_arrays(events, source_times, phases)
    result = np.zeros(len(phases))
    for birth, amp in zip(births, amplitudes):
        level = np.maximum(amp - threshold, 0) * gain
        age = (phases - birth) * speed / max(sweeps, .05)
        # Dynamic width uses calibrated beat strength, independent of artistic brightness.
        widths = width * (1 + dynamics * (.75 * np.minimum(level, 1) - .45))
        # Vectorized across the track: work scales with frames and fixed slots,
        # not with the number of distinct attacks in a long song.
        result = np.maximum(result, level * shape(age, pos, widths, style, loop))
    result *= intensity
    return np.where(result > 0, result / max(gain, 1e-12) + threshold, 0)
