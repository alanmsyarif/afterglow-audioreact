"""Environment Effects: stackable, audio-triggered light layers over the whole venue.

Six independent layers (Data Strikes, Data Blooms, Rapid Flashes, Travelling Light
Trails, Downward Trails, Gun Bursts) analyse the scene's sound once, place events
deterministically inside the shared venue bounds, and key pooled geometry plus real
lights in a tagged, removable collection. Everything is world-space over the venue,
never normalised per object, and silent audio produces no events.
"""
import math
import random
from bisect import bisect_right

import numpy as np
import bpy
from mathutils import Quaternion, Vector
from bpy_extras import anim_utils

from . import bake, core, tempo

TAG = "afterglow_effects"
SIG = TAG + "_sig"
COLLECTION = "Afterglow Effects"
PREFIX = "AFX"
VERSION = 1
MIN_GAP = 0.18       # s between two events of one layer
ONSET_REL = 0.3      # a run must exceed this fraction of the band's p95 to count
SNAP_MIN_EVENTS = 8  # shorter streams stay raw: too sparse to trust with a grid
SNAP_MAX_RMS = 0.15  # grid residual RMS, as a fraction of the period, that still counts as a pulse
SNAP_TOL = 0.30      # an event never moves more than this fraction of a period
SNAP_HARM = 0.20     # log2 slack reconciling the grid with the tempo curve (half/double time)
SNAP_LO_BPM, SNAP_HI_BPM = 40.0, 240.0  # pulse period search bounds

SHAPES = ("CIRCLE", "SQUARE", "RECT", "TRIANGLE")
LAYERS = ("strikes", "blooms", "flashes", "trails", "downward", "gun")
POOL = {"strikes": 6, "blooms": 5, "flashes": 2, "trails": 3, "downward": 6, "gun": 3}
BANDS = {"LOW": (20.0, 150.0), "MID": (150.0, 2000.0),
         "HIGH": (2000.0, 16000.0), "FULL": (20.0, 16000.0)}
BAND_ITEMS = (
    ("LOW", "Low (20-150 Hz)", "Kicks and bass"),
    ("MID", "Mid (150-2k Hz)", "Melody and voices"),
    ("HIGH", "High (2k-16k Hz)", "Hats and air"),
    ("FULL", "Full range", "Everything at once"),
)


def _fcurve(scene, path):
    ad = scene.animation_data
    if not (ad and ad.action):
        return None
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    return bag and next((fc for fc in bag.fcurves if fc.data_path == path), None)


def sample_prop(scene, path, default, frame):
    """Layer controls are keyframeable: sample the scene F-curve when there is one."""
    fc = _fcurve(scene, path)
    return float(fc.evaluate(frame)) if fc else default


def _onsets(times, amp):
    """One event per contiguous run above ONSET_REL*p95, timed at the run's peak.
    Digital silence (p95 ~ 0) yields nothing; there are no fixed-time fake beats."""
    if not len(amp):
        return []
    p95 = float(np.percentile(amp, 95))
    if p95 <= 1e-6:
        return []
    above = amp > ONSET_REL * p95
    events, last, i, n = [], -1e9, 0, len(amp)
    while i < n:
        if not above[i]:
            i += 1
            continue
        j = i
        while j < n and above[j]:
            j += 1
        k = i + int(np.argmax(amp[i:j]))
        if times[k] - last >= MIN_GAP:
            events.append((float(times[k]), min(float(amp[k]) / p95, 2.0)))
            last = float(times[k])
        i = j
    return events


def _grid_fit(ts):
    """(period, anchor) of the coarsest pulse grid the times already follow, or None.

    Candidate periods are scored by how tightly the times cluster on them (circular
    residual RMS), the coarsest trustworthy one wins, and a least-squares fit over the
    integer beat numbers refines it. Music that does not pulse fits no grid and is
    left exactly as the onsets timed it.
    """
    t = np.asarray(ts, float)
    p_lo = 60.0 / SNAP_HI_BPM
    p_hi = min(60.0 / SNAP_LO_BPM, (t[-1] - t[0]) / 3.0)  # at least ~3 cycles of evidence
    if p_hi <= p_lo * 1.002:
        return None
    cand = p_lo * 1.002 ** np.arange(int(math.log(p_hi / p_lo, 1.002)) + 1)
    r = np.mod(t[None, :] - t[0], cand[:, None]) / cand[:, None]
    ang = 2 * math.pi * r
    phi = np.arctan2(np.sin(ang).mean(axis=1), np.cos(ang).mean(axis=1)) / (2 * math.pi)
    w = np.mod(r - phi[:, None] + 0.5, 1.0) - 0.5
    rms = np.sqrt(np.square(w).mean(axis=1))
    ok = np.nonzero(rms <= SNAP_MAX_RMS)[0]
    if not len(ok):
        return None
    p = float(cand[ok[-1]])
    a = float(t[0] + p * phi[ok[-1]])
    k = np.round((t - a) / p)
    fit = np.linalg.lstsq(np.vstack([k, np.ones_like(k)]).T, t, rcond=None)[0]
    p, a = float(fit[0]), float(fit[1])
    res = t - (a + p * k)
    if np.sqrt(np.square(res).mean()) > SNAP_MAX_RMS * p or np.abs(res).max() > SNAP_TOL * p:
        return None
    return p, a


def _tempo_snap(events, mono, rate, curve_box):
    """Snap event times onto the pulse grid they trace, bounded by SNAP_TOL per event.

    The grid comes from the onsets themselves; the tempo curve only arbitrates. Its
    period must match the grid's within two octaves at a near-exact 1:1, 1:2, 1:4...
    ratio (the detector can report half/double time), and the curve is analysed at
    most once per run, lazily, through curve_box. Sparse or ambiguous streams keep
    their raw onset timing.
    """
    if len(events) < SNAP_MIN_EVENTS:
        return events
    grid = _grid_fit([t for t, _ in events])
    if grid is None:
        return events
    if not curve_box:
        curve_box.append(tempo.tempo_curve(mono, rate))
    p, a = grid
    tc = float(np.median([60.0 / b for _, b in curve_box[0]]))
    m = math.log2(tc / p)
    if abs(m - round(m)) > SNAP_HARM or abs(round(m)) > 2:
        return events  # grid contradicts the detector beyond an octave: trust the onsets
    out = []
    for t, s in events:
        g = a + p * round((t - a) / p)
        out.append(((g if abs(t - g) <= SNAP_TOL * p else t), s))
    return out


def analyze(scene):
    """{layer: [(seconds, strength)]} for every enabled layer, from the scene's sound.

    Each layer listens to its own trigger band; event placement is deterministic for a
    given (sound, seed, settings). Weight is sampled per event from its scene F-curve, so
    a weight keyframed 0 -> 1 lets a layer join as the track builds, and the random draw
    happens for every candidate regardless, keeping the stream stable as weight changes.
    Onset times are snapped to the detected pulse grid where that grid is reliable,
    before weight thinning, so every layer of one band shares exactly one event stream.
    """
    fx = scene.afterglow.effects
    out = {name: [] for name in LAYERS}
    enabled = [n for n in LAYERS if getattr(fx, n).enabled]
    if not enabled:
        return out
    mono, rate = bake._load(scene)
    fps = scene.render.fps / scene.render.fps_base
    start = scene.afterglow.start_frame
    cache, curve_box = {}, []  # band -> snapped onsets; curve_box: tempo curve, at most once
    for name in enabled:
        layer = getattr(fx, name)
        band = BANDS[layer.trigger_band]
        if band not in cache:
            times, series = bake.band_series(mono, rate, [band])
            cache[band] = _tempo_snap(_onsets(times, series[0]), mono, rate, curve_box)
        rng = random.Random(f"{fx.seed}:{name}")
        ev = []
        for t, s in cache[band]:
            w = sample_prop(scene, f"afterglow.effects.{name}.weight", layer.weight, start + t * fps)
            keep = rng.random()  # always drawn, so weight edits never shift later events
            if keep < min(max(w, 0.0), 1.0):
                ev.append((t, s))
        out[name] = ev
    return out


PATH_Z = 0.72  # travelling height, as a fraction of the venue's z span
CIRCLE_SEG = 64


class Venue:
    """The shared world-space bounds every layer emits in. Paths and footprints are
    derived from ONE venue frame fitted to the collection, never per-object bounds,
    so motion stays continuous across the whole scene."""

    def __init__(self, shape, cx, cy, rx, ry, z0, z1):
        self.shape = shape
        self.center = Vector((cx, cy))
        self.center3 = Vector((cx, cy, 0.0))
        self.rx, self.ry = rx, ry
        self.z0, self.z1 = z0, z1
        self.path_z = z0 + PATH_Z * (z1 - z0)
        self._cum = None

    @property
    def radius(self):
        return max(self.rx, self.ry)

    @property
    def span_z(self):
        return self.z1 - self.z0

    def polygon(self):
        """Closed CCW footprint polygon in world XY (the circle samples CIRCLE_SEG edges)."""
        if self.shape == "CIRCLE":
            return [Vector((self.center.x + self.radius * math.cos(a),
                            self.center.y + self.radius * math.sin(a)))
                    for a in (2 * math.pi * i / CIRCLE_SEG for i in range(CIRCLE_SEG))]
        if self.shape == "TRIANGLE":
            rt = 1.25 * math.hypot(self.rx, self.ry)
            return [Vector((self.center.x + rt * math.cos(a), self.center.y + rt * math.sin(a)))
                    for a in (math.pi / 2, math.pi * 7 / 6, math.pi * 11 / 6)]
        r = self.radius if self.shape == "SQUARE" else None
        hx = self.rx if r is None else r
        hy = self.ry if r is None else r
        cx, cy = self.center
        return [Vector((cx + hx, cy - hy)), Vector((cx + hx, cy + hy)),
                Vector((cx - hx, cy + hy)), Vector((cx - hx, cy - hy))]

    def _arclen(self):
        if self._cum is None:
            poly = self.polygon()
            cum = [0.0]
            for i in range(len(poly)):
                cum.append(cum[-1] + (poly[(i + 1) % len(poly)] - poly[i]).length)
            self._cum = (poly, cum)
        return self._cum

    def path_point(self, u):
        """World position on the closed emitter path at normalised arc length u."""
        u %= 1.0
        if self.shape == "CIRCLE":
            a = 2 * math.pi * u
            return Vector((self.center.x + self.radius * math.cos(a),
                           self.center.y + self.radius * math.sin(a), self.path_z))
        poly, cum = self._arclen()
        d = u * cum[-1]
        i = min(bisect_right(cum, d) - 1, len(poly) - 1)
        seg = cum[i + 1] - cum[i]
        f = (d - cum[i]) / seg if seg > 1e-9 else 0.0
        p = poly[i].lerp(poly[(i + 1) % len(poly)], f)
        return Vector((p.x, p.y, self.path_z))

    def path_tangent(self, u):
        """Unit travel direction of the path at u (CCW)."""
        u %= 1.0
        if self.shape == "CIRCLE":
            a = 2 * math.pi * u
            return Vector((-math.sin(a), math.cos(a), 0.0))
        poly, cum = self._arclen()
        i = min(bisect_right(cum, u * cum[-1]) - 1, len(poly) - 1)
        d = poly[(i + 1) % len(poly)] - poly[i]
        return Vector((d.x, d.y, 0.0)).normalized()

    def footprint(self, rng):
        """A deterministic random world point inside the venue shape, at ground level."""
        cx, cy = self.center
        if self.shape == "CIRCLE":
            r = self.radius * math.sqrt(rng.random())
            a = 2 * math.pi * rng.random()
            return Vector((cx + r * math.cos(a), cy + r * math.sin(a), self.z0))
        if self.shape == "TRIANGLE":
            tri = self.polygon()
            u, v = rng.random(), rng.random()
            if u + v > 1.0:
                u, v = 1.0 - u, 1.0 - v
            p = tri[0] + (tri[1] - tri[0]) * u + (tri[2] - tri[0]) * v
            return Vector((p.x, p.y, self.z0))
        hx = self.radius if self.shape == "SQUARE" else self.rx
        hy = self.radius if self.shape == "SQUARE" else self.ry
        return Vector((cx + rng.uniform(-hx, hx), cy + rng.uniform(-hy, hy), self.z0))


def make_venue(scene):
    """Fit the venue to the world bounds of every object in the scene's collection."""
    s = scene.afterglow
    if s.collection is None:
        raise ValueError("Pick a collection first")
    objs = core.targets(s.collection)
    if not objs:
        raise ValueError("The collection has no mesh, curve or text objects")
    fx = s.effects
    bpy.context.view_layer.update()  # matrix_world is stale for objects moved since the last eval
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    for ob in objs:
        for c in ob.bound_box:
            w = ob.matrix_world @ Vector(c)
            lo = np.minimum(lo, w)
            hi = np.maximum(hi, w)
    cx, cy = (lo[:2] + hi[:2]) / 2
    rx = max((hi[0] - lo[0]) / 2, 0.25) * fx.margin
    ry = max((hi[1] - lo[1]) / 2, 0.25) * fx.margin
    return Venue(fx.venue_shape, float(cx), float(cy), float(rx), float(ry),
                 float(lo[2]), float(hi[2]) + fx.height)


GEO = {"strikes": "streak", "blooms": "sphere", "flashes": "sphere",
       "trails": "streak", "downward": "streak", "gun": "cone"}
LIGHT_KIND = {"strikes": "SPOT", "blooms": "POINT", "flashes": "POINT",
              "trails": "POINT", "downward": "POINT", "gun": "SPOT"}
SPOT_SIZE = {"strikes": 0.5, "gun": 0.18}
ENERGY = {"strikes": 900.0, "blooms": 350.0, "flashes": 4000.0,
          "trails": 300.0, "downward": 250.0, "gun": 700.0}
EMIT_STRENGTH = 24.0     # emission strength at intensity 1, full-strength event
DUR = {"strikes": 0.45, "blooms": 1.1, "flashes": 0.22, "trails": 1.8, "downward": 0.9, "gun": 0.28}
ATTACK = {"strikes": 0.03, "blooms": 0.12, "flashes": 0.02, "trails": 0.15, "downward": 0.05, "gun": 0.015}
HOLD = {"strikes": 0.5, "blooms": 0.45, "flashes": 0.35, "trails": 0.7, "downward": 0.8, "gun": 0.85}
TAIL_S = 2.5             # keep the timeline this long past the song so tails finish
UP = Vector((0.0, 0.0, 1.0))


class _Keys:
    """Sparse keyframes collected per channel, then written once into the object's
    layered action. Channels are (struct, resolved data path, array index)."""

    def __init__(self):
        self.channels = {}

    def add(self, struct, idblock, path, index, frame, value, interp):
        ch = self.channels.get((struct, path, index))
        if ch is None:
            ch = self.channels[(struct, path, index)] = {
                "id": idblock, "full": struct.path_from_id(path), "interp": interp, "values": {}}
        ch["values"][round(float(frame), 4)] = float(value)

    def add_vec(self, struct, idblock, path, frame, values, interp):
        for i, v in enumerate(values):
            self.add(struct, idblock, path, i, frame, v, interp)

    def write(self):
        for (struct, path, index), ch in self.channels.items():
            frames = sorted(ch["values"])
            v0 = ch["values"][frames[0]]
            prop = getattr(struct, path)
            if index >= 0:
                prop[index] = v0
                struct.keyframe_insert(path, index=index, frame=frames[0])
            else:
                setattr(struct, path, v0)
                struct.keyframe_insert(path, frame=frames[0])
            fc = _own_fcurve(ch["id"], ch["full"], index)
            pts = fc.keyframe_points
            if len(frames) > 1:
                pts.add(len(frames) - 1)
            co = np.empty(2 * len(frames), np.float64)
            co[0::2] = frames
            co[1::2] = [ch["values"][f] for f in frames]
            pts.foreach_set("co", co)
            for kp in pts:
                kp.interpolation = ch["interp"]
                if ch["interp"] == "BEZIER":
                    kp.handle_left_type = kp.handle_right_type = "AUTO_CLAMPED"
            fc.update()


def _own_fcurve(idblock, path, index):
    ad = idblock.animation_data
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    want = 0 if index < 0 else index
    fc = next((f for f in bag.fcurves if f.data_path == path and f.array_index == want), None)
    if fc is None:
        raise RuntimeError(f"missing fcurve {path}[{index}] on {idblock.name}")
    return fc


def _tag_action(idblock):
    ad = idblock.animation_data
    if ad and ad.action:
        ad.action[TAG] = VERSION


def _geo_mesh(kind):
    """Unit-sized emitter geometry: streak = Z cylinder r1 d1, sphere = r1 icosphere,
    cone = base at origin, apex at +Z. Shared by every pool slot of a layer."""
    import bmesh
    from mathutils import Matrix
    name = f"{PREFIX}_geo_{kind}"
    found = next((m for m in bpy.data.meshes if m.name == name and m.get(TAG)), None)
    if found:
        return found
    bm = bmesh.new()
    if kind == "sphere":
        bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0, matrix=Matrix.Identity(4))
    elif kind == "streak":
        bmesh.ops.create_cone(bm, cap_ends=True, segments=10, radius1=1.0, radius2=1.0,
                              depth=1.0, matrix=Matrix.Identity(4))
    else:
        bmesh.ops.create_cone(bm, cap_ends=True, segments=10, radius1=1.0, radius2=0.0,
                              depth=1.0, matrix=Matrix.Identity(4))
        for v in bm.verts:  # 5.2: bmesh.ops.translate kw names are unstable; move verts directly
            v.co.z += 0.5
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me[TAG] = VERSION
    return me


def _emit_material(name, color):
    mat = bpy.data.materials.new(name)
    mat[TAG] = VERSION
    nt = mat.node_tree
    nt.nodes.clear()
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (color[0], color[1], color[2], 1.0)
    em.inputs["Strength"].default_value = 0.0
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    nt[TAG] = VERSION
    return mat, em.inputs["Strength"]


def _make_unit(coll, layer, slot, geo, color):
    """One pool slot: an emitter mesh with its own material, plus a real light sibling
    (parenting would inherit the emitter's animated scale)."""
    me = geo.copy()
    me[TAG] = VERSION
    mesh_ob = bpy.data.objects.new(f"{PREFIX}_{layer}_{slot}", me)
    mat, sock = _emit_material(f"{PREFIX}_mat_{layer}_{slot}", color)
    me.materials.append(mat)
    li = bpy.data.lights.new(f"{PREFIX}_{layer}_{slot}_light", LIGHT_KIND[layer])
    li[TAG] = VERSION
    li.color = (color[0], color[1], color[2])
    li.energy = 0.0
    li.use_shadow = False  # dozens of shadow-casting lights would crawl; spill only
    if LIGHT_KIND[layer] == "SPOT":
        li.spot_size = SPOT_SIZE[layer]
        li.spot_blend = 0.35
    light_ob = bpy.data.objects.new(f"{PREFIX}_{layer}_{slot}_light", li)
    for ob in (mesh_ob, light_ob):
        ob[TAG] = VERSION
        ob.rotation_mode = "QUATERNION"
        coll.objects.link(ob)
    return mesh_ob, light_ob, sock


def _assign(free, t0, dur):
    """First slot idle at t0, else the one freed longest ago: bounded pool, no duplicates."""
    for i, until in enumerate(free):
        if until <= t0:
            free[i] = t0 + dur
            return i
    i = min(range(len(free)), key=free.__getitem__)
    free[i] = t0 + dur
    return i


def _envelope(t0, dur, attack, hold):
    """Brightness 0 -> 1 -> 1 -> 0 in seconds; BEZIER AUTO_CLAMPED keys give the pulse shape."""
    return [(t0, 0.0), (t0 + attack, 1.0), (t0 + dur * hold, 1.0), (t0 + dur, 0.0)]


def _bright(strength, intensity):
    return max(0.0, min(strength, 2.0)) * max(0.0, intensity)


def _key_appear(keys, mesh_ob, light_ob, sock, layer, b, env, frame_of):
    """Shared appearance channels: emission strength and light energy follow the envelope."""
    for t, e in env:
        f = frame_of(t)
        keys.add(sock, mesh_ob.data.materials[0].node_tree, "default_value", -1, f,
                 EMIT_STRENGTH * b * e, "BEZIER")
        keys.add(light_ob.data, light_ob.data, "energy", -1, f, ENERGY[layer] * b * e, "BEZIER")


def _key_static_loc(keys, mesh_ob, light_ob, pos, t0, dur, frame_of, quat=None, quat_flip=None):
    for ob in (mesh_ob, light_ob):
        keys.add_vec(ob, ob, "location", frame_of(t0), pos, "LINEAR")
        keys.add_vec(ob, ob, "location", frame_of(t0 + dur), pos, "LINEAR")
    for ob, q in ((mesh_ob, quat), (light_ob, quat_flip if quat_flip is not None else quat)):
        if q is not None:
            keys.add_vec(ob, ob, "rotation_quaternion", frame_of(t0), q, "LINEAR")
            keys.add_vec(ob, ob, "rotation_quaternion", frame_of(t0 + dur), q, "LINEAR")


def _layer_keys(name, keys, unit, venue, ev, b, timing, rng, fps, start):
    """Motion + scale + brightness channels for one event of one layer."""
    mesh_ob, light_ob, sock = unit
    t0, strength = ev
    dur = DUR[name] * max(timing, 0.05)
    attack = ATTACK[name]
    frame_of = lambda t: start + t * fps  # noqa: E731
    env = _envelope(t0, dur, attack, HOLD[name])
    _key_appear(keys, mesh_ob, light_ob, sock, name, _bright(strength, b), env, frame_of)
    span = venue.span_z

    def scaled(e):  # mesh hides completely as the envelope dies
        return min(1.0, 3.0 * e)

    if name in ("strikes", "blooms", "flashes"):
        if name == "strikes":
            p = venue.footprint(rng)
            pos = Vector((p.x, p.y, (venue.z0 + venue.z1) / 2))
            r = 0.04 + 0.06 * strength
            size = lambda e: (r * e, r * e, span * scaled(e))  # noqa: E731
            light_pos = Vector((p.x, p.y, venue.z1))  # spot crowns the shaft, aimed down
        elif name == "blooms":
            p = venue.footprint(rng)
            pos = Vector((p.x, p.y, venue.z0 + 0.45 * span))
            rb = venue.radius * 0.30 * (0.5 + 0.5 * strength)
            size = lambda e: (rb * e,) * 3  # noqa: E731
            light_pos = pos
        else:
            pos = Vector((venue.center.x, venue.center.y, venue.z0 + 0.85 * span))
            rf = venue.radius * 0.12 * (0.5 + 0.5 * strength)
            size = lambda e: (rf * e,) * 3  # noqa: E731
            light_pos = pos
        _key_static_loc(keys, mesh_ob, light_ob, pos, t0, dur, frame_of)
        if name == "strikes":  # the spot lives at the shaft's crown, not its middle
            for t, _ in ((t0, 0), (t0 + dur, 0)):
                keys.add_vec(light_ob, light_ob, "location", frame_of(t), light_pos, "LINEAR")
        for t, e in env:
            keys.add_vec(mesh_ob, mesh_ob, "scale", frame_of(t), size(e), "BEZIER")
    elif name == "trails":
        u0 = rng.random()
        direction = 1.0 if rng.random() < 0.5 else -1.0
        u_span = direction * (0.25 + 0.25 * rng.random())
        samples = int(max(6, min(48, round(dur * fps))))
        length = min(3.0, 0.5 * venue.radius) * (0.6 + 0.4 * strength)
        r = 0.05 + 0.04 * strength
        for i in range(samples):
            frac = i / (samples - 1)
            p = venue.path_point(u0 + u_span * frac)
            tan = venue.path_tangent(u0 + u_span * frac) * direction
            q = UP.rotation_difference(tan)
            f = frame_of(t0 + dur * frac)
            for ob in (mesh_ob, light_ob):
                keys.add_vec(ob, ob, "location", f, p, "LINEAR")
                keys.add_vec(ob, ob, "rotation_quaternion", f, q, "LINEAR")
        for t, e in env:
            keys.add_vec(mesh_ob, mesh_ob, "scale", frame_of(t),
                         (r * min(1.0, 2.0 * e), r * min(1.0, 2.0 * e), length * scaled(e)), "BEZIER")
    elif name == "downward":
        p = venue.footprint(rng)
        ztop, zbot = venue.z1, venue.z0 + 0.02 * span
        length = min(2.0, 0.3 * span) * (0.6 + 0.4 * strength)
        r = 0.05 + 0.05 * strength
        top = Vector((p.x, p.y, ztop))
        bot = Vector((p.x, p.y, zbot))
        for f, pos in ((frame_of(t0), top), (frame_of(t0 + dur), bot)):
            for ob in (mesh_ob, light_ob):
                keys.add_vec(ob, ob, "location", f, pos, "LINEAR")
        for t, e in env:
            keys.add_vec(mesh_ob, mesh_ob, "scale", frame_of(t),
                         (r * min(1.0, 2.0 * e), r * min(1.0, 2.0 * e), length * scaled(e)), "BEZIER")
    else:  # gun: short, fast, directed rim -> centre
        muzzle = venue.path_point(rng.random())
        muzzle.z = venue.z0 + 0.55 * span
        target = Vector((venue.center.x, venue.center.y, venue.z0 + 0.30 * span))
        delta = target - muzzle
        dist = delta.length
        q = UP.rotation_difference(delta.normalized())
        q_flip = q @ Quaternion((1.0, 0.0, 0.0), math.pi)  # spots shine along -Z
        mid = muzzle.lerp(target, 0.5)
        for f, pos in ((frame_of(t0), muzzle), (frame_of(t0 + dur / 2), mid),
                       (frame_of(t0 + dur), target)):
            keys.add_vec(mesh_ob, mesh_ob, "location", f, pos, "LINEAR")
            keys.add_vec(light_ob, light_ob, "location", f, pos, "LINEAR")
        for ob, qq in ((mesh_ob, q), (light_ob, q_flip)):
            keys.add_vec(ob, ob, "rotation_quaternion", frame_of(t0), qq, "LINEAR")
            keys.add_vec(ob, ob, "rotation_quaternion", frame_of(t0 + dur), qq, "LINEAR")
        rc = 0.10 * (0.6 + 0.4 * strength)
        lc = max(0.3, min(dist * 0.8, 1.4 * (0.6 + 0.4 * strength)))
        for t, e in env:
            keys.add_vec(mesh_ob, mesh_ob, "scale", frame_of(t),
                         (rc * min(1.0, 4.0 * e), rc * min(1.0, 4.0 * e), lc * scaled(e)), "BEZIER")


def _build_layer(coll, scene, venue, name, events, fps, start, end):
    fx = scene.afterglow.effects
    layer = getattr(fx, name)
    keys = _Keys()
    units = [_make_unit(coll, name, slot, _geo_mesh(GEO[name]), tuple(layer.color))
             for slot in range(POOL[name])]
    rng = random.Random(f"{fx.seed}:{name}:place")
    free = [-1e9] * POOL[name]
    placed = 0
    for t0, strength in events:
        frame0 = start + t0 * fps
        if frame0 > end:
            break
        intensity = sample_prop(scene, f"afterglow.effects.{name}.intensity", layer.intensity, frame0)
        timing = sample_prop(scene, f"afterglow.effects.{name}.timing", layer.timing, frame0)
        dur = DUR[name] * max(timing, 0.05)
        slot = _assign(free, t0, dur)
        _layer_keys(name, keys, units[slot], venue, (t0, strength), intensity, timing, rng, fps, start)
        placed += 1
    # baseline: everything dark outside the events, so silence renders as nothing
    b0, b1 = start - 1.0, end + 1.0
    for mesh_ob, light_ob, sock in units:
        for f in (b0, b1):
            keys.add_vec(mesh_ob, mesh_ob, "scale", f, (0.0, 0.0, 0.0), "BEZIER")
            keys.add_vec(mesh_ob, mesh_ob, "rotation_quaternion", f, (1.0, 0.0, 0.0, 0.0), "LINEAR")
            keys.add(sock, mesh_ob.data.materials[0].node_tree, "default_value", -1, f, 0.0, "BEZIER")
            keys.add(light_ob.data, light_ob.data, "energy", -1, f, 0.0, "BEZIER")
        mesh_ob.location = (venue.center.x, venue.center.y, venue.z0)
        light_ob.location = (venue.center.x, venue.center.y, venue.z0)
    keys.write()
    for mesh_ob, light_ob, sock in units:
        _tag_action(mesh_ob)
        _tag_action(light_ob)
        _tag_action(light_ob.data)
        _tag_action(mesh_ob.data.materials[0].node_tree)
    return placed


def settings_sig(scene):
    """Everything a build depends on, for the panel's stale-build hint. Keyframed layer
    controls are sampled per event at build time, so their animation is not part of this."""
    s = scene.afterglow
    fx = s.effects
    vals = [fx.venue_shape, fx.seed, round(fx.height, 4), round(fx.margin, 4)]
    for name in LAYERS:
        layer = getattr(fx, name)
        vals += [layer.enabled, round(layer.weight, 4), round(layer.intensity, 4),
                 tuple(round(c, 4) for c in layer.color), round(layer.timing, 4), layer.trigger_band]
    vals += [s.sound.filepath if s.sound else "", s.start_frame,
             round(scene.render.fps / scene.render.fps_base, 4),
             s.collection.name if s.collection else ""]
    return repr(vals)


def outdated(scene):
    coll = next((c for c in bpy.data.collections if c.get(TAG)), None)
    return bool(coll) and coll.get(SIG) != settings_sig(scene)


def build(scene):
    """(Re)build the effects collection: analyse once, then key pooled geometry and real
    lights for every enabled layer. Deterministic for a given (sound, settings, seed)."""
    s = scene.afterglow
    if s.sound is None:
        raise ValueError("Pick a sound file first")
    venue = make_venue(scene)
    events = analyze(scene)
    mono, rate = bake._load(scene)
    fps = scene.render.fps / scene.render.fps_base
    start = s.start_frame
    end = start + int(np.ceil((len(mono) / rate + TAIL_S) * fps))
    remove(scene)
    coll = bpy.data.collections.new(COLLECTION)
    coll[TAG] = VERSION
    coll[SIG] = settings_sig(scene)
    scene.collection.children.link(coll)
    counts = {}
    for name in LAYERS:
        if getattr(scene.afterglow.effects, name).enabled:
            counts[name] = _build_layer(coll, scene, venue, name, events[name], fps, start, end)
    return counts


def remove(scene):
    """Delete every tagged datablock the build owns. User data is never touched."""
    for c in [c for c in bpy.data.collections if c.get(TAG)]:
        bpy.data.collections.remove(c)
    n = 0
    for ob in [o for o in bpy.data.objects if o.get(TAG)]:
        bpy.data.objects.remove(ob, do_unlink=True)
        n += 1
    for store in (bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.actions):
        for db in [d for d in store if d.get(TAG)]:
            store.remove(db)
    return n


