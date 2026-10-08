"""Collection-level operations: apply, respread, calibrate, sync, remove."""
import os

import aud
import numpy as np
import bpy
from mathutils import Vector

from . import materials, nodes

SUPPORTED = {"MESH", "CURVE", "FONT"}
MOD_NAME = "Afterglow"
STRIP_NAME = "Afterglow Audio"
LOW_HZ, HIGH_HZ = 20.0, 16000.0


def write_meter(ob, mod):
    """Push the object's Level Meter settings into its modifier."""
    m = ob.afterglow
    nodes.set_input(mod, "Meter", m.meter)
    nodes.set_input(mod, "Meter Axis", "XYZ".index(m.meter_axis))
    nodes.set_input(mod, "Meter Invert", m.meter_invert)


def sound_ok(sound):
    """Packed, or its file exists. Blender 5.2 crashes evaluating Sample Sound on a missing file."""
    return bool(sound.packed_file) or os.path.isfile(bpy.path.abspath(sound.filepath, library=sound.library))


def targets(collection):
    return sorted((o for o in collection.all_objects if o.type in SUPPORTED), key=lambda o: o.name)


def find_modifier(ob):
    return next((m for m in ob.modifiers
                 if m.type == "NODES" and m.node_group and m.node_group.get(nodes.TAG)), None)


def _materials(ob):
    return {slot.material for slot in ob.material_slots if slot.material}


def _managed(scene):
    """Objects in the current collection plus any object anywhere in the scene that still
    carries an Afterglow modifier (collection switched, object moved out)."""
    objs = {o for o in scene.objects if find_modifier(o)}
    if scene.afterglow.collection:
        objs |= set(targets(scene.afterglow.collection))
    return objs


def band(k, n):
    """Band k of n, log-spaced between LOW_HZ and HIGH_HZ."""
    ratio = HIGH_HZ / LOW_HZ
    return LOW_HZ * ratio ** (k / n), LOW_HZ * ratio ** ((k + 1) / n)


def apply(scene):
    """Add or refresh the modifier and patch materials on every emissive object in the collection.

    Returns (applied, skipped, outside, linked). 'outside' lists objects outside the collection
    that share a patched material; they have no Afterglow attributes, so they render dark.
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
    group = nodes.get_group()
    applied, skipped, patched, linked = [], [], set(), []
    for ob in targets(s.collection):
        mats = set()
        for m in _materials(ob):
            if not materials.has_emission(m):
                continue
            if m.library:
                if m not in linked:
                    linked.append(m)
            else:
                mats.add(m)
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
        mod = find_modifier(ob)
        nodes.set_input(mod, "Low", low)
        nodes.set_input(mod, "High", high)


def _level(ob, depsgraph):
    ev = ob.evaluated_get(depsgraph)
    me = ev.to_mesh()
    try:
        attr = me.attributes.get("ar_level")
        return attr.data[0].value if attr and len(attr.data) else 0.0
    finally:
        ev.to_mesh_clear()


def _seconds(sound):
    try:
        clip = aud.Sound(bpy.path.abspath(sound.filepath, library=sound.library))
        return clip.length / clip.specs[0]
    except Exception:
        raise ValueError("Calibrate needs the sound file on disk") from None


def _pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(p * len(values)))]


def calibrate(scene, samples=96, progress=None):
    """Set each object's Threshold to its band's median level and Gain so its 90th percentile
    reaches 1. Samples each modifier's raw band amplitude (decay off) across the sound.
    Returns the calibrated objects."""
    s = scene.afterglow
    objs = sorted((o for o in _managed(scene) if find_modifier(o)), key=lambda o: o.name)
    if not objs:
        raise ValueError("Apply first")
    if s.sound is None:
        raise ValueError("Pick a sound file first")
    duration = _seconds(s.sound)
    fps = scene.render.fps / scene.render.fps_base
    mods = [find_modifier(o) for o in objs]
    # Ripple listens to the kick band, which has its own scale: calibrate the pair in use.
    gain_key, threshold_key = ("Ripple Gain", "Ripple Threshold") if s.ripple else ("Gain", "Threshold")
    keys = (gain_key, threshold_key, "Decay")
    saved = [{k: nodes.get_input(m, k) for k in keys} for m in mods]
    sample_frames = [s.start_frame + round((i + 0.5) / samples * duration * fps) for i in range(samples)]
    if s.fast_playback:
        # The bake holds peaks (Decay) and may be stale; measure raw levels from its source.
        from . import bake  # bake imports core
        levels = [list(v) for v in bake.raw_levels(scene, mods, sample_frames)]
        _set_calibration(mods, levels, gain_key, threshold_key, shared=s.ripple)
        return objs
    frame = scene.frame_current
    levels = [[] for _ in objs]
    try:
        # Raw amplitude: no gain/threshold, and Decay 0 so each jump costs one FFT instead of 8
        # (random seeks miss the node's FFT cache; 8 samples made 96 jumps take ~80 s).
        for m in mods:
            for k, v in zip(keys, (1.0, 0.0, 0.0)):
                nodes.set_input(m, k, v)
        for i, f in enumerate(sample_frames):
            scene.frame_set(f)
            dg = bpy.context.evaluated_depsgraph_get()
            for ob, out in zip(objs, levels):
                out.append(_level(ob, dg))
            if progress:
                progress(i)
    finally:
        for m, old in zip(mods, saved):
            for k, v in old.items():
                nodes.set_input(m, k, v)
        scene.frame_set(frame)
    _set_calibration(mods, levels, gain_key, threshold_key, shared=s.ripple)
    return objs


def _set_calibration(mods, levels, gain_key, threshold_key, shared=False):
    if shared:  # Ripple: everyone hears the same kick band; one pair keeps Through seams smooth
        pooled = [v for values in levels for v in values]
        levels = [pooled] * len(levels)
    for m, values in zip(mods, levels):
        median, high = _pct(values, 0.5), _pct(values, 0.9)  # p90: robust to one-off clicks
        nodes.set_input(m, threshold_key, median)
        nodes.set_input(m, gain_key, 1.0 / max(high - median, 1e-4))


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
        tmin, tmax = (a - lo) / span, (b - lo) / span
        if s.ripple_reverse:
            tmin, tmax = 1.0 - tmax, 1.0 - tmin
        mod = find_modifier(ob)
        for name, value in (("Wave Radial", radial), ("Wave Axis", tuple(axis.tolist())),
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
        nodes.set_input(find_modifier(ob), "Ripple Position", 1.0 - pos if reverse and span > 1e-6 else pos)


def replace(scene):
    """Re-place every Afterglow object with the scene's Ripple From settings."""
    s = scene.afterglow
    bpy.context.view_layer.update()  # matrix_world is stale for objects moved since the last eval
    objs = sorted((o for o in _managed(scene) if find_modifier(o)), key=lambda o: o.name)
    place(objs, s.ripple_from, s.ripple_origin, s.ripple_reverse)
    if s.through:
        wave(scene, objs)
    for m in {m for o in objs for m in _materials(o) if materials.has_emission(m) and not m.library}:
        materials.patch_material(m, wave=s.through)  # Through switches the material layout


def setup_playback(scene):
    """One sound strip at Start Frame playing the scene's Sound, Sync to Audio on, and the
    frame range extended to cover the song. Removes the strip when there is no sound."""
    s = scene.afterglow
    se = scene.sequence_editor_create()
    strip = se.strips.get(STRIP_NAME)
    if strip and strip.sound != s.sound:
        se.strips.remove(strip)
        strip = None
    if s.sound is None:
        return
    if strip is None:
        channel = max((st.channel for st in se.strips_all), default=0) + 1
        strip = se.strips.new_sound(STRIP_NAME, bpy.path.abspath(s.sound.filepath, library=s.sound.library),
                                    channel=channel, frame_start=s.start_frame)
        strip.sound = s.sound
    strip.frame_start = s.start_frame
    scene.sync_mode = "AUDIO_SYNC"
    scene.frame_end = max(scene.frame_end, strip.frame_final_end - 1)


def sync(scene):
    """Push the scene-wide settings into every Afterglow modifier. A sound whose file is
    missing is never pushed (it would crash Blender); modifiers keep the previous one."""
    nodes.migrate()  # a file saved by an older version may be edited before the next Apply
    s = scene.afterglow
    push_sound = s.sound is None or sound_ok(s.sound)
    for ob in _managed(scene):
        mod = find_modifier(ob)
        if mod:
            if push_sound:
                nodes.set_input(mod, "Sound", s.sound)
            nodes.set_input(mod, "Start Frame", s.start_frame)
            nodes.set_input(mod, "Ripple", s.ripple)
            nodes.set_input(mod, "Beats per Sweep", s.beats_per_sweep)
            nodes.set_input(mod, "Use Baked", s.fast_playback)
            nodes.set_input(mod, "Through", s.through)
    if push_sound and scene.sequence_editor and scene.sequence_editor.strips.get(STRIP_NAME):
        setup_playback(scene)


def remove(scene):
    se = scene.sequence_editor
    strip = se and se.strips.get(STRIP_NAME)
    if strip:
        se.strips.remove(strip)
    from . import bake  # bake imports core
    for ob in _managed(scene):
        mod = find_modifier(ob)
        if mod:
            bake.unkey(ob, mod)
            ob.modifiers.remove(mod)
        for m in _materials(ob):
            materials.unpatch_material(m)
