"""Local tempo detection, baked into the node group as a Beat Length curve."""
import aud
import bpy
import numpy as np
from bpy_extras import anim_utils

from . import nodes

FFT, HOP = 2048, 512
WINDOW_S, STEP_S = 8.0, 1.0
LO_BPM, HI_BPM, DEFAULT_BPM = 60.0, 180.0, 120.0
CHUNK = 1024  # STFT frames per batch, bounds memory on long songs


def _onsets(mono):
    """Positive log-spectral flux per hop, minus its slow trend."""
    frames = 1 + (len(mono) - FFT) // HOP
    window = np.hanning(FFT).astype(np.float32)
    parts, prev = [], None
    for c in range(0, frames, CHUNK):
        idx = np.arange(FFT)[None, :] + HOP * np.arange(c, min(c + CHUNK, frames))[:, None]
        spec = np.log1p(100 * np.abs(np.fft.rfft(mono[idx] * window, axis=1)))
        if prev is not None:
            spec = np.vstack([prev, spec])
        parts.append(np.maximum(np.diff(spec, axis=0), 0).sum(axis=1))
        prev = spec[-1:]
    flux = np.concatenate(parts)
    return np.maximum(flux - np.convolve(flux, np.ones(16) / 16, mode="same"), 0)


def _median(curve, k=5):
    bpms = [b for _, b in curve]
    h = k // 2
    return [(t, float(np.median(bpms[max(0, i - h):i + h + 1]))) for i, (t, _) in enumerate(curve)]


def tempo_curve(samples, rate):
    """[(seconds, bpm)] every STEP_S from onset-strength autocorrelation over WINDOW_S."""
    mono = (samples.mean(axis=1) if samples.ndim == 2 else samples).astype(np.float32)
    fallback = [(len(mono) / rate / 2, DEFAULT_BPM)]
    if len(mono) < FFT * 4:
        return fallback
    flux = _onsets(mono)
    fps = rate / HOP
    width = min(int(WINDOW_S * fps), len(flux))
    lags = np.arange(int(fps * 60 / HI_BPM), int(fps * 60 / LO_BPM) + 1)
    lags = lags[lags < width - 1]
    if not len(lags):
        return fallback
    bpm = 60 * fps / lags
    prior = np.exp(-0.5 * (np.log2(bpm / DEFAULT_BPM) / 0.9) ** 2)  # settles octave errors
    out = []
    for start in range(0, len(flux) - width + 1, max(1, int(STEP_S * fps))):
        seg = flux[start:start + width] - flux[start:start + width].mean()
        ac = np.array([seg[:-lag] @ seg[lag:] for lag in lags]) * prior
        out.append(((start + width / 2) / fps, float(bpm[np.argmax(ac)]) if ac.max() > 0 else DEFAULT_BPM))
    return _median(out)


TAG_PATH, TAG_START = nodes.TEMPO_TAGS
_cache = {}  # absolute path -> curve; re-keying after a Start Frame change skips re-analysis


def bake(scene):
    """Analyze the scene's sound and key the group's Beat Length (seconds per beat)."""
    s = scene.afterglow
    if s.sound is None:
        raise ValueError("Pick a sound file first")
    path = bpy.path.abspath(s.sound.filepath, library=s.sound.library)
    curve = _cache.get(path)
    if curve is None:
        try:
            clip = aud.Sound(path)
            samples, rate = np.asarray(clip.data()), clip.specs[0]
        except Exception:
            raise ValueError("Tempo analysis needs the sound file on disk") from None
        curve = _cache[path] = tempo_curve(samples, rate)
    group = nodes.get_group()
    fc = _fcurve(group)
    if fc and len(fc.keyframe_points) and group.get(TAG_PATH) == path:
        # Same song already keyed: keep the user's hand edits, only follow Start Frame.
        delta = s.start_frame - group.get(TAG_START, s.start_frame)
        if delta:
            for kp in fc.keyframe_points:
                for attr in ("co", "handle_left", "handle_right"):
                    getattr(kp, attr).x += delta
            fc.update()
    else:
        socket = group.nodes[nodes.BEAT_NODE].outputs[0]
        fps = scene.render.fps / scene.render.fps_base
        group.animation_data_clear()  # the group carries no other animation
        for t, bpm in curve:
            socket.default_value = 60.0 / bpm
            socket.keyframe_insert("default_value", frame=s.start_frame + t * fps)
    group[TAG_PATH], group[TAG_START] = path, s.start_frame
    return curve


def _fcurve(group):
    ad = group.animation_data
    if not (ad and ad.action):
        return None
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    return bag and next((fc for fc in bag.fcurves if nodes.BEAT_NODE in fc.data_path), None)
