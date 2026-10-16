"""Environment effects: onset analysis, venue shapes, light-only pooled builds, motion, stacking, removal."""
import math
import random
import os
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402
import helpers  # noqa: E402  (puts repo root on sys.path)
import afterglow  # noqa: E402
from helpers import emission_material, plane  # noqa: E402
from afterglow import core, effects  # noqa: E402

BASS_PULSES = (1.0, 2.0, 3.0, 4.0)        # 80 Hz pulses, 0.12 s each
TREBLE_PULSES = (1.5, 2.5)                # 4 kHz pulses, 0.12 s each
FIXTURE_NOTE = "synthetic pulse fixture (not a real track)"


def _write_wav(path, seconds, pulses):
    """pulses: [(t0, freq)] 0.12 s Hann-gated sines, digital silence elsewhere."""
    rate = 44100
    x = np.zeros(int(rate * seconds), np.float32)
    k = int(0.12 * rate)
    for t0, freq in pulses:
        i0 = int(t0 * rate)
        tt = np.arange(k, dtype=np.float32) / rate
        x[i0:i0 + k] += (0.9 * np.hanning(k) * np.sin(2 * np.pi * freq * tt)).astype(np.float32)
    data = (x * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())
    return path


def pulse_wav():
    path = os.path.join(tempfile.gettempdir(), "afterglow_fx_pulses.wav")
    if not os.path.exists(path):
        _write_wav(path, 6.0, [(t, 80.0) for t in BASS_PULSES] + [(t, 4000.0) for t in TREBLE_PULSES])
    return path


def silent_wav():
    path = os.path.join(tempfile.gettempdir(), "afterglow_fx_silence.wav")
    if not os.path.exists(path):
        _write_wav(path, 6.0, [])
    return path


def stage(sound_path=None, objs=((-4.0, 0.0), (4.0, 0.0)), shape="CIRCLE"):
    """A venue collection, a sound, and the effects settings under test."""
    scene = bpy.context.scene
    coll = bpy.data.collections.new("Venue")
    scene.collection.children.link(coll)
    for i, (x, y) in enumerate(objs):
        ob = plane(f"v{i}", coll, emission_material(f"mv{i}"))
        ob.location = (x, y, 0.0)
    bpy.context.view_layer.update()
    scene.afterglow.collection = coll
    scene.afterglow.sound = bpy.data.sounds.load(sound_path or pulse_wav(), check_existing=False)
    scene.render.fps = 24
    scene.afterglow.effects.venue_shape = shape
    return scene


def times_of(events):
    return [t for t, _ in events]


# ---------------------------------------------------------------- slice 1: analysis

def test_analyze_finds_pulse_events():
    scene = stage()
    scene.afterglow.effects.strikes.enabled = True
    scene.afterglow.effects.strikes.weight = 1.0  # weight < 1 thins the stream by design
    out = effects.analyze(scene)
    ts = times_of(out["strikes"])
    assert len(ts) == len(BASS_PULSES), ts
    for got, want in zip(ts, BASS_PULSES):
        assert abs(got - want) < 0.12, (got, want)
    assert all(s > 0.5 for _, s in out["strikes"])


def test_analyze_silence_no_events():
    scene = stage(silent_wav())
    scene.afterglow.effects.strikes.enabled = True
    out = effects.analyze(scene)
    assert out["strikes"] == []


def test_analyze_deterministic():
    scene = stage()
    fx = scene.afterglow.effects
    for name in effects.LAYERS:
        getattr(fx, name).enabled = True
    a = effects.analyze(scene)
    b = effects.analyze(scene)
    assert a == b


def test_weight_zero_suppresses_events():
    scene = stage()
    scene.afterglow.effects.strikes.enabled = True
    scene.afterglow.effects.strikes.weight = 0.0
    assert effects.analyze(scene)["strikes"] == []


def test_partial_weight_thins_deterministically():
    scene = stage()
    fx = scene.afterglow.effects
    fx.strikes.enabled = True
    full = effects.analyze(scene)  # default weight 0.8
    fx.strikes.weight = 1.0
    all_ev = effects.analyze(scene)
    assert len(all_ev["strikes"]) == len(BASS_PULSES)
    assert 0 < len(full["strikes"]) <= len(all_ev["strikes"])
    # thinned events are a subset: weight never moves an onset
    assert {t for t, _ in full["strikes"]} <= {t for t, _ in all_ev["strikes"]}
    assert effects.analyze(scene) == all_ev  # and a rebuild sees the same show


def test_trigger_band_selects_events():
    scene = stage()
    fx = scene.afterglow.effects
    fx.blooms.enabled = True
    fx.blooms.weight = 1.0
    fx.blooms.trigger_band = "HIGH"
    ts = times_of(effects.analyze(scene)["blooms"])
    assert len(ts) == len(TREBLE_PULSES), ts
    for got, want in zip(ts, TREBLE_PULSES):
        assert abs(got - want) < 0.12, (got, want)


def test_all_layers_disabled_by_default():
    scene = stage()
    out = effects.analyze(scene)
    assert set(out) == set(effects.LAYERS)
    assert all(v == [] for v in out.values())


def test_registered_settings_exist():
    scene = bpy.context.scene
    fx = scene.afterglow.effects
    assert fx.bl_rna.properties["venue_shape"].enum_items.keys() == list(effects.SHAPES)
    for name in effects.LAYERS:
        layer = getattr(fx, name)
        for prop in ("enabled", "weight", "intensity", "color", "timing", "trigger_band"):
            assert prop in layer.bl_rna.properties, (name, prop)
        from afterglow import props  # each layer its own colour: tell them apart
        assert all(math.isclose(a, b, abs_tol=1e-6) for a, b in zip(layer.color, props.LAYER_COLORS[name])), name
        # keyframeable floats, not fixed constants
        assert layer.bl_rna.properties["intensity"].type == "FLOAT"


# ------------------------------------------------- slice 1b: tempo-locked placement

BEAT_BPM = 120.0
BEAT_STEP = 60.0 / BEAT_BPM        # 0.5 s beat grid
BEAT_COUNT = 16
BEAT_START = 1.0
BEAT_JITTER = 0.06                 # every pulse sits deliberately off the grid


def beat_wav():
    """120 BPM kick train, 10 s; alternating pulses jittered +-60 ms off the beat."""
    path = os.path.join(tempfile.gettempdir(), "afterglow_fx_beatgrid.wav")
    if not os.path.exists(path):
        pulses = [(BEAT_START + BEAT_STEP * k + (BEAT_JITTER if k % 2 else -BEAT_JITTER), 80.0)
                  for k in range(BEAT_COUNT)]
        _write_wav(path, 10.0, pulses)
    return path


def _beat_phase(path, ts):
    """Beat phase of times ts under the repo's own tempo curve, plus the curve's BPM."""
    from afterglow import tempo
    with wave.open(path, "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768.0
        rate = w.getframerate()
    curve = tempo.tempo_curve(data, rate)
    ct = np.array([t for t, _ in curve])
    cb = np.array([b for _, b in curve])
    tg = np.arange(0.0, len(data) / rate + 1.0, 0.01)
    bpm = np.interp(tg, ct, cb)
    ph = np.concatenate([[0.0], np.cumsum((bpm[:-1] + bpm[1:]) / 2 * 0.01 / 60.0)])
    return np.interp(ts, tg, ph), float(np.mean(cb))


def _grid_dev(path, ts):
    """(worst deviation from the measured pulse grid, mean pulse BPM)."""
    if len(ts) < 2:
        return 0.0, 0.0
    ts = np.asarray(ts, dtype=float)
    step = float(np.median(np.diff(ts)))
    anchor = float(np.mean(np.mod(ts, step)))
    dev = np.abs(((np.mod(ts - anchor, step) + step / 2) % step) - step / 2) / step
    return float(dev.max()), 60.0 / step


def test_events_snap_to_detected_beat_grid():
    scene = stage(beat_wav())
    fx = scene.afterglow.effects
    fx.strikes.enabled = True
    fx.strikes.weight = 1.0
    out = effects.analyze(scene)
    ts = times_of(out["strikes"])
    assert len(ts) == BEAT_COUNT, ts                 # snapping never drops or invents events
    assert all(s > 0.5 for _, s in out["strikes"])   # onset strength is preserved
    dev, bpm = _grid_dev(beat_wav(), ts)
    assert abs(bpm - BEAT_BPM) < 6.0, bpm            # the train really detects near 120 BPM
    assert dev < 0.06, dev                           # phase-locked to the detected beat grid
    # and strictly tighter than the raw amplitude-run peaks, which carry the jitter
    mono, rate = effects.bake._load(scene)
    ft, fs = effects.bake.band_series(mono, rate, [effects.BANDS["FULL"]])
    raw_dev, _ = _grid_dev(beat_wav(), times_of(effects._onsets(ft, fs[0])))
    assert raw_dev > 2 * dev + 0.02, (raw_dev, dev)


def test_beat_grid_silence_stays_empty():
    scene = stage(silent_wav())
    fx = scene.afterglow.effects
    for name in effects.LAYERS:
        getattr(fx, name).enabled = True
        getattr(fx, name).weight = 1.0
    assert all(v == [] for v in effects.analyze(scene).values())

def test_contradicting_tempo_curve_keeps_raw_onsets():
    """Ambiguity guard: when the detector's curve cannot be reconciled with the pulse
    grid at any near-octave ratio, snapping must stand down and onsets keep their timing."""
    scene = stage(beat_wav())
    fx = scene.afterglow.effects
    fx.strikes.enabled = True
    fx.strikes.weight = 1.0
    real = effects.tempo.tempo_curve
    effects.tempo.tempo_curve = lambda samples, rate: [(5.0, 97.0)]  # 0.618 s: no harmonic of ~0.5 s
    try:
        ts = times_of(effects.analyze(scene)["strikes"])
    finally:
        effects.tempo.tempo_curve = real
    mono, rate = effects.bake._load(scene)
    lt, ls = effects.bake.band_series(mono, rate, [effects.BANDS["LOW"]])
    assert ts == times_of(effects._onsets(lt, ls[0]))  # byte-identical raw onsets


# ---------------------------------------------------------------- slice 2: venue

def test_venue_bounds_span_whole_collection():
    scene = stage(objs=((-10.0, 0.0), (10.0, 0.0)))
    v = effects.make_venue(scene)
    # planes are 2 wide: world x spans -11..11, margin 1.15 default
    assert abs(v.center.x) < 1e-4 and abs(v.center.y) < 1e-4
    assert abs(v.rx - 11.0 * 1.15) < 0.05, v.rx
    assert abs(v.ry - 1.0 * 1.15) < 0.05, v.ry
    assert v.z1 - v.z0 == scene.afterglow.effects.height
    # venue scale follows the collection's spread, never a single object's size
    assert v.rx > 10.0


def test_venue_needs_collection():
    scene = bpy.context.scene
    scene.afterglow.collection = None
    try:
        effects.make_venue(scene)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_shape_changes_path_geometry():
    pts = {}
    for shape in effects.SHAPES:
        scene = stage(shape=shape)
        v = effects.make_venue(scene)
        pts[shape] = [v.path_point(u) for u in (0.0, 0.125, 0.3, 0.55, 0.8)]
    for a in pts:
        for b in pts:
            if a < b:
                assert any((pa - pb).length > 0.5 for pa, pb in zip(pts[a], pts[b])), (a, b)


def test_circle_path_is_equidistant():
    scene = stage(shape="CIRCLE")
    v = effects.make_venue(scene)
    radii = {(v.path_point(i / 37) - v.center3).xy.length for i in range(37)}
    # an ellipse only if rx == ry here (bounds are symmetric), so one radius fits all
    assert max(radii) - min(radii) < 1e-6, (min(radii), max(radii))


def test_square_path_lies_on_perimeter():
    scene = stage(objs=((-4.0, -4.0), (4.0, 4.0)), shape="SQUARE")
    v = effects.make_venue(scene)
    r = max(v.rx, v.ry)
    for i in range(40):
        p = v.path_point(i / 40) - v.center3
        assert abs(max(abs(p.x), abs(p.y)) - r) < 1e-6, (i, p)
    # tangent is unit-length and perpendicular to the rim for most of each edge
    t = v.path_tangent(0.05)
    assert abs(t.length - 1.0) < 1e-6


def test_footprint_stays_inside_shape():
    import random as _random
    for shape in effects.SHAPES:
        scene = stage(objs=((-4.0, -3.0), (4.0, 3.0)), shape=shape)
        v = effects.make_venue(scene)
        rng = _random.Random(7)
        for _ in range(60):
            p = v.footprint(rng)
            if shape == "CIRCLE":
                assert (p - v.center3).xy.length <= v.radius + 1e-6
            elif shape in ("SQUARE", "RECT"):
                r = max(v.rx, v.ry) if shape == "SQUARE" else None
                assert abs(p.x - v.center.x) <= (r if r else v.rx) + 1e-6
                assert abs(p.y - v.center.y) <= (r if r else v.ry) + 1e-6
            else:
                assert _point_in_poly(p, v.polygon()), (shape, tuple(p))
            assert abs(p.z - v.z0) < 1e-6


def _point_in_poly(p, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if (a.y > p.y) != (b.y > p.y):
            x = a.x + (p.y - a.y) * (b.x - a.x) / (b.y - a.y)
            if p.x <= x + 1e-6:
                inside = not inside
    return inside


# ---------------------------------------------------------------- slice 3: build lifecycle

def fx_objs(layer=None):
    pre = f"AFX_{layer}" if layer else "AFX"
    return sorted((ob for ob in bpy.data.objects if ob.name.startswith(pre)), key=lambda o: o.name)


def build_stage(**kw):
    scene = stage(**kw)
    fx = scene.afterglow.effects
    fx.strikes.enabled = True
    fx.strikes.weight = 1.0
    return scene


def channel_signature():
    out = []
    for ob in fx_objs():
        for fc in helpers.fcurves(ob):
            out.append((ob.name, fc.data_path, fc.array_index,
                        tuple(round(kp.co[0], 4) for kp in fc.keyframe_points),
                        tuple(round(kp.co[1], 6) for kp in fc.keyframe_points)))
        if ob.type == "LIGHT":
            for fc in helpers.fcurves(ob.data):
                out.append((ob.name + "#energy", fc.data_path, fc.array_index,
                            tuple(round(kp.co[0], 4) for kp in fc.keyframe_points),
                            tuple(round(kp.co[1], 6) for kp in fc.keyframe_points)))
    return sorted(out)


def test_build_creates_only_tagged_lights():
    scene = build_stage()
    counts = effects.build(scene)
    assert counts["strikes"] == len(BASS_PULSES), counts
    assert effects.built_counts(scene) == counts  # kept for the panel
    colls = [c for c in bpy.data.collections if c.get(effects.TAG)]
    assert len(colls) == 1 and colls[0].name == effects.COLLECTION
    objs = fx_objs("strikes")
    assert len(objs) == effects.POOL["strikes"]        # one real light per pool slot
    assert all(ob.type == "LIGHT" for ob in objs)      # lights only, never meshes
    assert all(ob.data.type in ("POINT", "SPOT") for ob in objs)
    assert all(ob.get(effects.TAG) and ob.data.get(effects.TAG) for ob in objs)
    # every pool light lives in the tagged collection
    assert set(colls[0].all_objects) == set(objs)


def test_build_generates_no_meshes_or_materials():
    scene = stage()
    enable(scene, *effects.LAYERS)
    user_meshes = set(bpy.data.meshes)
    user_mats = set(bpy.data.materials)
    effects.build(scene)
    assert not [ob for ob in bpy.data.objects if ob.get(effects.TAG) and ob.type != "LIGHT"]
    assert not [m for m in bpy.data.meshes if m.get(effects.TAG)]
    assert not [m for m in bpy.data.materials if m.get(effects.TAG)]
    assert set(bpy.data.meshes) == user_meshes         # the venue is left exactly as built
    assert set(bpy.data.materials) == user_mats
    want = sum(effects.POOL[n] * (scene.afterglow.effects.cascade_count if n == "downward" else 1)
               for n in effects.LAYERS)
    assert len(fx_objs()) == want                      # the expected tagged LIGHT count


def test_rebuild_is_idempotent():
    scene = build_stage()
    effects.build(scene)
    first = channel_signature()
    n_coll = len(bpy.data.collections)
    n_light = len(bpy.data.lights)
    effects.build(scene)
    assert len([c for c in bpy.data.collections if c.get(effects.TAG)]) == 1
    assert len(bpy.data.collections) == n_coll
    assert len(bpy.data.lights) == n_light  # no leaked copies
    assert channel_signature() == first    # same show, key for key


def test_build_is_deterministic_across_sessions():
    scene = build_stage()
    effects.build(scene)
    sig = channel_signature()
    effects.remove(scene)
    effects.build(scene)
    assert channel_signature() == sig


def test_remove_restores_user_scene():
    scene = build_stage()
    user_objs = set(bpy.data.objects)
    user_mats = set(bpy.data.materials)
    user_meshes = set(bpy.data.meshes)
    effects.build(scene)
    n = effects.remove(scene)
    assert n == effects.POOL["strikes"]              # lights only: one object per slot
    assert not [c for c in bpy.data.collections if c.get(effects.TAG)]
    assert not fx_objs()
    assert set(bpy.data.objects) == user_objs
    assert set(bpy.data.materials) == user_mats
    assert set(bpy.data.meshes) == user_meshes
    assert not [a for a in bpy.data.actions if a.get(effects.TAG)]
    assert not [l for l in bpy.data.lights if l.get(effects.TAG)]
    assert not [m for m in bpy.data.materials if m.get(effects.TAG)]
    # the user's own material is untouched
    mat = bpy.data.objects["v0"].data.materials[0]
    assert [n.name for n in mat.node_tree.nodes] == ["Emission", "Material Output"]


def test_disabled_layers_keep_last_build():
    scene = stage()
    scene.afterglow.effects.strikes.enabled = True
    effects.build(scene)
    built = len(fx_objs())
    scene.afterglow.effects.strikes.enabled = False
    try:
        effects.build(scene)
    except ValueError as e:
        assert "Enable an effect layer" in str(e), str(e)
    else:
        raise AssertionError("an empty build must refuse")
    assert built and len(fx_objs()) == built  # the last build survives


def test_build_needs_sound():
    scene = stage()
    scene.afterglow.sound = None
    try:
        effects.build(scene)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_silence_build_is_dark():
    scene = stage(sound_path=silent_wav())
    fx = scene.afterglow.effects
    for name in ("strikes", "downward", "trails"):
        getattr(fx, name).enabled = True
        getattr(fx, name).weight = 1.0
    counts = effects.build(scene)
    assert all(v == 0 for v in counts.values()), counts
    objs = fx_objs()
    assert objs and all(ob.type == "LIGHT" for ob in objs)  # silence builds a dark, mesh-free pool
    for f in range(0, 145, 7):
        scene.frame_set(f)
        for ob in objs:
            assert ob.data.energy == 0.0, (ob.name, f)


# ---------------------------------------------------------------- slice 4: motion & timing

def lit_rows(scene, name, value_fn, lo, hi, thresh=1e-6):
    """(frame, value) for every frame in [lo, hi) where the named effect light is lit."""
    ob = bpy.data.objects[name]
    out = []
    for f in range(lo, hi):
        scene.frame_set(f)
        if ob.data.energy > thresh:
            out.append((f, value_fn(ob)))
    return out


def enable(scene, *layers):
    fx = scene.afterglow.effects
    for name in layers:
        layer = getattr(fx, name)
        layer.enabled = True
        layer.weight = 1.0


def test_downward_trail_is_a_smooth_light_cascade():
    scene = stage()
    enable(scene, "downward")
    effects.build(scene)
    objs = fx_objs("downward")
    assert all(ob.type == "LIGHT" for ob in objs)     # no geometry anywhere in the layer
    assert len(objs) == effects.POOL["downward"] * scene.afterglow.effects.cascade_count
    # head light of the first event: smooth monotonic fall from the venue top to the ground
    rows = lit_rows(scene, "AFX_downward_0_light0", lambda ob: ob.location.z, lo=24, hi=47)
    frames = [f for f, _ in rows]
    zs = [z for _, z in rows]
    assert len(frames) >= 5, frames
    assert all(b <= a + 1e-6 for a, b in zip(zs, zs[1:])), zs        # never rises
    steps = [a - b for a, b in zip(zs, zs[1:])]
    assert max(steps) <= 1.6 * min(steps) + 1e-6, steps              # steady glide, no blocky jumps
    assert zs[0] > 6.5 and zs[-1] < 2.0, (zs[0], zs[-1])             # venue top (z1=8) to near ground
    assert abs(frames[0] - 25) <= 4, frames[0]                       # first pulse at 1.0 s -> frame ~25
    # mid-event the cascade lights spread vertically: a trail of lights, not one dot
    spread = 0.0
    for f in range(24, 45):
        scene.frame_set(f)
        zs2 = [ob.location.z for ob in objs if ob.data.energy > 1.0]
        if len(zs2) >= 2:
            spread = max(spread, max(zs2) - min(zs2))
    assert spread > 0.5, spread


def test_downward_rebuild_and_remove_lifecycle():
    scene = stage()
    enable(scene, "downward")
    user_objs = set(bpy.data.objects)
    effects.build(scene)
    sig = channel_signature()
    n_light = len(bpy.data.lights)
    effects.build(scene)
    assert channel_signature() == sig                                # rebuild is idempotent
    assert len(bpy.data.lights) == n_light
    n = effects.remove(scene)
    assert n == effects.POOL["downward"] * scene.afterglow.effects.cascade_count       # remove is safe and total
    assert not fx_objs()
    assert not [l for l in bpy.data.lights if l.get(effects.TAG)]
    assert not [a for a in bpy.data.actions if a.get(effects.TAG)]
    assert set(bpy.data.objects) == user_objs                        # the venue survives untouched


def test_cascade_up_center_count_and_size():
    scene = stage()
    enable(scene, "downward")
    fx = scene.afterglow.effects
    fx.cascade_direction, fx.cascade_placement = "UP", "CENTER"
    fx.cascade_count, fx.cascade_size = 5, 0.8
    effects.build(scene)
    objs = fx_objs("downward")
    assert len(objs) == effects.POOL["downward"] * 5                 # Count lights per slot
    assert all(abs(ob.data.shadow_soft_size - 0.8) < 1e-6 for ob in objs)     # Size is each light's radius
    v = effects.make_venue(scene)
    rows = lit_rows(scene, "AFX_downward_0_light0", lambda ob: tuple(ob.location), lo=24, hi=47)
    zs = [p[2] for _, p in rows]
    assert len(zs) >= 5 and all(b >= a - 1e-6 for a, b in zip(zs, zs[1:])), zs  # never falls
    assert zs[0] < 2.0 and zs[-1] > 6.5, (zs[0], zs[-1])             # floor to venue top
    for _, (x, y, _) in rows:                                        # Center: the venue's own axis
        assert math.dist((x, y), (v.center.x, v.center.y)) < 1e-4, (x, y)


def test_cascade_travels_toward_a_moving_camera():
    scene = stage()
    enable(scene, "downward")
    fx = scene.afterglow.effects
    fx.cascade_direction = "TO_CAMERA"
    try:
        effects.build(scene)
    except ValueError as e:
        assert "active camera" in str(e), str(e)
    else:
        raise AssertionError("a camera cascade needs a camera")
    ceiling = plane("ceiling")
    ceiling.location, ceiling.scale = (0.0, 0.0, 6.0), (20.0, 20.0, 1.0)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.rotation_euler = (math.pi, 0.0, 0.0)                         # looking straight up
    for f, x in ((1, 0.0), (121, 6.0)):                              # slides along +X
        cam.location = (x, 0.0, 1.0)
        cam.keyframe_insert("location", frame=f)
    for fc in effects._fcurves_of(cam):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    scene.frame_set(7)
    effects.build(scene)
    assert scene.frame_current == 7                                  # following the camera restores the frame
    rows = lit_rows(scene, "AFX_downward_0_light0", lambda ob: tuple(ob.location), lo=24, hi=47)
    zs = [p[2] for _, p in rows]
    assert len(zs) >= 5 and all(b <= a + 1e-6 for a, b in zip(zs, zs[1:])), zs  # ceiling -> lens
    assert abs(zs[0] - (6.0 - effects.CAMERA_INSET)) < 0.3, zs[0]    # starts at the surface it sees
    assert abs(zs[-1] - (1.0 + effects.CAMERA_NEAR)) < 0.3, zs[-1]   # ends just in front of the lens
    x_at_event = 6.0 * (rows[0][0] - 1) / 120.0
    assert all(abs(x - x_at_event) < 0.3 for _, (x, _, _) in rows), rows  # where the camera was
    sig = effects.settings_sig(scene)
    scene.frame_set(60)
    assert effects.settings_sig(scene) == sig                        # scrubbing is not outdated
    fc = effects._fcurves_of(cam)[0]
    fc.keyframe_points[1].co.y = 3.0
    assert effects.settings_sig(scene) != sig                        # re-keying the camera is


def test_live_controls_retune_the_build_without_rebuilding():
    scene = stage()
    enable(scene, "downward", "strikes")
    fx = scene.afterglow.effects
    fx.beams = True
    effects.build(scene)
    sig = effects.settings_sig(scene)
    ob = bpy.data.objects["AFX_downward_0_light0"]
    shaft = next(c for c in bpy.data.objects["AFX_strikes_0_light"].children if c.get(effects.TAG))
    peak = max(lit_rows(scene, ob.name, lambda o: o.data.energy, lo=24, hi=47))[1]
    glow_peak = max(kp.co.y for fc in effects._fcurves_of(shaft) if fc.data_path == effects.GLOW_PATH
                    for kp in fc.keyframe_points)
    objs_before = set(bpy.data.objects)
    fx.downward.intensity = 2.0
    fx.downward.color = (1.0, 0.0, 0.0)
    fx.cascade_size = 1.5
    fx.strikes.intensity = 0.0                                       # dark...
    fx.strikes.intensity = 0.5                                       # ...and back: 0 stays recoverable
    fx.beam_glow = 3.0
    assert set(bpy.data.objects) == objs_before                      # retuned in place, not rebuilt
    assert abs(max(lit_rows(scene, ob.name, lambda o: o.data.energy, lo=24, hi=47))[1] - 2 * peak) < 1e-2 * peak
    assert tuple(ob.data.color) == (1.0, 0.0, 0.0) and abs(ob.data.shadow_soft_size - 1.5) < 1e-6
    glow_now = max(kp.co.y for fc in effects._fcurves_of(shaft) if fc.data_path == effects.GLOW_PATH
                   for kp in fc.keyframe_points)
    assert abs(glow_now - 0.5 * glow_peak) < 1e-3 * glow_peak, (glow_now, glow_peak)
    mat = shaft.material_slots[0].material
    assert abs(mat.node_tree.nodes["Glow"].inputs[1].default_value - 3.0 * mat[effects.GAIN]) < 1e-4
    assert effects.settings_sig(scene) == sig                        # live edits never mark it outdated
    fx.downward.timing = 2.0
    assert effects.settings_sig(scene) != sig                        # Timing still needs Build


def test_travelling_trail_rides_circular_path():
    scene = stage()  # objects at x=+-4: circle radius = 5*1.15 = 5.75, path z = 0.72*8
    enable(scene, "trails")
    effects.build(scene)
    v = effects.make_venue(scene)
    pts = [p for _, p in lit_rows(scene, "AFX_trails_0_light",
                                  lambda ob: tuple(ob.location), lo=24, hi=75)]
    assert len(pts) >= 8, len(pts)
    for p in pts:
        d = math.dist(p[:2], (v.center.x, v.center.y))
        assert abs(d - v.radius) < 0.05, (p, d, v.radius)   # rides the venue circle
        assert abs(p[2] - v.path_z) < 1e-3, p
    angles = [math.atan2(p[1] - v.center.y, p[0] - v.center.x) for p in pts]
    unw = np.unwrap(angles)
    steps = np.diff(unw)
    assert np.all(steps > 1e-4) or np.all(steps < -1e-4), steps  # one direction, real travel
    assert abs(unw[-1] - unw[0]) > 0.5                          # sweeps a visible arc


def test_venue_shape_routes_trails():
    positions = {}
    for shape in ("CIRCLE", "SQUARE"):
        scene = stage(shape=shape)
        enable(scene, "trails")
        effects.build(scene)
        ob = None
        pos = []
        for f in range(20, 75):
            ob = bpy.data.objects["AFX_trails_0_light"]
            scene.frame_set(f)
            if ob.data.energy > 1e-6:
                pos.append(tuple(ob.location.xy))
        positions[shape] = pos
    sq, ci = positions["SQUARE"], positions["CIRCLE"]
    assert sq and ci, positions.keys()
    v_r = 5.75  # 5 (plane half-span + origin offset) x 1.15 margin
    # the square route hugs the square rim on every sample
    assert all(abs(max(abs(x), abs(y)) - v_r) < 0.05 for x, y in sq), sq[:5]
    # the circular route dips well inside that box near the diagonals: shape really routes
    assert all(abs(math.hypot(x, y) - v_r) < 0.05 for x, y in ci), ci[:5]
    assert any(abs(max(abs(x), abs(y)) - v_r) > 0.5 for x, y in ci)


def test_gun_burst_is_fast_and_directed():
    scene = stage()
    enable(scene, "gun")
    effects.build(scene)
    v = effects.make_venue(scene)
    pts = [(f, tuple(ob.location)) for f, ob in
           ((f, bpy.data.objects["AFX_gun_0_light"]) for f in range(24, 40))  # first burst only
           if (scene.frame_set(f) or ob.data.energy > 1e-6)]
    assert len(pts) >= 3, pts
    dists = [math.dist(p[:2], (v.center.x, v.center.y)) for _, p in pts]
    assert v.radius - 1.0 < dists[0] <= v.radius + 0.05, dists[0]  # fired from the rim (the
    # first visible frame is already a hair into the burst: attack is 15 ms)
    assert dists[-1] < 0.5 * v.radius, dists[-1]         # driven toward the centre
    assert all(b <= a + 1e-6 for a, b in zip(dists, dists[1:]))
    assert pts[-1][0] - pts[0][0] <= 10                  # short directed burst, not a drift


def test_strikes_spot_is_static_and_aimed_down():
    scene = stage()
    enable(scene, "strikes")
    effects.build(scene)
    ob = bpy.data.objects["AFX_strikes_0_light"]
    assert ob.data.type == "SPOT"
    rows = [(f, tuple(ob.location)) for f in range(15, 45)
            if (scene.frame_set(f) or ob.data.energy > 1e-6)]
    assert rows
    xy = {(round(p[0], 6), round(p[1], 6)) for _, p in rows}
    assert len(xy) == 1, xy                                     # the spot does not wander
    v = effects.make_venue(scene)
    assert abs(rows[0][1][2] - v.z1) < 1e-3, rows[0]            # crowning the venue top
    scene.frame_set(rows[len(rows) // 2][0])
    d = ob.matrix_world.to_3x3() @ Vector((0.0, 0.0, -1.0))     # spots shine along local -Z
    assert d.z < -0.99, tuple(d)                                # aimed straight down at the floor


def test_timing_mask_dark_outside_events():
    scene = stage()
    enable(scene, "strikes", "downward", "blooms", "flashes", "trails", "gun")
    effects.build(scene)
    objs = fx_objs()
    assert all(ob.type == "LIGHT" for ob in objs)
    for f in (2, 5, 20, 200, 210):  # before the first pulse (~26.5) and past the song tail
        scene.frame_set(f)
        for ob in objs:
            assert ob.data.energy == 0.0, (ob.name, f)
    # and lit inside an event: the 1.0 s pulse peaks around frame 27-28
    scene.frame_set(28)
    assert any(ob.type == "LIGHT" and ob.data.energy > 0 for ob in objs)


def test_layers_stack_simultaneously():
    scene = stage()
    enable(scene, "strikes", "downward", "flashes")
    counts = effects.build(scene)
    assert set(counts) == {"strikes", "downward", "flashes"}  # stack, not a dropdown
    assert all(n > 0 for n in counts.values())
    for layer in ("strikes", "downward", "flashes"):
        assert fx_objs(layer), layer
    # a common frame where two layers are lit at once (first pulse peaks ~27-28)
    scene.frame_set(28)
    lit = {ob.name.split("_")[1] for ob in fx_objs() if ob.type == "LIGHT" and ob.data.energy > 0}
    assert len(lit) >= 2, lit


def test_intensity_animation_is_sampled():
    scene = stage()
    enable(scene, "strikes")
    scene.afterglow.effects.strikes.intensity = 0.2
    scene.keyframe_insert("afterglow.effects.strikes.intensity", frame=1)
    scene.afterglow.effects.strikes.intensity = 3.0
    scene.keyframe_insert("afterglow.effects.strikes.intensity", frame=100)
    effects.build(scene)
    lights = [ob for ob in fx_objs("strikes") if ob.type == "LIGHT"]

    def peak_around(f0, f1):
        best = 0.0
        for f in range(f0, f1):
            scene.frame_set(f)
            best = max(best, max(ob.data.energy for ob in lights))
        return best

    early = peak_around(24, 34)   # 1.0 s pulse: intensity ~0.9
    late = peak_around(95, 105)   # 4.0 s pulse: intensity ~2.6
    assert early > 0 and late > early * 1.8, (early, late)


def test_timing_scales_event_duration():
    spans = {}
    for timing in (1.0, 2.0):
        scene = stage()
        enable(scene, "strikes")
        scene.afterglow.effects.strikes.timing = timing
        effects.build(scene)
        light = next(ob for ob in fx_objs("strikes") if ob.type == "LIGHT")
        active = []
        for f in range(24, 50):  # first event only; the next pulse is at frame ~50
            scene.frame_set(f)
            if light.data.energy > 1.0:
                active.append(f)
        spans[timing] = (min(active), max(active))
    d1 = spans[1.0][1] - spans[1.0][0]
    d2 = spans[2.0][1] - spans[2.0][0]
    assert d1 >= 4 and 1.5 * d1 <= d2 <= 2.6 * d1 + 2, spans


def dense_wav():
    path = os.path.join(tempfile.gettempdir(), "afterglow_fx_dense.wav")
    if not os.path.exists(path):
        _write_wav(path, 6.0, [(0.5 + 0.32 * i, 80.0) for i in range(16)])
    return path


def lit_moves(ob, layer):
    """(seconds, distance) between consecutive location keys of a light, for every key
    interval it is lit through (energy at the midpoint above 5% of the layer's peak)."""
    loc = sorted((fc for fc in helpers.fcurves(ob) if fc.data_path == "location"),
                 key=lambda fc: fc.array_index)
    energy = next(fc for fc in helpers.fcurves(ob.data) if fc.data_path == "energy")
    frames = sorted({round(kp.co[0], 4) for fc in loc for kp in fc.keyframe_points})
    out = []
    for fa, fb in zip(frames, frames[1:]):
        if energy.evaluate((fa + fb) / 2) > 0.05 * effects.ENERGY[layer]:
            a = Vector([fc.evaluate(fa) for fc in loc])
            b = Vector([fc.evaluate(fb) for fc in loc])
            out.append(((fb - fa) / 24.0, (b - a).length))
    return out


def test_busy_pool_hands_slot_over_cleanly():
    """More overlapping events than pool slots: the stolen slot's old event is cut short,
    never interleaved with the new one (which made lights jump between two spots)."""
    for layer, timing in (("blooms", 4.0), ("trails", 1.0)):
        scene = stage(dense_wav())
        enable(scene, layer)
        getattr(scene.afterglow.effects, layer).timing = timing
        ts = times_of(effects.analyze(scene)[layer])
        dur = effects.DUR[layer] * timing
        n = effects.POOL[layer]
        assert len(ts) > 2 * n and ts[n] - ts[0] < dur, ts  # the pool must run out
        effects.build(scene)
        v = effects.make_venue(scene)
        speed = 0.5 * v._arclen()[1][-1] / dur if layer == "trails" else 0.0  # max trail travel
        for ob in fx_objs(layer):
            for dt, dist in lit_moves(ob, layer):
                assert dist <= 1.5 * speed * dt + 1e-4, (layer, ob.name, dt, dist)


def test_scenes_keep_their_own_builds():
    a = build_stage()
    effects.build(a)
    lights_a = {ob.name for ob in fx_objs()}
    b = bpy.data.scenes.new("B")
    b.render.fps = 24
    s, sa = b.afterglow, a.afterglow
    s.collection, s.sound = sa.collection, sa.sound
    s.effects.strikes.enabled, s.effects.strikes.weight = True, 1.0
    effects.build(b)                                   # building B leaves A's build alone
    assert {ob.name for ob in fx_objs()} >= lights_a
    assert len(fx_objs()) == 2 * len(lights_a)
    assert effects._collections(a) and effects._collections(b)
    s.effects.seed += 1                                # B's settings never flag A
    assert effects.outdated(b) and not effects.outdated(a)
    effects.remove(b)
    assert {ob.name for ob in fx_objs()} == lights_a   # removing B leaves A alone
    assert not effects._collections(b) and effects._collections(a)
    assert all(ob.data.users == 1 for ob in fx_objs())
    assert not [d for d in bpy.data.lights if d.get(effects.TAG) and d.users == 0]
    assert not [d for d in bpy.data.actions if d.get(effects.TAG) and d.users == 0]


def test_packed_sound_builds_without_file():
    import shutil
    path = os.path.join(tempfile.gettempdir(), "afterglow_fx_packed.wav")
    shutil.copy(pulse_wav(), path)
    scene = build_stage()
    want = times_of(effects.analyze(scene)["strikes"])
    snd = bpy.data.sounds.load(path)
    snd.pack()
    os.remove(path)
    scene.afterglow.sound = snd
    assert times_of(effects.analyze(scene)["strikes"]) == want
    assert effects.build(scene)["strikes"] == len(want)


# ---------------------------------------------------------------- emitter cage

def cage_local(cage, p):
    """A world point in the cage's own unit frame."""
    return cage.matrix_world.inverted() @ Vector(p)


def test_emitter_cage_starts_as_the_fitted_venue():
    for shape in effects.SHAPES:
        helpers.fresh()
        scene = build_stage(shape=shape)
        fitted = effects.make_venue(scene)
        cage = effects.fit_cage(scene)
        fx = scene.afterglow.effects
        assert fx.emitter is cage and cage.get(effects.EMITTER_TAG)
        assert cage.hide_render and cage.show_in_front               # a viewport control
        assert tuple(cage.lock_rotation) == (True, True, False)      # it turns about Z only
        assert not cage.data.polygons and cage.data.edges            # an outline, nothing to render
        caged = effects.make_venue(scene)
        assert caged.exact and abs(caged.z0 - fitted.z0) < 1e-5 and abs(caged.z1 - fitted.z1) < 1e-5
        for a, b in zip(fitted.polygon(), caged.polygon()):           # the same footprint, exactly
            assert (a - b).length < 1e-4, (shape, tuple(a), tuple(b))
        for u in (0.0, 0.3, 0.71):
            assert (fitted.path_point(u) - caged.path_point(u)).length < 0.05, (shape, u)


def test_emitter_cage_moves_turns_and_scales_the_venue():
    scene = build_stage(shape="CIRCLE")
    cage = effects.fit_cage(scene)
    cage.location = (3.0, -2.0, 0.5)
    cage.rotation_euler.z = 0.6
    cage.scale = (7.0, 2.5, 5.0)                                     # an ellipse, 5 m tall
    bpy.context.view_layer.update()
    v = effects.make_venue(scene)
    assert abs(v.z0 - 0.5) < 1e-5 and abs(v.z1 - 5.5) < 1e-5
    rng = random.Random(4)
    for _ in range(200):                                             # spawns fill the cage's ellipse
        q = cage_local(cage, v.footprint(rng))
        assert q.x * q.x + q.y * q.y <= 1.0 + 1e-6 and abs(q.z) < 1e-5, tuple(q)
    for i in range(40):                                              # trails ride its path ring
        q = cage_local(cage, v.path_point(i / 40))
        assert abs(math.hypot(q.x, q.y) - 1.0) < 0.01 and abs(q.z - effects.PATH_Z) < 1e-5, tuple(q)
    enable(scene, "trails")                                          # and so do the built lights
    effects.build(scene)
    light = next(ob for ob in fx_objs("trails") if ob.type == "LIGHT")
    for f in range(20, 90, 7):
        scene.frame_set(f)
        if light.data.energy > 1.0:
            q = cage_local(cage, light.matrix_world.translation)
            assert abs(math.hypot(q.x, q.y) - 1.0) < 0.02 and abs(q.z - effects.PATH_Z) < 1e-4, (f, tuple(q))


def test_emitter_cage_lifecycle():
    scene = build_stage(shape="SQUARE")
    cage = effects.fit_cage(scene)
    fx = scene.afterglow.effects
    assert len(cage.data.vertices) == 3 * 4                         # floor, top and path rings
    fx.venue_shape = "CIRCLE"                                        # Shape redraws the cage
    assert len(cage.data.vertices) == 3 * effects.CIRCLE_SEG
    fx.venue_shape = "TRIANGLE"
    assert len(cage.data.vertices) == 3 * 3
    effects.build(scene)
    assert not effects.outdated(scene)
    cage.location.x += 1.0                                           # moving the cage is an edit
    bpy.context.view_layer.update()
    assert effects.outdated(scene)
    assert bpy.ops.afterglow.remove_effects() == {"FINISHED"}
    assert bpy.data.objects.get(cage.name) is cage                  # Build/Remove never touch it
    assert bpy.ops.afterglow.fit_emitter() == {"FINISHED"}           # snap back, same cage
    assert [o for o in bpy.data.objects if o.get(effects.EMITTER_TAG)] == [cage]
    assert abs(cage.location.x) < 1e-4
    bpy.data.objects.remove(cage)                                    # deleted: fitted venue again
    assert fx.emitter is None and not effects.make_venue(scene).exact




# ---------------------------------------------------------------- strike points

SPOTS = ((3.0, -2.0), (-4.0, 1.5), (0.5, 5.0))


def place_points(scene, spots=SPOTS):
    for x, y in spots:
        scene.cursor.location = (x, y, 7.0)                        # cursor height is ignored
        assert bpy.ops.afterglow.add_strike_point() == {"FINISHED"}
    return [p for p in effects.strike_points(scene)]


def strike_at_onsets(scene):
    """XY of the strike light lit at each strike onset, in time order."""
    fps = scene.render.fps / scene.render.fps_base
    out = []
    for t, _ in effects.analyze(scene)["strikes"]:
        scene.frame_set(int(scene.afterglow.start_frame + t * fps) + 2)
        lit = [ob for ob in fx_objs("strikes") if ob.type == "LIGHT" and ob.data.energy > 1.0]
        assert len(lit) == 1, (t, [o.name for o in lit])
        out.append(tuple(round(c, 4) for c in lit[0].matrix_world.translation.xy))
    return out


def test_add_strike_point_marks_where_a_strike_lands():
    scene = build_stage()
    scene.cursor.location = (3.0, -2.0, 7.0)
    assert bpy.ops.afterglow.add_strike_point() == {"FINISHED"}
    fx = scene.afterglow.effects
    v = effects.make_venue(scene)
    ob = bpy.context.view_layer.objects.active                       # ready to grab
    assert ob.get(effects.POINT_TAG) and ob.type == "EMPTY" and ob.select_get()
    assert ob.empty_display_type == "SINGLE_ARROW" and abs(ob.empty_display_size - v.span_z) < 1e-5
    assert abs(ob.location.x - 3.0) < 1e-6 and abs(ob.location.y + 2.0) < 1e-6
    assert abs(ob.location.z - v.z0) < 1e-6                          # on the venue floor
    assert fx.strike_placement == "POINTS" and ob.name in fx.strike_points.objects
    assert fx.strike_points.name in scene.collection.children


def test_strikes_land_on_points_in_sequence():
    scene = build_stage()
    pts = place_points(scene)
    effects.build(scene)
    got = strike_at_onsets(scene)
    want = [tuple(round(c, 4) for c in pts[i % len(pts)]) for i in range(len(got))]
    assert len(got) >= 4 and got == want, (got, want)                # one after another, in name order
    scene.afterglow.effects.beams = True                             # beams strike there too
    effects.build(scene)
    for sh in [s for s in shaft_objs() if s.name.startswith("AFX_strikes")]:
        assert sh.parent.type == "LIGHT"                             # riding the strike's light


def test_strike_points_random_order_edits_and_errors():
    scene = build_stage()
    pts = {tuple(round(c, 4) for c in p) for p in place_points(scene)}
    fx = scene.afterglow.effects
    fx.strike_order = "RANDOM"
    effects.build(scene)
    first = strike_at_onsets(scene)
    assert set(first) <= pts and len(set(first)) > 1, first          # always one of the points
    effects.build(scene)
    assert strike_at_onsets(scene) == first                          # the same every build
    assert not effects.outdated(scene)
    pt = sorted(fx.strike_points.objects, key=lambda o: o.name)[0]
    pt.location.x += 1.0                                             # moving a point is an edit
    bpy.context.view_layer.update()
    assert effects.outdated(scene)
    effects.remove(scene)
    assert bpy.data.objects.get(pt.name) is pt                       # Remove Effects keeps the points
    effects.build(scene)
    built = [o.name for o in fx_objs()]
    for ob in list(fx.strike_points.objects):                        # no points: a clear error,
        bpy.data.objects.remove(ob)
    try:
        effects.build(scene)
    except ValueError as e:
        assert "strike point" in str(e), str(e)
    else:
        raise AssertionError("built without strike points")
    assert [o.name for o in fx_objs()] == built                      # and the last build is kept
    fx.strike_placement = "RANDOM"                                   # Random needs none
    effects.build(scene)


# ---------------------------------------------------------------- slice 5: operators & UI

def test_operators_registered():
    assert hasattr(bpy.ops.afterglow, "build_effects")
    assert hasattr(bpy.ops.afterglow, "remove_effects")


def test_build_operator_and_outdated_flag():
    scene = build_stage()
    assert bpy.ops.afterglow.build_effects() == {"FINISHED"}
    assert not effects.outdated(scene)
    scene.afterglow.effects.strikes.timing = 2.5      # settings moved: the build is stale
    assert effects.outdated(scene)
    assert bpy.ops.afterglow.build_effects() == {"FINISHED"}
    assert not effects.outdated(scene)
    assert bpy.ops.afterglow.remove_effects() == {"FINISHED"}
    assert not [c for c in bpy.data.collections if c.get(effects.TAG)]


def shaft_objs():
    return [ob for ob in bpy.data.objects if ob.get(effects.TAG) and ob.type == "MESH"]


def beams_stage(sound_path=None):
    scene = build_stage(sound_path=sound_path)
    fx = scene.afterglow.effects
    enable(scene, "gun", "blooms")
    fx.beams = True
    return scene


def test_visible_beams_give_strikes_and_guns_a_shaft():
    scene = beams_stage()
    effects.build(scene)
    lights = {ob.name: ob for ob in fx_objs() if ob.type == "LIGHT"}
    shafts = shaft_objs()
    assert len(shafts) == effects.POOL["strikes"] + effects.POOL["gun"]  # blooms get none
    mats = set()
    for sh in shafts:
        layer = sh.name.split("_")[1]
        assert layer in effects.BEAM_LAYERS
        assert sh.parent is lights[sh.name.replace("_shaft", "_light")]   # follows its light
        if layer == "gun":                                              # the tracer's spot fits it
            assert abs(sh.parent.data.spot_size - effects.BEAM_SPOT_SIZE["gun"]) < 1e-6
        else:                                                           # a strike glows from its base
            assert sh.parent.data.type == "POINT"
        assert sh.data.get(effects.TAG) and sh.users_collection[0] is effects._collections(scene)[0]
        assert sh.display_type == "WIRE" and sh.hide_select             # never hides or blocks the venue
        assert not (sh.visible_shadow or sh.visible_diffuse)            # a glow, not a lamp
        assert all(p.use_smooth for p in sh.data.polygons)
        want = ("filament", "sleeve", "beads") if layer == "strikes" else ("tracer",)
        assert [m.name.rsplit("_", 1)[1] for m in sh.data.materials] == list(want)
        assert {p.material_index for p in sh.data.polygons} == set(range(len(want)))
        for mat in sh.data.materials:
            mats.add(mat.name)
            nodes = {n.bl_idname for n in mat.node_tree.nodes}
            assert {"ShaderNodeEmission", "ShaderNodeBsdfTransparent", "ShaderNodeAddShader"} <= nodes
            attrs = {(n.attribute_type, n.attribute_name) for n in mat.node_tree.nodes
                     if n.bl_idname == "ShaderNodeAttribute"}
            assert ("OBJECT", effects.GLOW) in attrs
            assert ("ShaderNodeObjectInfo" in nodes) == (layer == "strikes")  # dashes differ per strike
    assert len(mats) == 4                                              # shared per layer and part
    for ob in lights.values():                                         # other layers stay lights only
        if ob.name.split("_")[1] not in effects.BEAM_LAYERS:
            assert not [c for c in ob.children]


def test_strike_shaft_reaches_the_floor():
    scene = beams_stage()
    effects.build(scene)
    v = effects.make_venue(scene)
    sh = next(s for s in shaft_objs() if s.name.startswith("AFX_strikes"))
    for f in range(20, 60):
        scene.frame_set(f)
        if sh[effects.GLOW] > 0.1:
            break
    zs = [(sh.matrix_world @ vtx.co).z for vtx in sh.data.vertices]
    assert abs(max(zs) - v.z1) < 1e-3 and abs(min(zs) - v.z0) < 1e-3, (min(zs), max(zs), v.z0, v.z1)
    light_z = sh.parent.matrix_world.translation.z                     # lit from low down, near its base
    assert abs(light_z - (v.z0 + effects._strike_base(v))) < 1e-3, light_z


def companions_of(sh):
    """[(centre xy, z extent)] of a Strike shaft's companion tubes (one per 2 * SEG verts)."""
    me = sh.data
    vs = sorted({vi for p in me.polygons if p.material_index == 2 for vi in p.vertices})
    n = 2 * effects.SHAFT_SEGMENTS
    out = []
    for k in range(0, len(vs), n):
        pts = [me.vertices[i].co for i in vs[k:k + n]]
        c = sum(pts, Vector()) / len(pts)
        out.append((c.xy, max(p.z for p in pts) - min(p.z for p in pts)))
    return out


def test_strike_companion_covers_part_of_the_height():
    scene = beams_stage()
    effects.build(scene)
    v = effects.make_venue(scene)
    sides = set()
    for sh in [s for s in shaft_objs() if s.name.startswith("AFX_strikes")]:
        (comp,) = companions_of(sh)                                  # exactly one companion
        centre, ext = comp
        assert abs(centre.length - effects.COMPANION_RING) < 1e-3, centre.length
        lo, hi = effects.COMPANION_SPAN                              # only part of the height
        assert lo * v.span_z - 1e-3 <= ext <= hi * v.span_z + 1e-3, (ext, v.span_z)
        sides.add(round(math.atan2(centre.y, centre.x), 3))
    assert len(sides) == effects.POOL["strikes"]                       # sides differ per light


def strike_material(kind):
    return next(m for m in bpy.data.materials if m.get(effects.TAG) and m.name.endswith("strikes_" + kind))


def test_beam_direction_and_noise_controls():
    scene = beams_stage()
    fx = scene.afterglow.effects
    effects.build(scene)
    for prop, value in (("beam_direction", "UP"), ("beam_gaps", 0.8), ("beam_dash", 2.0)):
        setattr(fx, prop, value)
        assert effects.outdated(scene), prop                         # every control is an edit
    effects.build(scene)
    fps = scene.render.fps / scene.render.fps_base
    for kind in ("filament", "sleeve", "beads"):                     # Up: the pattern streams up
        (fc,) = effects._fcurves_of(strike_material(kind).node_tree)
        assert abs(fc.evaluate(11) - fc.evaluate(10) + effects.FLOW_SPEED / fps) < 1e-4, kind
    cut = effects.GAP_CUT[0] + (effects.GAP_CUT[1] - effects.GAP_CUT[0]) * 0.8
    for kind, want_cut, per_metre in (("filament", cut, 1 / 2.0),
                                      ("beads", cut + effects.BEAD_EXTRA_CUT, effects.BEAD_SCALE / 2.0)):
        nt = strike_material(kind).node_tree
        step = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeMapRange")
        assert abs(step.inputs["From Min"].default_value - want_cut) < 1e-5, kind  # Gaps
        line = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeCombineXYZ" and n.inputs["Z"].is_linked)
        scale = line.inputs["Z"].links[0].from_node
        assert abs(scale.inputs[1].default_value - per_metre) < 1e-5, kind   # Dash Size
    fx.beam_flow = 0.0                                               # still: no flow either way
    effects.build(scene)
    assert "Flow" not in strike_material("filament").node_tree.nodes


def test_emitter_cage_keeps_your_changes():
    """Applied scale, Edit Mode resizing and Shape changes keep the cage's venue; Build,
    Remove Effects, Apply and Remove never delete or reset it."""
    from mathutils import Matrix
    scene = build_stage(shape="RECT")
    cage = effects.fit_cage(scene)
    cage.location = (2.0, 1.0, 0.0)
    cage.rotation_euler.z = 0.4
    bpy.context.view_layer.update()
    before = effects.make_venue(scene)
    s = Vector(cage.scale)                                           # Ctrl+A: apply scale
    cage.data.transform(Matrix.Diagonal((s.x, s.y, s.z, 1.0)))
    cage.scale = (1.0, 1.0, 1.0)
    after = effects.make_venue(scene)
    for a, b in zip(before.polygon(), after.polygon()):
        assert (a - b).length < 1e-4, (tuple(a), tuple(b))
    assert abs(after.z1 - before.z1) < 1e-4
    for vtx in cage.data.vertices:                                    # Edit Mode: shrink it in X
        vtx.co.x *= 0.5
    half = effects.make_venue(scene)
    assert abs(half.rx - before.rx * 0.5) < 1e-4 and abs(half.ry - before.ry) < 1e-4
    scene.afterglow.effects.venue_shape = "CIRCLE"                    # Shape keeps that size
    circ = effects.make_venue(scene)
    assert abs(circ.rx - half.rx) < 1e-4 and abs(circ.ry - half.ry) < 1e-4
    assert abs(circ.z1 - half.z1) < 1e-4 and (circ.center - half.center).length < 1e-4
    keep = (tuple(cage.matrix_world.col[3]), len(cage.data.vertices), [tuple(v.co) for v in cage.data.vertices])
    enable(scene, "strikes")
    effects.build(scene)
    effects.remove(scene)
    core.apply(scene)
    core.remove(scene)
    bpy.context.view_layer.update()
    assert bpy.data.objects.get(cage.name) is cage and scene.afterglow.effects.emitter is cage
    assert (tuple(cage.matrix_world.col[3]), len(cage.data.vertices),
            [tuple(v.co) for v in cage.data.vertices]) == keep


def test_strike_dashes_flow_down():
    scene = beams_stage()
    fx = scene.afterglow.effects
    fx.beam_flow = 2.0
    effects.build(scene)
    v = effects.make_venue(scene)
    fps = scene.render.fps / scene.render.fps_base
    want = effects.FLOW_SPEED * 2.0 / fps                          # metres per frame
    mats = {m.name.rsplit("_", 2)[1] + "_" + m.name.rsplit("_", 1)[1]: m for m in bpy.data.materials
            if m.get(effects.TAG)}
    for kind in ("strikes_filament", "strikes_sleeve", "strikes_beads"):
        nt = mats[kind].node_tree
        flow = nt.nodes["Flow"].outputs[0]
        assert flow.links[0].to_node.bl_idname == "ShaderNodeMath"   # offsets the dash height
        (fc,) = effects._fcurves_of(nt)
        assert fc.extrapolation == "LINEAR" and all(k.interpolation == "LINEAR" for k in fc.keyframe_points)
        for f in (10, 100, 400):                                     # a steady rise, past the keys too
            assert abs(fc.evaluate(f + 1) - fc.evaluate(f) - want) < 1e-4, (kind, f)
    assert "Flow" not in mats["gun_tracer"].node_tree.nodes        # the tracer has no dashes
    fx.beam_flow = 0.0
    assert effects.outdated(scene)
    effects.build(scene)
    assert not [m for m in bpy.data.materials if m.get(effects.TAG) and "Flow" in m.node_tree.nodes]


def beam_measures(scene):
    """(core radius, sleeve radius, companion distance, light height above the floor,
    dash flow per frame) of the first Strike beam, all in metres."""
    effects.build(scene)
    v = effects.make_venue(scene)
    sh = sorted((s for s in shaft_objs() if s.name.startswith("AFX_strikes")), key=lambda s: s.name)[0]
    me = sh.data

    def ring(slot):
        vs = {vi for p in me.polygons if p.material_index == slot for vi in p.vertices}
        pts = [me.vertices[i].co for i in vs]
        c = sum(pts, Vector()) / len(pts)
        return c, max((p.xy - c.xy).length for p in pts)

    (c0, core), (_, sleeve), (c2, _) = ring(0), ring(1), ring(2)
    for f in range(20, 80):
        scene.frame_set(f)
        if sh[effects.GLOW] > 0.1:
            break
    flow = next(m for m in bpy.data.materials if m.name.endswith("strikes_filament")).node_tree
    (fc,) = effects._fcurves_of(flow)
    return (core, sleeve, (c2.xy - c0.xy).length,
            sh.parent.matrix_world.translation.z - v.z0, fc.evaluate(11) - fc.evaluate(10))


def test_beam_size_ignores_venue_height():
    """Raising the venue (Height, or the cage's Z scale) makes beams taller, never thicker:
    the core, glow, companion distance, light height and dash speed stay the same."""
    scene = beams_stage()
    fx = scene.afterglow.effects
    fx.height = 6.0
    low = beam_measures(scene)
    fx.height = 48.0
    high = beam_measures(scene)
    for a, b in zip(low, high):
        assert abs(a - b) < 1e-4, (low, high)
    cage = effects.fit_cage(scene)
    cage.scale.z *= 3.0
    caged = beam_measures(scene)
    for a, b in zip(low, caged):
        assert abs(a - b) < 1e-4, (low, caged)
    assert abs(low[0] - effects.FILAMENT_RADIUS) < 1e-4 and abs(low[1] - effects.SLEEVE_RADIUS) < 1e-4


def walk_curve(sh):
    return next((fc for fc in helpers.fcurves(sh) if fc.data_path == effects.WALK_PATH), None)


def onsets(sh):
    """Frames where a strike light flashes on: its energy keys at 0 followed by a rise."""
    energy = next(fc for fc in helpers.fcurves(sh.parent.data) if fc.data_path == "energy")
    k = [(kp.co.x, kp.co.y) for kp in energy.keyframe_points]
    return [f for (f, v), (_, nxt) in zip(k, k[1:]) if v == 0.0 and nxt > 1e-6]


def walk_events(sh):
    """[(head starts, head across, tail starts, tail gone)] frames of a shaft's walks
    (none for a light that never fired)."""
    fc = walk_curve(sh)
    pts = [(kp.co.x, kp.co.y) for kp in fc.keyframe_points] if fc else []
    return [tuple(p[0] for p in pts[i:i + 4]) for i in range(0, len(pts), 4)]


def test_strike_beam_walks_in_and_lands_on_the_beat():
    scene = beams_stage()
    fx = scene.afterglow.effects
    assert fx.beam_walk == "DOWN"                                    # on by default: no sudden pop
    effects.build(scene)
    fps = scene.render.fps / scene.render.fps_base
    dur = effects.DUR["strikes"] * fx.strikes.timing
    walk = fx.beam_walk_time * fps                                   # Walk Time is in seconds
    t0 = effects.analyze(scene)["strikes"][0][0]                    # the first onset
    sh = sorted((s for s in shaft_objs() if s.name.startswith("AFX_strikes")), key=lambda s: s.name)[0]
    pts = [(kp.co.x, kp.co.y) for kp in walk_curve(sh).keyframe_points]
    assert [round(w, 6) for _, w in pts[:4]] == [0.0, round(effects.WALK_FULL, 6),
                                                 round(effects.WALK_FULL, 6), round(effects.WALK_GONE, 6)]
    head0, land, tail0, gone = (p[0] for p in pts[:4])
    onset = scene.afterglow.start_frame + t0 * fps
    assert abs(land - onset) < 1e-3, (land, onset)                   # the head lands on the beat,
    assert abs(land - head0 - walk) < 1e-3                           # after walking Walk Time,
    assert abs(tail0 - onset - effects.HOLD["strikes"] * dur * fps) < 1e-3   # holds,
    assert abs(gone - tail0 - walk) < 1e-3                           # and its tail takes as long
    for sh in [s for s in shaft_objs() if s.name.startswith("AFX_strikes")]:
        flashes = onsets(sh)                                         # every strike, every event:
        for _, land, _, _ in walk_events(sh):                        # the light flashes as it lands
            assert min(abs(land - f) for f in flashes) < 1e-3, (sh.name, land)
    for kind in ("filament", "sleeve", "beads"):
        attrs = {n.attribute_name for n in strike_material(kind).node_tree.nodes
                 if n.bl_idname == "ShaderNodeAttribute"}
        assert effects.WALK in attrs, kind
    gun = next(m for m in bpy.data.materials if m.get(effects.TAG) and m.name.endswith("gun_tracer"))
    assert effects.WALK not in {n.attribute_name for n in gun.node_tree.nodes if n.bl_idname == "ShaderNodeAttribute"}


def test_walk_time_is_seconds_not_strike_length():
    scene = beams_stage()
    fx = scene.afterglow.effects
    fx.beam_walk_time = 0.6
    lengths = set()
    for timing in (0.5, 2.0):                                        # a short and a long strike
        fx.strikes.timing = timing
        effects.build(scene)
        sh = sorted((s for s in shaft_objs() if s.name.startswith("AFX_strikes")), key=lambda s: s.name)[0]
        head0, land, _, _ = walk_events(sh)[0]
        lengths.add(round(land - head0, 3))
    fps = scene.render.fps / scene.render.fps_base
    assert lengths == {round(0.6 * fps, 3)}, lengths


def test_strike_walk_up_and_off():
    scene = beams_stage()
    fx = scene.afterglow.effects
    effects.build(scene)
    for prop, value in (("beam_walk", "UP"), ("beam_walk_time", 0.2)):
        setattr(fx, prop, value)
        assert effects.outdated(scene), prop
    effects.build(scene)
    sh = sorted((s for s in shaft_objs() if s.name.startswith("AFX_strikes")), key=lambda s: s.name)[0]
    flashes = onsets(sh)
    for head0, _, _, _ in walk_events(sh):                           # walking Up, it leaves on the beat
        assert min(abs(head0 - f) for f in flashes) < 1e-3, head0
    fx.beam_walk = "OFF"
    effects.build(scene)
    sh = sorted((s for s in shaft_objs() if s.name.startswith("AFX_strikes")), key=lambda s: s.name)[0]
    assert walk_curve(sh) is None and sh[effects.WALK] == effects.WALK_FULL  # all at once, as before
    assert effects.WALK not in {n.attribute_name for n in strike_material("filament").node_tree.nodes
                                if n.bl_idname == "ShaderNodeAttribute"}


def test_shaft_glow_follows_its_light():
    """The glow is keyed with the light's own envelope, through busy-pool handovers too."""
    scene = beams_stage(dense_wav())
    scene.afterglow.effects.strikes.timing = 4.0          # more overlap than strike slots
    scene.afterglow.effects.beam_walk = "OFF"             # a walking beam glows for its whole walk
    effects.build(scene)
    pairs = [(sh, sh.parent) for sh in shaft_objs()]
    lit = 0
    for f in range(1, 170, 2):
        scene.frame_set(f)
        for sh, light in pairs:
            layer = sh.name.split("_")[1]
            scale = effects.STRIKE_BEAM_ENERGY if layer == "strikes" else 1.0
            want = light.data.energy / (effects.ENERGY[layer] * scale)
            assert abs(sh[effects.GLOW] - want) < 1e-3, (sh.name, f, sh[effects.GLOW], want)
            lit += want > 0.05
    assert lit > 20, lit


def test_visible_beams_lifecycle():
    scene = beams_stage()
    scene.afterglow.effects.beams = False
    effects.build(scene)
    assert not shaft_objs()                                          # off by default: lights only
    fx = scene.afterglow.effects
    fx.beams = True
    assert effects.outdated(scene)
    effects.build(scene)
    n = len(shaft_objs())
    effects.build(scene)
    assert len(shaft_objs()) == n                                    # rebuilds never stack shafts
    fx.beam_flow = 2.0
    assert effects.outdated(scene)
    assert bpy.ops.afterglow.remove_effects() == {"FINISHED"}
    assert not shaft_objs()
    for store in (bpy.data.meshes, bpy.data.materials, bpy.data.actions):
        assert not [d for d in store if d.get(effects.TAG)], store


def test_outdated_follows_keyframes_not_frame():
    scene = build_stage()
    path = "afterglow.effects.strikes.weight"
    scene.afterglow.effects.strikes.weight = 0.0
    scene.keyframe_insert(path, frame=1)
    scene.afterglow.effects.strikes.weight = 1.0
    scene.keyframe_insert(path, frame=100)
    effects.build(scene)
    for f in (1, 37, 100, 140):                # scrubbing an animated control is not an edit
        scene.frame_set(f)
        assert not effects.outdated(scene), f
    kp = effects._fcurve(scene, path).keyframe_points[1]
    kp.co.y = kp.handle_left.y = kp.handle_right.y = 0.5   # editing a key is
    assert effects.outdated(scene)
    effects.build(scene)
    assert not effects.outdated(scene)
    effects._fcurve(scene, path).keyframe_points[0].interpolation = "CONSTANT"
    assert effects.outdated(scene)


def test_build_operator_reports_missing_sound():
    scene = stage()
    scene.afterglow.sound = None
    try:
        bpy.ops.afterglow.build_effects()
    except RuntimeError as e:
        assert "sound" in str(e).lower(), str(e)
    else:
        raise AssertionError("expected an error report")


def test_register_roundtrip_with_effects():
    afterglow.unregister()
    assert "afterglow" not in bpy.types.Scene.bl_rna.properties
    assert not hasattr(bpy.types, "AFTERGLOW_OT_build_effects")
    afterglow.register()
    assert "effects" in bpy.types.Scene.bl_rna.properties["afterglow"].fixed_type.properties
    assert hasattr(bpy.types, "AFTERGLOW_PT_effects")


helpers.run(globals())
