"""Audition a track excerpt without changing the render range or its animation."""
import math

import numpy as np

from . import bake, core

BACKUP = "afterglow_preview_range"
VERSION = 1


def _editable(scene):
    if not scene.is_editable:
        raise ValueError("Make the scene local before changing its playback range")


def _number(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        value = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{name} must be a finite number") from None
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        relation = "greater than zero" if positive else "zero or greater"
        raise ValueError(f"{name} must be finite and {relation}")
    return value


def _track(scene):
    sound = scene.afterglow.sound
    if sound is None:
        raise ValueError("Load a sound file before choosing a playback range")
    mono, rate = core.load_mono(sound, "Playback range")
    if not len(mono) or not math.isfinite(rate) or rate <= 0 or not np.isfinite(mono).all():
        raise ValueError("Choose a sound file with readable, finite audio samples")
    duration = len(mono) / rate
    fps = scene.render.fps / scene.render.fps_base
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(fps) or fps <= 0:
        raise ValueError("The track duration and scene frame rate must be positive and finite")
    return duration, fps, scene.afterglow.start_frame


def _bounds(scene, start, end, preview=False):
    if preview:
        prop = scene.bl_rna.properties["frame_current"]
        lo, hi = prop.hard_min, prop.hard_max
    else:
        lo = scene.bl_rna.properties["frame_start"].hard_min
        hi = scene.bl_rna.properties["frame_end"].hard_max
    if start > end or start < lo or end > hi:
        raise ValueError(f"This range exceeds Blender's supported frames ({lo} to {hi}); adjust Start Frame")
    return start, end


def _backup(scene):
    data = scene.get(BACKUP)
    if data is None:
        return None
    keys = {"version", "enabled", "start", "end"}
    if not hasattr(data, "keys") or set(data.keys()) != keys or data["version"] != VERSION:
        raise ValueError("Saved Afterglow preview range is invalid; restore the playback range manually")
    start, end = data["start"], data["end"]
    if type(start) is not int or type(end) is not int or type(data["enabled"]) is not bool:
        raise ValueError("Saved Afterglow preview range is invalid; restore the playback range manually")
    _bounds(scene, start, end, preview=True)
    return {"enabled": data["enabled"], "start": start, "end": end}


def _set_preview(scene, start, end, enabled):
    # Blender initializes preview endpoints when enabling a previously unused range.
    # Enable first, then set both endpoints, and restore the requested enabled state.
    scene.use_preview_range = True
    scene.frame_preview_start = start
    scene.frame_preview_end = end
    scene.use_preview_range = enabled


def set_preview(scene, start_seconds=0.0, duration_seconds=8.0):
    """Loop a track excerpt and return its inclusive frame endpoints.

    Seconds are relative to the track's Start Frame. The excerpt covers the frames
    intersecting those seconds, is clipped to the audio, and never changes render
    endpoints, the playhead, audio placement, frame rate or animation. The first prior
    preview state is retained across repeated edits and saved projects for restoration.
    """
    _editable(scene)
    start_seconds = _number(start_seconds, "Preview start")
    duration_seconds = _number(duration_seconds, "Preview duration", positive=True)
    duration, fps, origin = _track(scene)
    if start_seconds >= duration:
        raise ValueError("Preview start is past the end of the sound; choose an earlier time")
    end_seconds = min(duration, start_seconds + duration_seconds)
    start = origin + math.floor(start_seconds * fps)
    end = origin + math.ceil(end_seconds * fps) - 1
    _bounds(scene, start, end, preview=True)
    saved = _backup(scene)
    if saved is None:
        scene[BACKUP] = {"version": VERSION, "enabled": scene.use_preview_range,
                         "start": scene.frame_preview_start, "end": scene.frame_preview_end}
    _set_preview(scene, start, end, True)
    return start, end


def reset_preview(scene):
    """Restore the first saved preview state, or disable preview if none was saved.

    Returns True when a saved state was restored. A missing backup only disables the
    preview range; it never resets the render endpoints or the stored preview values.
    """
    _editable(scene)
    saved = _backup(scene)
    if saved is None:
        scene.use_preview_range = False
        return False
    _set_preview(scene, saved["start"], saved["end"], saved["enabled"])
    del scene[BACKUP]
    return True


restore_preview = reset_preview


def set_song_range(scene, include_tail=True):
    """Explicitly fit render endpoints to the sound and optional effect decay tail.

    The resulting endpoints are inclusive. This leaves any preview range, audio,
    playhead, frame rate and animation unchanged. Negative or unreachable render
    frames are rejected rather than silently clamped by Blender.
    """
    _editable(scene)
    if type(include_tail) is not bool:
        raise ValueError("Include Tail must be true or false")
    duration, fps, start = _track(scene)
    end = start + math.ceil((duration + (bake.TAIL_S if include_tail else 0.0)) * fps) - 1
    _bounds(scene, start, end)
    scene.frame_start = start
    scene.frame_end = end
    return start, end
