"""Fast Playback: precompute each object's held peak per frame with numpy and key it onto the
modifier's Baked Peak input, so playback never evaluates the slow Sample Sound node."""
import aud
import bpy
import numpy as np
from bpy_extras import anim_utils

from . import core, nodes, tempo

FFT, HOP, CHUNK = 4096, 256, 256  # FFT and Hann window match the node defaults
TAIL_S = 2.0  # keep keying past the song end so decay/delay tails finish
SIG = "afterglow_bake"


def band_series(mono, rate, bands):
    """Node-equivalent band amplitude every HOP samples: Hann window centred on each hop,
    sum(|rfft|) over low <= f <= high, times 2 / FFT. Like the node, the window is kept inside
    the file at its edges instead of zero-padding. Returns (times, array[bands, hops])."""
    samples = np.pad(mono.astype(np.float32), (0, max(0, FFT - len(mono))))
    hops = 1 + len(mono) // HOP
    freqs = np.fft.rfftfreq(FFT, 1 / rate)
    edges = [(np.searchsorted(freqs, lo, "left"), np.searchsorted(freqs, hi, "right")) for lo, hi in bands]
    window = np.hanning(FFT).astype(np.float32)
    out = np.zeros((len(bands), hops), np.float32)
    for c in range(0, hops, CHUNK):
        k = np.arange(c, min(c + CHUNK, hops))
        starts = np.clip(HOP * k - FFT // 2, 0, len(samples) - FFT)
        mag = np.abs(np.fft.rfft(samples[starts[:, None] + np.arange(FFT)[None, :]] * window, axis=1))
        cum = np.concatenate([np.zeros((len(k), 1), np.float32), np.cumsum(mag, axis=1, dtype=np.float32)], axis=1)
        for b, (i0, i1) in enumerate(edges):
            out[b, k] = (cum[:, i1] - cum[:, i0]) * 2 / FFT
    return np.arange(hops) * HOP / rate, out


def held_peak(times, amp, t, decay):
    """The node's Repeat-Zone peak hold, evaluated at audio times t (array)."""
    step, d = decay / 4, max(decay, 1e-4)
    peak = np.zeros_like(t)
    for i in range(nodes.SAMPLES):
        ts = t - i * step
        a = np.interp(ts, times, amp, right=0.0)
        a[ts < 0] = 0.0
        peak = np.maximum(peak, a * np.exp(-i * step / d))
    return peak


def signature(mod, scene):
    s = scene.afterglow
    vals = [nodes.get_input(mod, n) for n in
            ("Low", "High", "Decay", "Delay", "Ripple", "Ripple Position", "Beats per Sweep", "Start Frame",
             "Through", "Tap Min", "Tap Max")]
    vals = [round(v, 5) if isinstance(v, float) else v for v in vals]
    fps = round(scene.render.fps / scene.render.fps_base, 4)  # keys are placed per frame
    return repr(vals + [s.sound.filepath if s.sound else "", fps])


def outdated(scene):
    out = []
    for ob in core._managed(scene):
        mod = core.find_modifier(ob)
        if mod and ob.get(SIG) != signature(mod, scene):
            out.append(ob)
    return sorted(out, key=lambda o: o.name)


def _path(mod, name="Baked Peak"):
    return nodes._socket(mod, name).path_from_id("value")


def _bake_fcurve(ob, mod, name="Baked Peak"):
    ad = ob.animation_data
    if not (ad and ad.action):
        return None, None
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    path = _path(mod, name)
    return bag, bag and next((fc for fc in bag.fcurves if fc.data_path == path), None)


def unkey(ob, mod):
    """Delete only Afterglow's bake curves; the object's other animation stays."""
    for name in nodes.BAKE_INPUTS:
        bag, fc = _bake_fcurve(ob, mod, name)
        if fc:
            bag.fcurves.remove(fc)
    ob.pop(SIG, None)


def _own_action(ob):
    """Objects sharing one action (Alt+D, Link Animation Data) would share one Baked Peak
    curve; give this object its own copy, which keeps the user's curves."""
    ad = ob.animation_data
    if not (ad and ad.action and ad.action.users > 1):
        return
    ident = ad.action_slot.identifier if ad.action_slot else None
    ad.action = ad.action.copy()
    slot = next((sl for sl in ad.action.slots if sl.identifier == ident), None)
    if slot:
        ad.action_slot = slot


def _write(ob, mod, frames, values, name="Baked Peak"):
    sock = nodes._socket(mod, name)
    sock.value = float(values[0])
    sock.keyframe_insert("value", frame=float(frames[0]))
    _, fc = _bake_fcurve(ob, mod, name)
    fc.keyframe_points.add(len(frames) - 1)
    co = np.empty(2 * len(frames), np.float32)
    co[0::2], co[1::2] = frames, values
    fc.keyframe_points.foreach_set("co", co)
    fc.update()


def _beat_lengths(frames):
    group = nodes.get_group()
    fc = tempo._fcurve(group)
    if not fc:
        return np.full(len(frames), group.nodes[nodes.BEAT_NODE].outputs[0].default_value)
    return np.array([fc.evaluate(float(f)) for f in frames])


def _load(scene):
    s = scene.afterglow
    if s.sound is None:
        raise ValueError("Pick a sound file first")
    if not core.sound_ok(s.sound):
        raise ValueError("Sound file not found")
    try:
        clip = aud.Sound(bpy.path.abspath(s.sound.filepath, library=s.sound.library))
        data, rate = np.asarray(clip.data()), clip.specs[0]
    except Exception:
        raise ValueError("Fast Playback needs the sound file on disk") from None
    return (data.mean(axis=1) if data.ndim == 2 else data), rate


def _band_of(m):
    return nodes.KICK if nodes.get_input(m, "Ripple") else (nodes.get_input(m, "Low"), nodes.get_input(m, "High"))


def _times(m, frames, fps, beat, pos=None):
    """Audio time each frame samples for this modifier: delay plus the ripple lag."""
    def g(name):
        return nodes.get_input(m, name)
    pos = g("Ripple Position") if pos is None else pos
    lag = float(g("Ripple")) * pos * g("Beats per Sweep") * beat
    return (frames - g("Start Frame")) / fps - g("Delay") - lag


def raw_levels(scene, mods, frames):
    """Raw band amplitude (no peak hold) each modifier samples at the given frames: what
    Calibrate measures live with Decay 0, from the same numpy source as the bake."""
    mono, rate = _load(scene)
    frames = np.asarray(frames, dtype=np.float64)
    fps = scene.render.fps / scene.render.fps_base
    bands = sorted({_band_of(m) for m in mods})
    times, series = band_series(mono, rate, bands)
    beat = _beat_lengths(frames)
    out = []
    for m in mods:
        t = _times(m, frames, fps, beat)
        a = np.interp(t, times, series[bands.index(_band_of(m))], right=0.0)
        a[t < 0] = 0.0
        out.append(a)
    return out


def bake_all(scene):
    """Bake every object carrying an Afterglow modifier. Returns how many were baked."""
    s = scene.afterglow
    objs = sorted((o for o in core._managed(scene) if core.find_modifier(o)), key=lambda o: o.name)
    mono, rate = _load(scene)
    if not objs:
        return 0
    fps = scene.render.fps / scene.render.fps_base
    mods = [core.find_modifier(o) for o in objs]
    song_frames = np.arange(s.start_frame, s.start_frame + len(mono) / rate * fps + 1)
    beat_max = float(_beat_lengths(song_frames).max())
    # Key until every object's delayed, held tail has died, or extrapolation keeps it lit.
    tail = TAIL_S + max(nodes.get_input(m, "Delay") + 2 * nodes.get_input(m, "Decay")
                        + float(nodes.get_input(m, "Ripple")) * 1.0  # every position (taps too) is <= 1
                        * nodes.get_input(m, "Beats per Sweep") * beat_max for m in mods)
    end = s.start_frame + int(np.ceil((len(mono) / rate + tail) * fps))
    frames = np.arange(s.start_frame - 1, end + 1, dtype=np.float64)
    bands = sorted({_band_of(m) for m in mods})
    times, series = band_series(mono, rate, bands)
    beat = _beat_lengths(frames)
    for ob, m in zip(objs, mods):
        _own_action(ob)
        unkey(ob, m)
        amp = series[bands.index(_band_of(m))]
        decay = nodes.get_input(m, "Decay")
        _write(ob, m, frames, held_peak(times, amp, _times(m, frames, fps, beat), decay))
        if nodes.get_input(m, "Through"):
            tmin, tmax = nodes.get_input(m, "Tap Min"), nodes.get_input(m, "Tap Max")
            for k in range(nodes.TAPS):
                pos = tmin + (tmax - tmin) * k / (nodes.TAPS - 1)
                _write(ob, m, frames, held_peak(times, amp, _times(m, frames, fps, beat, pos), decay),
                       name=f"Baked Tap {k}")
        ob[SIG] = signature(m, scene)
    return len(objs)
