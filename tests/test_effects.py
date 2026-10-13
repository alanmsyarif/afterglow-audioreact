"""Environment effects: onset analysis, venue shapes, light-only pooled builds, motion, stacking, removal."""
import math
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
from afterglow import effects  # noqa: E402

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
    want = sum(effects.POOL[n] * (effects.DOWN_LIGHTS if n == "downward" else 1)
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


def test_disabled_layers_build_nothing():
    scene = stage()
    counts = effects.build(scene)
    assert counts == {}
    assert not fx_objs()
    assert [c for c in bpy.data.collections if c.get(effects.TAG)]  # collection exists, empty


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
    assert len(objs) == effects.POOL["downward"] * effects.DOWN_LIGHTS
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
    assert n == effects.POOL["downward"] * effects.DOWN_LIGHTS       # remove is safe and total
    assert not fx_objs()
    assert not [l for l in bpy.data.lights if l.get(effects.TAG)]
    assert not [a for a in bpy.data.actions if a.get(effects.TAG)]
    assert set(bpy.data.objects) == user_objs                        # the venue survives untouched


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


# ---------------------------------------------------------------- slice 5: operators & UI

def test_operators_registered():
    assert hasattr(bpy.ops.afterglow, "build_effects")
    assert hasattr(bpy.ops.afterglow, "remove_effects")


def test_build_operator_and_outdated_flag():
    scene = build_stage()
    assert bpy.ops.afterglow.build_effects() == {"FINISHED"}
    assert not effects.outdated(scene)
    scene.afterglow.effects.strikes.intensity = 2.5   # settings moved: the build is stale
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
        want = ("filament", "sleeve") if layer == "strikes" else ("tracer",)
        assert [m.name.rsplit("_", 1)[1] for m in sh.data.materials] == list(want)
        assert {p.material_index for p in sh.data.polygons} == set(range(len(want)))
        for mat in sh.data.materials:
            mats.add(mat.name)
            nodes = {n.bl_idname for n in mat.node_tree.nodes}
            assert {"ShaderNodeEmission", "ShaderNodeBsdfTransparent", "ShaderNodeAddShader"} <= nodes
            key = next(n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeAttribute")
            assert (key.attribute_type, key.attribute_name) == ("OBJECT", effects.GLOW)
            assert ("ShaderNodeObjectInfo" in nodes) == (layer == "strikes")  # dashes differ per strike
    assert len(mats) == 3                                              # shared per layer and part
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
    assert abs(light_z - (v.z0 + effects.STRIKE_BASE * v.span_z)) < 1e-3, light_z


def test_shaft_glow_follows_its_light():
    """The glow is keyed with the light's own envelope, through busy-pool handovers too."""
    scene = beams_stage(dense_wav())
    scene.afterglow.effects.strikes.timing = 4.0          # more overlap than strike slots
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
    fx.beam_glow = 2.0
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
