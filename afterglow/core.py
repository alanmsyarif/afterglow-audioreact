"""Collection-level operations: apply, respread, calibrate, sync, remove."""
import os
import tempfile

import aud
import numpy as np
import bpy
from mathutils import Vector

from . import effect_schema, light_reaction, materials, nodes

GEOMETRY_TYPES = {"MESH", "CURVE", "FONT"}
SUPPORTED = GEOMETRY_TYPES | {"LIGHT"}
MOD_NAME = "Noctune"
STRIP_NAME = "Noctune Audio"
AUDIO_TAG = "afterglow_audio"
LEGACY_STRIP_NAME = "Afterglow Audio"
LOW_HZ, HIGH_HZ = 20.0, 16000.0


def write_meter(ob, mod):
    """Push the object's Level Meter settings into its modifier."""
    if ob.type == "LIGHT":
        return
    m = ob.afterglow
    nodes.set_input(mod, "Meter", m.meter)
    nodes.set_input(mod, "Meter Axis", "XYZ".index(m.meter_axis))
    nodes.set_input(mod, "Meter Invert", m.meter_invert)


def sound_ok(sound):
    """Packed, or its file exists. Blender 5.2 crashes evaluating Sample Sound on a missing file."""
    return bool(sound.packed_file) or os.path.isfile(bpy.path.abspath(sound.filepath, library=sound.library))


_audio = {}  # audio_key -> (mono samples, rate); one entry, the last song decoded


def audio_key(sound):
    """What a sound's audio content is, for caches: its packed copy, or its file's path,
    size and modification time, so replacing the file on disk is noticed."""
    if sound.packed_file:
        return ("packed", sound.name_full, sound.packed_file.size)
    path = bpy.path.abspath(sound.filepath, library=sound.library)
    try:
        st = os.stat(path)
    except OSError:
        return (path, None, None)
    return (path, st.st_size, st.st_mtime_ns)


def load_mono(sound, what="Audio analysis"):
    """(mono float32 samples, rate) of a Sound, decoded from its packed copy when it has one,
    else from its file. The last song decoded is cached, so Build, Bake, Calibrate and tempo
    analysis on one song decode it once."""
    key = audio_key(sound)
    if key in _audio:
        return _audio[key]
    tmp = None
    try:
        if sound.packed_file:
            fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(sound.filepath)[1] or ".wav")
            with os.fdopen(fd, "wb") as f:
                f.write(sound.packed_file.data)
        clip = aud.Sound(tmp or key[0])
        data, rate = np.asarray(clip.data()), clip.specs[0]
        del clip
    except Exception:
        raise ValueError(f"{what} needs the sound file on disk or packed into the .blend") from None
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass
    mono = (data.mean(axis=1) if data.ndim == 2 else data).astype(np.float32)
    _audio.clear()
    _audio[key] = (mono, rate)
    return mono, rate


def is_target(ob):
    return (ob.type in SUPPORTED and not ob.get(effect_schema.TAG)
            and not ob.get(effect_schema.EMITTER_TAG))


def targets(collection):
    return sorted((o for o in collection.all_objects if is_target(o)), key=lambda o: o.name)


def find_modifier(ob):
    return next((m for m in ob.modifiers
                 if m.type == "NODES" and m.node_group and m.node_group.get(nodes.TAG)), None)


def reaction(ob):
    """A geometry modifier or native-light reaction with the same input interface."""
    return light_reaction.reaction(ob) if ob.type == "LIGHT" else find_modifier(ob)


def _materials(ob):
    return {slot.material for slot in ob.material_slots if slot.material}


def _managed(scene):
    """Current targets plus scene objects with reactions or prepared materials.

    Switched collections and objects moved out still participate in cleanup.
    """
    from . import prepare
    objs = {o for o in scene.objects if find_modifier(o) or light_reaction.managed(o) or prepare.TAG in o}
    if scene.afterglow.collection:
        objs |= set(targets(scene.afterglow.collection))
    return objs


def band(k, n):
    """Band k of n, log-spaced between LOW_HZ and HIGH_HZ."""
    ratio = HIGH_HZ / LOW_HZ
    return LOW_HZ * ratio ** (k / n), LOW_HZ * ratio ** ((k + 1) / n)


def apply(scene):
    """Refresh geometry reactions and native-light drivers in the chosen collection.

    Returns (applied, skipped, outside, linked). 'outside' lists objects outside the collection
    that share a patched material; they have no Noctune attributes, so they render dark.
    'linked' lists library-linked emissive materials, which can't be patched persistently.
    """
    s = scene.afterglow
    if s.collection is None:
        raise ValueError("Pick a collection first")
    if s.sound is None:
        raise ValueError("Pick a sound file first")
    if s.ripple and s.ripple_from == "EMPTY" and s.ripple_origin is None:
        raise ValueError("Pick an Origin object for Ripple From: Empty")
    if not sound_ok(s.sound):
        raise ValueError(f"Sound file not found: {bpy.path.abspath(s.sound.filepath, library=s.sound.library)}")
    objects = targets(s.collection)
    light_reaction.preflight(objects)
    group = nodes.get_group() if any(ob.type in GEOMETRY_TYPES for ob in objects) else None
    applied, skipped, patched, linked = [], [], set(), []
    for ob in objects:
        mats = set()
        for m in _materials(ob):
            if not materials.has_emission(m):
                continue
            if m.library:
                if m not in linked:
                    linked.append(m)
            else:
                mats.add(m)
        if ob.type == "LIGHT":
            mod = light_reaction.apply(ob)
        else:
            if not mats:
                skipped.append(ob)
                continue
            mod = find_modifier(ob) or ob.modifiers.new(MOD_NAME, "NODES")
            mod.node_group = group
        nodes.set_input(mod, "Sound", s.sound)
        nodes.set_input(mod, "Start Frame", s.start_frame)
        nodes.set_input(mod, "Ripple", s.ripple)
        nodes.set_input(mod, "Beats per Sweep", s.beats_per_sweep)
        nodes.set_input(mod, "Use Baked", s.fast_playback)
        nodes.set_input(mod, "Through", s.through)
        write_meter(ob, mod)
        for m in mats:
            materials.patch_material(m, wave=s.through)
        patched |= mats
        applied.append(ob)
    respread(applied)
    bpy.context.view_layer.update()  # matrix_world is stale for objects moved since the last eval
    place(applied, s.ripple_from, s.ripple_origin, s.ripple_reverse)
    if s.through:
        wave(scene, applied)
    setup_playback(scene)
    outside = [o for o in scene.objects if o not in applied and _materials(o) & patched]
    return applied, skipped, outside, linked


def respread(objs):
    free = [o for o in objs if not o.afterglow.locked]
    for k, ob in enumerate(free):
        low, high = band(k, len(free))
        mod = reaction(ob)
        nodes.set_input(mod, "Low", low)
        nodes.set_input(mod, "High", high)


def _seconds(sound):
    mono, rate = load_mono(sound, "Calibrate")
    return len(mono) / rate


def _pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(p * len(values)))]


# Calibration leaves artistic Gain/Intensity edits unrestricted. These limits
# apply only to automatically selected normalization values.
CALIBRATION_FLOOR = 1e-4  # preserve the existing minimum useful band-amplitude span
CALIBRATION_HEADROOM = 4.0


def calibrate(scene, samples=None):
    """Normalize full source bands, independently of playback Delay and Ripple lag.

    Every source frame is sampled once by default; an explicit sample count uses
    fractional frame positions. Quiet/flat/sparse bands have stable fallbacks,
    and full-envelope peaks limit automatically calibrated response to headroom.
    """
    from . import bake, workflow  # both import core
    s = scene.afterglow
    objs = sorted((o for o in _managed(scene) if reaction(o)), key=lambda o: o.name)
    if not objs:
        raise ValueError("Apply first")
    if s.sound is None:
        raise ValueError("Pick a sound file first")
    workflow.calibration_preflight(scene)
    mono, rate = load_mono(s.sound, "Calibrate")
    if not len(mono) or rate <= 0 or not np.isfinite(mono).all():
        raise ValueError("Choose a sound file with readable, finite audio samples")
    duration = len(mono) / rate
    fps = scene.render.fps / scene.render.fps_base
    if samples is None:
        sample_frames = s.start_frame + np.arange(max(1, int(np.ceil(duration * fps))))
    else:
        if isinstance(samples, bool) or not isinstance(samples, (int, np.integer)) or samples < 1:
            raise ValueError("Choose a positive integer calibration sample count")
        sample_frames = s.start_frame + np.arange(samples) * duration * fps / samples
    mods = [reaction(o) for o in objs]
    gain_key, threshold_key = ("Ripple Gain", "Ripple Threshold") if s.ripple else ("Gain", "Threshold")
    levels, source = bake.calibration_levels(scene, mods, sample_frames)
    _set_calibration(mods, levels, gain_key, threshold_key, shared=s.ripple, source_levels=source)
    return objs


def _calibration_pair(values, envelope):
    values, envelope = np.asarray(values), np.asarray(envelope)
    if (not len(values) or not len(envelope) or not np.isfinite(values).all()
            or not np.isfinite(envelope).all()):
        raise ValueError("Calibration needs finite audio levels")
    peak = float(np.max(envelope))
    if peak <= CALIBRATION_FLOOR:
        # Do not amplify silence or negligible spectral residue into a reaction.
        return max(peak, 0.0), 1.0
    median, high = _pct(values, 0.5), _pct(values, 0.9)
    if high - median < 0.05 * peak:
        # Sparse attacks may occupy less than 10% of the song (or fall between
        # source frames). Use the full envelope's active region in that case.
        active = envelope[envelope > max(CALIBRATION_FLOOR, 0.01 * peak)]
        high = _pct(active, 0.9) if len(active) else peak
    if high - median < 0.05 * high:
        # A sustained band should stay stable instead of subtracting almost its
        # entire level and amplifying tiny numerical differences.
        median = min(median, 0.5 * high)
    span = max(float(high - median), (peak - float(median)) / CALIBRATION_HEADROOM,
               CALIBRATION_FLOOR)
    return float(median), 1.0 / span


def _set_calibration(mods, levels, gain_key, threshold_key, shared=False, source_levels=None):
    source_levels = levels if source_levels is None else source_levels
    if shared:  # Ripple uses one source kick envelope and a common pair at every origin.
        pair = _calibration_pair(np.concatenate(levels), np.concatenate(source_levels))
        pairs = [pair] * len(mods)
    else:
        pairs = [_calibration_pair(values, source) for values, source in zip(levels, source_levels)]
    # Validate every measurement before writing any object's controls.
    for mod, (threshold, gain) in zip(mods, pairs):
        nodes.set_input(mod, threshold_key, threshold)
        nodes.set_input(mod, gain_key, gain)


AXES = {"X": 0, "Y": 1, "Z": 2}


def _metric(pts, mode, origin):
    """(radial, unit axis, centre) for Ripple From; shared by place() and wave()."""
    if mode == "CENTER" or (mode == "EMPTY" and origin):
        centre = origin.matrix_world.translation.copy() if mode == "EMPTY" else sum(pts, Vector()) / len(pts)
        return True, Vector((1.0, 0.0, 0.0)), centre
    i = AXES.get(mode)
    if i is None:
        i = max(range(3), key=lambda a: max(p[a] for p in pts) - min(p[a] for p in pts))
    axis = Vector((0.0, 0.0, 0.0))
    axis[i] = 1.0
    return False, axis, Vector((0.0, 0.0, 0.0))


def _world_points(ob, depsgraph):
    """Every evaluated vertex in world space (the object's origin if it has none)."""
    if ob.type == "LIGHT":
        return np.array(ob.matrix_world.translation, dtype=np.float64)[None, :]
    ev = ob.evaluated_get(depsgraph)
    me = ev.to_mesh()
    try:
        co = np.empty(len(me.vertices) * 3, np.float32)
        me.vertices.foreach_get("co", co)
    finally:
        ev.to_mesh_clear()
    mw = np.array(ob.matrix_world, dtype=np.float64)
    return co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3] if len(co) else mw[None, :3, 3]


def wave(scene, objs):
    """Through Objects: one wave coordinate over every vertex of every object (Lo, Span) and
    each object's own range along it (Tap Min/Max), written to the modifiers. Unlike place(),
    Longest Axis and Collection Center come from the vertices, so one long object, or objects
    sharing an origin, still get swept along their real length."""
    if not objs:
        return
    s = scene.afterglow
    dg = bpy.context.evaluated_depsgraph_get()
    pts = [_world_points(o, dg) for o in objs]
    allp = np.concatenate(pts)
    axis, centre = np.zeros(3), np.zeros(3)
    if s.ripple_from == "EMPTY" and s.ripple_origin:
        radial, centre = True, np.array(s.ripple_origin.matrix_world.translation)
    elif s.ripple_from == "CENTER":
        radial, centre = True, (allp.min(axis=0) + allp.max(axis=0)) / 2
    else:
        radial = False
        i = AXES.get(s.ripple_from)
        axis[int(np.argmax(allp.max(axis=0) - allp.min(axis=0)) if i is None else i)] = 1.0
    dists = [np.linalg.norm(p - centre, axis=1) if radial else p @ axis for p in pts]
    ranges = [(float(d.min()), float(d.max())) for d in dists]
    lo = min(r[0] for r in ranges)
    span = max(r[1] for r in ranges) - lo
    span = span if span > 1e-6 else 1.0
    for ob, (a, b) in zip(objs, ranges):
        follows_curve = ob.type == "CURVE"
        # Each spline traverses its own full length; collection axes still place
        # ordinary meshes and native lights. Reverse is folded into its factor.
        tmin, tmax = (0.0, 1.0) if follows_curve else ((a - lo) / span, (b - lo) / span)
        if s.ripple_reverse:
            tmin, tmax = 1.0 - tmax, 1.0 - tmin
        mod = reaction(ob)
        for name, value in (("Wave Curve", follows_curve), ("Wave Radial", radial), ("Wave Axis", tuple(axis.tolist())),
                            ("Wave Origin", tuple(centre.tolist())), ("Wave Lo", lo), ("Wave Span", span),
                            ("Wave Reverse", s.ripple_reverse), ("Tap Min", tmin), ("Tap Max", tmax)):
            nodes.set_input(mod, name, value)


def place(objs, mode="LONGEST", origin=None, reverse=False):
    """Ripple Position 0..1 per object, by real distance so the wave keeps an even speed:
    along an axis (LONGEST picks the axis the origins spread most on, or X/Y/Z), or radially
    from an origin object (EMPTY) or the collection's centre (CENTER). The nearest object
    fires first; reverse flips the direction. EMPTY without an origin falls back to LONGEST."""
    if not objs:
        return
    pts = [o.matrix_world.translation.copy() for o in objs]
    radial, axis, centre = _metric(pts, mode, origin)
    dist = [(p - centre).length for p in pts] if radial else [p.dot(axis) for p in pts]
    lo = min(dist)
    span = max(dist) - lo
    for ob, d in zip(objs, dist):
        pos = (d - lo) / span if span > 1e-6 else 0.0
        nodes.set_input(reaction(ob), "Ripple Position", 1.0 - pos if reverse and span > 1e-6 else pos)


def replace(scene):
    """Re-place every Noctune object with the scene's Ripple From settings."""
    s = scene.afterglow
    bpy.context.view_layer.update()  # matrix_world is stale for objects moved since the last eval
    objs = sorted((o for o in _managed(scene) if reaction(o)), key=lambda o: o.name)
    place(objs, s.ripple_from, s.ripple_origin, s.ripple_reverse)
    if s.through:
        wave(scene, objs)
    for m in {m for o in objs for m in _materials(o) if materials.has_emission(m) and not m.library}:
        materials.patch_material(m, wave=s.through)  # Through switches the material layout


def sound_strip(scene):
    """Find the shared music strip, including projects saved before the rename.

    Tags keep owned strips usable after a manual rename or a name collision.
    Legacy canonical names retain compatibility with projects saved before tags.
    """
    se = scene.sequence_editor
    if se:
        owned = next((strip for strip in se.strips
                      if strip.type == "SOUND" and strip.get(AUDIO_TAG)), None)
        if owned:
            return owned
        for name in (STRIP_NAME, LEGACY_STRIP_NAME):
            strip = se.strips.get(name)
            if strip and strip.type == "SOUND":
                return strip
    return None


def sync_timeline(scene):
    """Follow track duration when the music, offset or frame rate changes.

    Keep manual range edits until the track configuration changes again. The
    last frame is inclusive; a partial audio frame must remain in the range.
    """
    s = scene.afterglow
    tag = "afterglow_timeline"
    if s.sound is None:
        if tag in scene:
            del scene[tag]
        return
    if not sound_ok(s.sound):
        return
    signature = repr((audio_key(s.sound), s.start_frame,
                      scene.render.fps, scene.render.fps_base))
    if scene.get(tag) == signature:
        return
    mono, rate = load_mono(s.sound, "Fit Music Timeline")
    if not len(mono) or not np.isfinite(rate) or rate <= 0:
        return
    count = max(1, int(np.ceil(len(mono) / rate * scene.render.fps / scene.render.fps_base)))
    end = s.start_frame + count - 1
    if scene.frame_start > end:
        scene.frame_start = min(s.start_frame, end)
    scene.frame_end = end
    if scene.use_preview_range:
        scene.frame_preview_start = min(scene.frame_preview_start, end)
        scene.frame_preview_end = end
    scene[tag] = signature


def setup_playback(scene):
    """Play music at Start Frame and fit the timeline when its configuration changes."""
    sync_timeline(scene)
    s = scene.afterglow
    se = scene.sequence_editor_create()
    strip = sound_strip(scene)
    if strip and strip.sound != s.sound:
        se.strips.remove(strip)
        strip = None
    if s.sound is None:
        return
    if strip is None:
        channel = max((st.channel for st in se.strips_all), default=0) + 1
        path = bpy.path.abspath(s.sound.filepath, library=s.sound.library)
        temporary = None
        try:
            # The sequencer requires a real path to create a strip, even when the
            # desired Sound is packed. Bootstrap from its packed bytes so reopening
            # a portable .blend never depends on the original audio file's location.
            if s.sound.packed_file:
                fd, temporary = tempfile.mkstemp(suffix=os.path.splitext(path)[1] or ".wav")
                with os.fdopen(fd, "wb") as audio:
                    audio.write(s.sound.packed_file.data)
                path = temporary
            strip = se.strips.new_sound(STRIP_NAME, path, channel=channel,
                                        frame_start=s.start_frame)
            loaded = strip.sound
            strip.sound = s.sound
            # Avoid keeping an orphan Sound that refers to the bootstrap file.
            if temporary and loaded != s.sound and loaded.users == 0:
                bpy.data.sounds.remove(loaded)
        finally:
            if temporary:
                os.remove(temporary)
    strip[AUDIO_TAG] = True
    strip.frame_start = s.start_frame
    scene.sync_mode = "AUDIO_SYNC"


def sync(scene):
    """Push the scene-wide settings into every Noctune modifier. A sound whose file is
    missing is never pushed (it would crash Blender); modifiers keep the previous one."""
    nodes.migrate()  # a file saved by an older version may be edited before the next Apply
    s = scene.afterglow
    for m in {m for o in _managed(scene) for m in _materials(o) if materials.is_patched(m) and not m.library}:
        materials.patch_material(m, wave=s.through)  # no-op unless an older material layout
    push_sound = s.sound is None or sound_ok(s.sound)
    for ob in _managed(scene):
        mod = reaction(ob)
        if mod:
            if push_sound:
                nodes.set_input(mod, "Sound", s.sound)
            nodes.set_input(mod, "Start Frame", s.start_frame)
            nodes.set_input(mod, "Ripple", s.ripple)
            nodes.set_input(mod, "Beats per Sweep", s.beats_per_sweep)
            nodes.set_input(mod, "Use Baked", s.fast_playback)
            nodes.set_input(mod, "Through", s.through)
    if push_sound:
        sync_timeline(scene)
        if sound_strip(scene):
            setup_playback(scene)


def remove(scene):
    se = scene.sequence_editor
    strip = sound_strip(scene)
    # Existing environment builds retain their shared music strip in either edition.
    has_environment = any(collection.get(effect_schema.TAG) for collection in scene.collection.children)
    if strip and not has_environment:
        se.strips.remove(strip)
    from . import bake  # bake imports core
    for ob in _managed(scene):
        mod = reaction(ob)
        if mod:
            bake.unkey(ob, mod)
            if ob.type != "LIGHT":
                ob.modifiers.remove(mod)
        light_reaction.remove(ob)
        for m in _materials(ob):
            materials.unpatch_material(m)
        from . import prepare
        prepare.restore(ob)
