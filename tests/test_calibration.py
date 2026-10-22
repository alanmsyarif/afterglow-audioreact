"""Calibration remains useful for sparse, quiet, delayed and steady audio.

These tests inspect evaluated geometry and native Light power, rather than
repeating the calibration percentile formula as the expected answer.
"""
import math
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import numpy as np
import helpers
from afterglow import bake, core, nodes, workflow


RATE = 22050
POWER = 800.0
_FILES = tempfile.TemporaryDirectory(prefix='noctune-calibration-')


def sound(kind='pulses', duration=8.0, scale=1.0):
    t = np.arange(round(duration * RATE), dtype=np.float64) / RATE
    if kind == 'pulses':
        envelope = np.where(t % 1 < 0.4, 0.2 + 0.5 * (np.floor(t) % 2), 0.0)
    elif kind == 'sparse':
        envelope = np.zeros_like(t)
        for start in (1.0, 6.0):
            chosen = (t >= start) & (t < start + 0.12)
            envelope[chosen] = 0.7 * np.hanning(np.count_nonzero(chosen))
    elif kind == 'transient':
        envelope = np.zeros_like(t)
        # A short hit centered between two 24 fps samples must still be measured.
        chosen = (t >= 2.012) & (t < 2.030)
        envelope[chosen] = 0.7 * np.hanning(np.count_nonzero(chosen))
    elif kind == 'steady':
        envelope = np.full_like(t, 0.5)
    elif kind == 'silence':
        envelope = np.zeros_like(t)
    elif kind == 'noise':
        samples = np.random.default_rng(812).normal(0.0, 1e-4, len(t))
        envelope = None
    else:
        raise ValueError(kind)
    if envelope is not None:
        samples = np.sin(2 * np.pi * 100 * t) * envelope
    pcm = np.rint(np.clip(samples * scale, -1, 1) * 32767).astype('<i2')
    filename = f'{kind}-{duration}-{scale}.wav'
    path = Path(_FILES.name) / filename
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(pcm.tobytes())
    return bpy.data.sounds.load(str(path), check_existing=True)


def stage(kind='pulses', duration=8.0, scale=1.0, *, mesh=True, apply_reaction=True):
    scene = bpy.context.scene
    collection = bpy.data.collections.new('Calibration sources')
    scene.collection.children.link(collection)
    data = bpy.data.lights.new('User light', 'POINT')
    data.energy = POWER
    lamp = bpy.data.objects.new('A user light', data)
    collection.objects.link(lamp)
    objects = [lamp]
    if mesh:
        obj = helpers.plane('B user mesh', collection, helpers.emission_material('User emission'))
        obj.location.x = 5.0
        objects.append(obj)
    scene.afterglow.collection = collection
    scene.afterglow.sound = sound(kind, duration, scale)
    scene.afterglow.easy_glow = False
    if apply_reaction:
        core.apply(scene)
        for obj in objects:
            obj.afterglow.locked = True
            for name, value in {'Low': 70.0, 'High': 180.0, 'Decay': 0.0, 'Delay': 0.0,
                                'Glow': 0.0, 'Intensity': 1.0, 'Use Baked': True}.items():
                nodes.set_input(core.reaction(obj), name, value)
    return scene, objects


def calibration(objects, ripple=False):
    names = ('Ripple Gain', 'Ripple Threshold') if ripple else ('Gain', 'Threshold')
    return {obj.name: tuple(float(nodes.get_input(core.reaction(obj), name)) for name in names)
            for obj in objects}


def response(objects, frames):
    measured = {obj.name: [] for obj in objects}
    for frame in frames:
        scene = bpy.context.scene
        # Neighbor evaluation also avoids Blender's initial sound-node glitch.
        base = math.floor(frame)
        scene.frame_set(base + 1)
        scene.frame_set(base, subframe=float(frame - base))
        graph = bpy.context.evaluated_depsgraph_get()
        for obj in objects:
            value = (obj.evaluated_get(graph).data.energy / POWER if obj.type == 'LIGHT'
                     else helpers.attr(obj))
            measured[obj.name].append(float(value))
    return measured


def calibrated_response(scene, objects, frames):
    assert set(core.calibrate(scene)) == set(objects)
    assert bake.bake_all(scene) == len(objects)
    return response(objects, frames)


def test_regular_pulses_normalize_mesh_and_native_light_consistently():
    scene, objects = stage()
    measured = calibrated_response(scene, objects, range(1, 193))
    lamp, mesh = (np.array(measured[obj.name]) for obj in objects)
    assert np.allclose(lamp, mesh, atol=1e-5, rtol=1e-5)
    assert 0.75 < np.quantile(lamp, 0.9) < 1.3
    assert 0.8 < lamp.max() < 3.0
    assert np.count_nonzero(lamp < 1e-4) > len(lamp) / 3


def test_sparse_hits_stay_bounded_on_actual_light_and_mesh_outputs():
    scene, objects = stage('sparse')
    measured = calibrated_response(scene, objects, range(1, 193))
    for obj in objects:
        values = np.array(measured[obj.name])
        assert 0.3 < values.max() <= 4.0, (obj.name, calibration(objects), values.max())
        assert np.quantile(values, 0.9) < 1e-3
    assert np.allclose(measured[objects[0].name], measured[objects[1].name], rtol=1e-5, atol=1e-5)


def test_default_build_reaction_does_not_overexpose_sparse_light_audio():
    scene, (lamp,) = stage('sparse', mesh=False, apply_reaction=False)
    result = workflow.create_reaction(scene, look_identifier='NEON_CLUB', style_scope='OBJECT')
    assert result['applied'] == result['calibrated'] == result['baked'] == 1
    values = response([lamp], range(1, 193))[lamp.name]
    assert 0.3 < max(values) <= 4.0, (calibration([lamp]), max(values))


def test_quiet_sparse_hits_keep_a_useful_response_without_extreme_gain():
    scene, (lamp,) = stage('sparse', mesh=False)
    normal = calibrated_response(scene, [lamp], range(1, 193))[lamp.name]
    scene.afterglow.sound = sound('sparse', scale=0.001)
    quiet = calibrated_response(scene, [lamp], range(1, 193))[lamp.name]
    assert 0.3 < max(quiet) <= 4.0
    assert math.isclose(max(quiet), max(normal), rel_tol=0.2), (max(normal), max(quiet))


def test_delay_beyond_the_track_does_not_change_calibration_or_source_response():
    scene, objects = stage()
    frames = list(range(1, 193))
    original = calibrated_response(scene, objects, frames)
    settings = calibration(objects)
    for obj in objects:
        nodes.set_input(core.reaction(obj), 'Delay', 10.0)
    delayed = calibrated_response(scene, objects, [frame + 240 for frame in frames])
    assert calibration(objects) == settings
    for obj in objects:
        assert np.allclose(original[obj.name], delayed[obj.name], atol=2e-4, rtol=2e-4)
    assert all(value < 1e-5 for values in response(objects, [1, 100, 200]).values() for value in values)


def test_ripple_calibration_uses_the_whole_kick_band_independent_of_position():
    scene, objects = stage()
    scene.afterglow.ripple = True
    assert set(core.calibrate(scene)) == set(objects)
    original = calibration(objects, ripple=True)
    assert len(set(original.values())) == 1
    scene.afterglow.beats_per_sweep = 16.0
    for index, obj in enumerate(objects):
        nodes.set_input(core.reaction(obj), 'Ripple Position', float(index))
        nodes.set_input(core.reaction(obj), 'Delay', index * 10.0)
    assert set(core.calibrate(scene)) == set(objects)
    assert calibration(objects, ripple=True) == original


def test_steady_audio_gets_stable_useful_brightness_instead_of_numerical_noise():
    scene, objects = stage('steady')
    measured = calibrated_response(scene, objects, range(13, 180))
    for obj in objects:
        values = np.array(measured[obj.name])
        assert 0.5 < values.min() <= values.max() < 2.0, (calibration(objects), values.min(), values.max())
        assert values.max() - values.min() < 0.05


def test_silence_and_quiet_noise_do_not_produce_runaway_responses():
    scene, objects = stage('silence')
    measured = calibrated_response(scene, objects, (0, 1, 13, 61, 192, 240))
    assert all(abs(value) < 1e-6 for values in measured.values() for value in values)
    assert all(math.isfinite(gain) and gain <= 100 for gain, _ in calibration(objects).values())
    scene.afterglow.sound = sound('noise')
    measured = calibrated_response(scene, objects, range(1, 193))
    assert all(math.isfinite(value) and 0 <= value <= 4 for values in measured.values() for value in values)


def sample_calls(scene, **kwargs):
    calls = []
    original = bake.calibration_levels

    def measured(scene, reactions, frames, **options):
        calls.append((np.array(frames, dtype=float), options.copy()))
        return original(scene, reactions, frames, **options)

    with patch.object(bake, 'calibration_levels', side_effect=measured):
        core.calibrate(scene, **kwargs)
    return calls


def test_default_sampling_covers_each_source_frame_once_at_integer_and_fractional_fps():
    scene, _ = stage(duration=3.0)
    scene.afterglow.start_frame = 123
    scene.frame_set(47)
    for fps_base in (1.0, 1.001):
        scene.render.fps, scene.render.fps_base = 24, fps_base
        calls = sample_calls(scene)
        assert len(calls) == 1
        frames, options = calls[0]
        count = len(np.arange(0, 3.0 * 24 / fps_base))
        assert len(frames) == len(np.unique(frames)) == count, frames
        assert np.allclose(np.diff(frames), 1.0), frames
        assert frames[0] == scene.afterglow.start_frame
        assert frames[-1] < scene.afterglow.start_frame + 3.0 * 24 / fps_base
        assert scene.frame_current == 47 and scene.afterglow.start_frame == 123


def test_short_audio_and_explicit_samples_have_finite_unique_source_positions():
    scene, _ = stage(duration=0.02)
    calls = sample_calls(scene)
    frames, options = calls[0]
    assert len(frames) == 1 and frames[0] == scene.afterglow.start_frame
    scene.afterglow.sound = sound(duration=3.0)
    frames, options = sample_calls(scene, samples=7)[0]
    assert len(frames) == len(np.unique(frames)) == 7
    assert np.isfinite(frames).all() and np.all(np.diff(frames) > 0)
    assert scene.afterglow.start_frame <= frames[0] < frames[-1] < scene.afterglow.start_frame + 72


def test_between_frame_transient_is_measured_without_runaway_gain():
    scene, objects = stage('transient')
    measured = calibrated_response(scene, objects, np.arange(48.0, 51.0, 0.125))
    for obj in objects:
        peak = max(measured[obj.name])
        assert 0.1 < peak <= 4.0, (obj.name, calibration(objects), peak)


helpers.run(globals())
