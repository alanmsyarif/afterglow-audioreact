import os
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import audition, core


def track():
    scene = bpy.context.scene
    scene.afterglow.sound = helpers.sound()  # Three seconds of real audio.
    return scene


def state(scene):
    return (scene.use_preview_range, scene.frame_preview_start, scene.frame_preview_end,
            scene.frame_start, scene.frame_end, scene.frame_current)


def test_excerpt_is_clipped_to_track_without_changing_render_or_audio():
    scene = track()
    scene.afterglow.start_frame = 25
    scene.render.fps = 24
    scene.frame_start, scene.frame_end = 1, 400
    scene.frame_set(170)
    sound = scene.afterglow.sound
    strips = [(strip.name, strip.frame_start, strip.frame_final_end)
              for strip in scene.sequence_editor.strips]
    result = audition.set_preview(scene, 2.0, 8.0)
    assert result == (73, 96), result
    assert scene.use_preview_range
    assert (scene.frame_start, scene.frame_end, scene.frame_current) == (1, 400, 170)
    assert scene.afterglow.sound == sound and scene.afterglow.start_frame == 25
    assert [(strip.name, strip.frame_start, strip.frame_final_end)
            for strip in scene.sequence_editor.strips] == strips


def test_fractional_fps_uses_inclusive_intersecting_frames():
    scene = track()
    scene.afterglow.start_frame = 50
    scene.render.fps, scene.render.fps_base = 24, 1.001
    assert audition.set_preview(scene, 0.5, 1.0) == (61, 85)
    assert (scene.render.fps, round(scene.render.fps_base, 3)) == (24, 1.001)


def test_repeated_excerpts_restore_the_first_existing_preview_exactly():
    scene = track()
    scene.use_preview_range = True
    scene.frame_preview_start, scene.frame_preview_end = -20, 150
    before = state(scene)
    audition.set_preview(scene, 0.0, 1.0)
    audition.set_preview(scene, 1.0, 0.5)
    assert audition.reset_preview(scene)
    assert state(scene) == before
    assert audition.BACKUP not in scene


def test_initial_unused_preview_restores_zero_endpoints_and_disabled_state():
    scene = track()
    before = state(scene)
    audition.set_preview(scene)
    assert audition.restore_preview(scene)
    assert state(scene) == before
    assert before[:3] == (False, 0, 0)
    assert not audition.reset_preview(scene)
    assert state(scene) == before


def test_backup_survives_save_and_reopen():
    scene = track()
    scene.use_preview_range = True
    scene.frame_preview_start, scene.frame_preview_end = 10, 240
    before = state(scene)
    audition.set_preview(scene, 0.25, 0.5)
    with tempfile.TemporaryDirectory() as folder:
        path = str(Path(folder) / "audition.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        scene = bpy.context.scene
        assert audition.reset_preview(scene)
        assert state(scene) == before


def test_invalid_excerpt_and_audio_errors_leave_ranges_and_backup_unchanged():
    scene = track()
    before = state(scene)
    for start, duration in ((3.0, 1.0), (-1, 1), (float("nan"), 1), (0, 0),
                            (0, float("inf")), (True, 1), (0, "8")):
        try:
            audition.set_preview(scene, start, duration)
        except ValueError:
            pass
        else:
            raise AssertionError((start, duration))
        assert state(scene) == before and audition.BACKUP not in scene
    original = core.load_mono
    def unreadable(sound, what):
        raise ValueError("unreadable test audio")
    core.load_mono = unreadable
    try:
        try:
            audition.set_preview(scene)
        except ValueError as error:
            assert "unreadable" in str(error)
        else:
            raise AssertionError("Unreadable audio must fail")
    finally:
        core.load_mono = original
    assert state(scene) == before and audition.BACKUP not in scene


def test_nonfinite_decoded_samples_fail_before_changing_preview():
    scene = track()
    original = core.load_mono
    core.load_mono = lambda *args: (np.array([0.0, np.nan]), 44100)
    before = state(scene)
    try:
        try:
            audition.set_preview(scene)
        except ValueError as error:
            assert "finite audio" in str(error)
        else:
            raise AssertionError("Nonfinite audio must fail")
    finally:
        core.load_mono = original
    assert state(scene) == before and audition.BACKUP not in scene


def test_song_range_fits_audio_plus_tail_without_changing_preview_or_playhead():
    scene = track()
    scene.render.fps = 24
    scene.afterglow.start_frame = 25
    audition.set_preview(scene, 0.25, 1.0)
    before = state(scene)
    assert audition.set_song_range(scene) == (25, 144)
    assert (scene.frame_start, scene.frame_end) == (25, 144)
    assert state(scene)[:3] == before[:3] and scene.frame_current == before[-1]
    assert audition.set_song_range(scene, include_tail=False) == (25, 96)


def test_supported_frame_limits_and_missing_sound_reject_without_changes():
    scene = bpy.context.scene
    before = state(scene)
    try:
        audition.set_preview(scene)
    except ValueError as error:
        assert "Load a sound" in str(error)
    else:
        raise AssertionError("A sound is required")
    assert state(scene) == before
    scene = track()
    scene.afterglow.start_frame = 1_048_570
    before = state(scene)
    for operation in (audition.set_preview, audition.set_song_range):
        try:
            operation(scene)
        except ValueError as error:
            assert "supported frames" in str(error)
        else:
            raise AssertionError("Range would exceed Blender limits")
        assert state(scene) == before and audition.BACKUP not in scene


def test_corrupt_backup_does_not_overwrite_ranges():
    scene = track()
    scene[audition.BACKUP] = {"version": 99}
    before = state(scene)
    for operation in (audition.set_preview, audition.reset_preview):
        try:
            operation(scene)
        except ValueError as error:
            assert "Saved Afterglow" in str(error)
        else:
            raise AssertionError("Corrupt restore state must fail")
        assert state(scene) == before


helpers.run(globals())
