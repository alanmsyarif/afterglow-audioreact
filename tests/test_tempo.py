import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import helpers  # noqa: E402
from afterglow import tempo  # noqa: E402

RATE = 22050


def clicks(bpm_at, seconds):
    """Kick-like 80 Hz bursts at a tempo that may vary with time."""
    x = np.zeros(int(seconds * RATE), np.float32)
    burst = (np.sin(np.arange(400) * 2 * np.pi * 80 / RATE) * np.exp(-np.arange(400) / 80)).astype(np.float32)
    t = 0.0
    while t < seconds - 0.05:
        i = int(t * RATE)
        n = min(400, len(x) - i)
        x[i:i + n] += burst[:n]
        t += 60 / bpm_at(t)
    return x


def test_steady_tempo():
    curve = tempo.tempo_curve(clicks(lambda t: 100, 30), RATE)
    assert curve and all(abs(b - 100) <= 4 for _, b in curve), curve


def test_accelerating_tempo_tracked():
    curve = tempo.tempo_curve(clicks(lambda t: 120 + 40 * t / 31, 31), RATE)
    for t, b in curve:
        assert abs(b - (120 + 40 * t / 31)) <= 5, (t, b)


def test_spike_removed_by_median():
    curve = [(float(i), 120.0) for i in range(9)]
    curve[4] = (4.0, 200.0)
    assert [b for _, b in tempo._median(curve)] == [120.0] * 9


def test_short_audio_falls_back():
    assert tempo.tempo_curve(np.zeros(int(0.1 * RATE), np.float32), RATE) == [(0.05, 120.0)]


def test_silence_reads_default():
    curve = tempo.tempo_curve(np.zeros(10 * RATE, np.float32), RATE)
    assert curve and all(b == tempo.DEFAULT_BPM for _, b in curve), curve


def test_stereo_input_accepted():
    mono = clicks(lambda t: 100, 12)
    curve = tempo.tempo_curve(np.stack([mono, mono], axis=1), RATE)
    assert all(abs(b - 100) <= 4 for _, b in curve), curve


import tempfile  # noqa: E402
import wave  # noqa: E402

import bpy  # noqa: E402
from afterglow import nodes  # noqa: E402


def write_wav(name, x):
    path = os.path.join(tempfile.gettempdir(), name)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(x, -1, 1) * 30000).astype("<i2").tobytes())
    return path


def beat_length_at(seconds, start_frame=1):
    bpy.context.scene.frame_set(int(round(start_frame + seconds * 24)))
    return nodes.get_group().nodes[nodes.BEAT_NODE].outputs[0].default_value


def test_bake_keys_beat_length_and_rebake_clears():
    scene = bpy.context.scene
    scene.afterglow.sound = bpy.data.sounds.load(write_wav("ag_100.wav", clicks(lambda t: 100, 20)))
    tempo.bake(scene)
    assert abs(beat_length_at(10) - 0.6) < 0.03, beat_length_at(10)
    scene.afterglow.sound = bpy.data.sounds.load(write_wav("ag_150.wav", clicks(lambda t: 150, 12)))
    tempo.bake(scene)
    # 16 s is past the 12 s song: old 100 BPM keys must be gone (constant extrapolation of 0.4)
    assert abs(beat_length_at(16) - 0.4) < 0.03, beat_length_at(16)


def test_start_frame_rekeys_curve():
    scene = bpy.context.scene
    scene.afterglow.sound = bpy.data.sounds.load(write_wav("ag_100.wav", clicks(lambda t: 100, 20)))
    scene.afterglow.ripple = True  # update callback bakes
    first = beat_length_at(0.0)
    scene.afterglow.start_frame = 100  # update callback re-keys at the new offset
    group = nodes.get_group()
    scene.frame_set(1)
    assert group.animation_data is not None
    assert abs(beat_length_at(4, start_frame=100) - 0.6) < 0.03
    assert abs(first - 0.6) < 0.03


def test_bake_missing_file_raises():
    scene = bpy.context.scene
    snd = bpy.data.sounds.load(write_wav("ag_100_short.wav", clicks(lambda t: 100, 5)))
    snd.filepath = "//definitely_missing.wav"
    scene.afterglow.sound = snd
    try:
        tempo.bake(scene)
    except ValueError as e:
        assert "on disk" in str(e), str(e)
    else:
        raise AssertionError("no ValueError")


def test_rebake_keeps_hand_edits_and_follows_start_frame():
    scene = bpy.context.scene
    scene.afterglow.sound = bpy.data.sounds.load(write_wav("ag_100.wav", clicks(lambda t: 100, 20)))
    tempo.bake(scene)
    sock = nodes.get_group().nodes[nodes.BEAT_NODE].outputs[0]
    sock.default_value = 0.25
    sock.keyframe_insert("default_value", frame=200)  # the user fixes a section by hand
    tempo.bake(scene)  # e.g. Apply again: same song, edits stay
    scene.frame_set(200)
    assert abs(sock.default_value - 0.25) < 1e-4, sock.default_value
    scene.afterglow.start_frame = 11
    tempo.bake(scene)  # keys shift with the audio, edits included
    scene.frame_set(210)
    assert abs(sock.default_value - 0.25) < 1e-4, sock.default_value
    nodes.get_group().animation_data_clear()  # deleting the keys asks for a fresh detection
    tempo.bake(scene)
    scene.frame_set(210)
    assert abs(sock.default_value - 0.6) < 0.03, sock.default_value

def test_migration_keeps_tempo_curve():
    scene = bpy.context.scene
    scene.afterglow.sound = bpy.data.sounds.load(write_wav("ag_100.wav", clicks(lambda t: 100, 20)))
    tempo.bake(scene)
    old = nodes.get_group()
    keys = len(tempo._fcurve(old).keyframe_points)
    path = old[tempo.TAG_PATH]
    old[nodes.TAG] = nodes.VERSION - 1  # the file was saved by the previous release
    new = nodes.get_group()
    fc = tempo._fcurve(new)
    assert fc and len(fc.keyframe_points) == keys, fc
    assert new.get(tempo.TAG_PATH) == path


helpers.run(globals())
