"""Environment Effects: stackable, audio-triggered light layers over the whole venue.

Six independent layers (Data Strikes, Data Blooms, Rapid Flashes, Travelling Light
Trails, Downward Trails, Gun Bursts) analyse the scene's sound once, place events
deterministically inside the shared venue bounds, and key pooled real lights in a
tagged, removable collection. Generated effects are lights only by default: the venue
itself is left exactly as the user built it and the visible result is real light spill
on existing objects. The opt-in Visible Beams gives each Data Strike and Gun Burst light
a glowing shaft mesh, so they read as beams in the air, identically in EEVEE and Cycles.
Everything is world-space over the venue, never normalised per object, and silent audio
produces no events.
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
VERSION = 2  # lights-only build layout; invalidates older geometry-based builds
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


def _unit_outline(shape):
    """The shape's footprint outline at unit half-extents, CCW in local XY."""
    if shape == "CIRCLE":
        return [(math.cos(a), math.sin(a)) for a in (2 * math.pi * i / CIRCLE_SEG for i in range(CIRCLE_SEG))]
    if shape == "TRIANGLE":
        return [(math.cos(a), math.sin(a)) for a in (math.pi / 2, math.pi * 7 / 6, math.pi * 11 / 6)]
    return [(1.0, -1.0), (1.0, 1.0), (-1.0, 1.0), (-1.0, -1.0)]


class Venue:
    """The shared world-space bounds every layer emits in. Paths and footprints are
    derived from ONE venue frame (fitted to the collection, or an emitter cage), never
    per-object bounds, so motion stays continuous across the whole scene.

    Fitted to a collection, Circle and Square use the larger half-extent and Triangle
    circumscribes the bounds. exact=True (an emitter cage) takes rx/ry as the shape's own
    half-extents instead, so a scaled cage gives an ellipse or rectangle, and angle turns
    the whole frame about its centre."""

    def __init__(self, shape, cx, cy, rx, ry, z0, z1, exact=False, angle=0.0):
        self.shape = shape
        self.center = Vector((cx, cy))
        self.center3 = Vector((cx, cy, 0.0))
        self.rx, self.ry = rx, ry
        self.z0, self.z1 = z0, z1
        self.exact, self.angle = exact, angle
        self._cos, self._sin = math.cos(angle), math.sin(angle)
        self.path_z = z0 + PATH_Z * (z1 - z0)
        self._cum = None

    @property
    def radius(self):
        return max(self.rx, self.ry)

    @property
    def span_z(self):
        return self.z1 - self.z0

    def half(self):
        """(hx, hy): the shape's half-extents in its own frame."""
        if self.exact or self.shape == "RECT":
            return self.rx, self.ry
        if self.shape == "TRIANGLE":
            rt = 1.25 * math.hypot(self.rx, self.ry)
            return rt, rt
        return self.radius, self.radius

    def _world(self, x, y):
        """Local frame (x, y) -> world XY: turned by angle about the centre."""
        return Vector((self.center.x + x * self._cos - y * self._sin,
                       self.center.y + x * self._sin + y * self._cos))

    def polygon(self):
        """Closed CCW footprint polygon in world XY (the circle samples CIRCLE_SEG edges)."""
        hx, hy = self.half()
        return [self._world(hx * ux, hy * uy) for ux, uy in _unit_outline(self.shape)]

    def _analytic(self):
        return self.shape == "CIRCLE" and not self.exact

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
        if self._analytic():
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
        if self._analytic():
            a = 2 * math.pi * u
            return Vector((-math.sin(a), math.cos(a), 0.0))
        poly, cum = self._arclen()
        i = min(bisect_right(cum, u * cum[-1]) - 1, len(poly) - 1)
        d = poly[(i + 1) % len(poly)] - poly[i]
        return Vector((d.x, d.y, 0.0)).normalized()

    def footprint(self, rng):
        """A deterministic random world point inside the venue shape, at ground level."""
        hx, hy = self.half()
        if self.shape == "CIRCLE":
            r = math.sqrt(rng.random())
            a = 2 * math.pi * rng.random()
            p = self._world(hx * r * math.cos(a), hy * r * math.sin(a))
        elif self.shape == "TRIANGLE":
            tri = self.polygon()
            u, v = rng.random(), rng.random()
            if u + v > 1.0:
                u, v = 1.0 - u, 1.0 - v
            p = tri[0] + (tri[1] - tri[0]) * u + (tri[2] - tri[0]) * v
        else:
            p = self._world(rng.uniform(-hx, hx), rng.uniform(-hy, hy))
        return Vector((p.x, p.y, self.z0))


def _fitted_venue(scene):
    """The venue fitted to the world bounds of every object in the scene's collection."""
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


def make_venue(scene):
    """The venue every layer emits in: the emitter cage when there is one (its location,
    Z rotation, XY scale and Z scale are the venue's centre and floor, turn, half-extents
    and height), else fitted to the scene's collection."""
    fx = scene.afterglow.effects
    cage = fx.emitter
    if cage is None:
        return _fitted_venue(scene)
    bpy.context.view_layer.update()  # matrix_world is stale right after the cage is moved
    loc, rot, scale = cage.matrix_world.decompose()
    rx, ry, h = (max(abs(v), 0.05) for v in scale)
    return Venue(fx.venue_shape, loc.x, loc.y, rx, ry, loc.z, loc.z + h,
                 exact=True, angle=rot.to_euler().z)


# --------------------------------------------------------------------------- emitter cage

EMITTER_TAG = "afterglow_emitter"
EMITTER_NAME = "Afterglow Emitter"
CAGE_POSTS = 8  # vertical edges drawn on a circle cage


def _cage_geometry(shape):
    """(verts, edges) of a unit cage: the footprint at the floor (z 0) and top (z 1), the
    travelling path ring at PATH_Z, and vertical posts. The object's scale sizes it."""
    outline = _unit_outline(shape)
    n = len(outline)
    verts, edges = [], []
    for z in (0.0, 1.0, PATH_Z):
        base = len(verts)
        verts += [(x, y, z) for x, y in outline]
        edges += [(base + i, base + (i + 1) % n) for i in range(n)]
    step = max(1, n // CAGE_POSTS)
    edges += [(i, n + i) for i in range(0, n, step)]
    return verts, edges


def refresh_cage(scene):
    """Redraw the scene's emitter cage for the current Venue Shape (cages Afterglow made)."""
    fx = scene.afterglow.effects
    cage = fx.emitter
    if not (cage and cage.get(EMITTER_TAG) and cage.type == "MESH"):
        return
    me = cage.data
    verts, edges = _cage_geometry(fx.venue_shape)
    me.clear_geometry()
    me.from_pydata(verts, edges, [])
    me.update()


def fit_cage(scene):
    """Create the scene's emitter cage if needed, and fit it to the collection: the cage
    then outlines exactly the venue that Shape, Height and Margin fit today."""
    fx = scene.afterglow.effects
    v = _fitted_venue(scene)
    hx, hy = v.half()
    cage = fx.emitter
    if cage is None or not cage.get(EMITTER_TAG):
        me = bpy.data.meshes.new(EMITTER_NAME)
        me[EMITTER_TAG] = 1
        cage = bpy.data.objects.new(EMITTER_NAME, me)
        cage[EMITTER_TAG] = 1
        cage.hide_render = True        # a viewport control, never rendered
        cage.show_in_front = True      # visible through the venue
        cage.lock_rotation = (True, True, False)  # the venue turns about Z only
        scene.collection.objects.link(cage)
        fx.emitter = cage
    refresh_cage(scene)
    cage.location = (v.center.x, v.center.y, v.z0)
    cage.rotation_euler = (0.0, 0.0, 0.0)
    cage.scale = (hx, hy, v.span_z)
    return cage


def _spawn_targets(scene):
    """[(x, y, top)] of the Spawn On collection's objects: each one's world bounding-box
    centre and top, sorted by name so the draw is deterministic."""
    coll = scene.afterglow.effects.spawn_on
    if coll is None:
        return []
    bpy.context.view_layer.update()
    out = []
    for ob in sorted(coll.all_objects, key=lambda o: o.name):
        if ob.get(TAG) or ob.get(EMITTER_TAG):
            continue  # Afterglow's own lights and cage are never targets
        pts = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
        out.append((sum(p.x for p in pts) / 8, sum(p.y for p in pts) / 8, max(p.z for p in pts)))
    return out


LIGHT_KIND = {"strikes": "SPOT", "blooms": "POINT", "flashes": "POINT",
              "trails": "POINT", "downward": "POINT", "gun": "SPOT"}
SPOT_SIZE = {"strikes": 0.5, "gun": 0.18}
SPOT_BLEND = 0.35
ENERGY = {"strikes": 900.0, "blooms": 350.0, "flashes": 4000.0,
          "trails": 300.0, "downward": 250.0, "gun": 700.0}
DOWN_LIGHTS = 3        # cascade size of one Downward Trail event: a trail of falling lights
DOWN_LAG = 0.12        # fraction of the event duration between one cascade light and the next
DOWN_GAIN = (1.0, 0.55, 0.3)  # head brightest, tail faintest
DUR = {"strikes": 0.45, "blooms": 1.1, "flashes": 0.22, "trails": 1.8, "downward": 0.9, "gun": 0.28}
ATTACK = {"strikes": 0.03, "blooms": 0.12, "flashes": 0.02, "trails": 0.15, "downward": 0.05, "gun": 0.015}
HOLD = {"strikes": 0.5, "blooms": 0.45, "flashes": 0.35, "trails": 0.7, "downward": 0.8, "gun": 0.85}
TAIL_S = 2.5             # keep the timeline this long past the song so tails finish
# Visible Beams. A Data Strike becomes a thin, dashed filament of light from the venue top
# to the floor (a bright core inside a faint glow sleeve), lit by a point light near its
# base so it lights its surroundings from low down. A Gun Burst's spot carries a glowing
# tracer cone along its aim and tightens to it (a spot's brightness does not depend on its
# cone angle, so the floor pool shrinks but stays as bright).
BEAM_LAYERS = ("strikes", "gun")
BEAM_SPOT_SIZE = {"gun": 0.12}
BEAM_SPOT_BLEND = 0.2
# Beam sizes are real-world metres: the venue's height only sets how tall a beam is, never
# how thick it is, how far its companion sits or how fast its dashes stream.
STRIKE_BASE = 1.2        # m: Strike light height above the floor (at most a quarter of the venue)
STRIKE_BEAM_ENERGY = 0.4  # its energy relative to the spot it replaces: close to the floor
FILAMENT_RADIUS = 0.018  # m: Strike core radius...
SLEEVE_RADIUS = 0.06     # m: ...and its glow sleeve's
FILAMENT_EMISSION = 30.0  # emission strengths at Glow 1 and full event brightness
SLEEVE_EMISSION = 1.0
SLEEVE_POWER = 3.0       # sleeve edge falloff: brightest hugging the core, gone at its rim
DASH_SCALE = 1.3         # dash pattern features per metre along a Strike
DASH_CUT = 0.45          # pattern values below this are gaps (each Strike light has its own)
# Companion: a fainter filament beside each Strike, mostly gaps, so it reads as a string of
# bright beads (the reference's beaded strikes). Its side is seeded per light.
COMPANION_OFFSET = (0.6, 1.1)  # m: distance from the Strike
BEAD_SCALE = 3.0         # bead pattern features per metre
BEAD_CUT = 0.6           # pattern values below this are gaps: short beads, long gaps
BEAD_EMISSION = 18.0
FLOW_SPEED = 6.0         # m/s: dashes and beads stream down this fast at Flow 1
GUN_TRACER = 1.0         # Gun Burst tracer length, as a fraction of the venue radius
SHAFT_SEGMENTS = 24
TRACER_EMISSION = 4.0
TRACER_CORE = 4.0        # tracer edge falloff power: bright core, soft edges hide the cone
TRACER_FADE = 2.0        # tracer falloff power along its length, brightest at its light
GLOW = "afterglow_glow"  # shaft object property keyed with its event envelope (0..)
GLOW_PATH = f'["{GLOW}"]'
CUT_FADE = 0.03          # s an event cut short by a stolen pool slot takes to fade out
CUT_EPS = 0.05           # frames: a cut event is dark and still this long before the handover
                         # (FCurve.update() merges keys closer than 0.01 frame)
UP = Vector((0.0, 0.0, 1.0))


class _Keys:
    """Sparse keyframes collected per channel, then written once into the object's
    layered action. Channels are (struct, resolved data path, array index)."""

    def __init__(self):
        self.channels = {}
        self.shafts = {}  # light object name -> its Visible Beams shaft object
        self.beams = False  # building this layer with Visible Beams
        self.targets = []   # Spawn On: [(x, y, top)] that footprint layers land on
        self.energy = 1.0   # light energy scale for this layer (Visible Beams strikes sit low)

    def add(self, struct, idblock, path, index, frame, value, interp):
        ch = self.channels.get((struct, path, index))
        if ch is None:
            ch = self.channels[(struct, path, index)] = {
                "id": idblock, "full": struct.path_from_id(path), "interp": interp, "values": {}}
        ch["values"][round(float(frame), 4)] = float(value)

    def add_vec(self, struct, idblock, path, frame, values, interp):
        for i, v in enumerate(values):
            self.add(struct, idblock, path, i, frame, v, interp)

    def cut(self, lights, frame, fade):
        """Truncate what these lights are already keyed to do from frame on, so a stolen
        pool slot hands over cleanly instead of interleaving two events' keys: energy fades
        to 0 over the fade frames, motion holds still, and the light only moves once dark."""
        structs = {s for ob in lights for s in (ob, ob.data, self.shafts.get(ob.name)) if s}
        end = frame - CUT_EPS
        for (struct, path, _), ch in self.channels.items():
            if struct not in structs:
                continue
            values = ch["values"]
            bright = path in ("energy", GLOW_PATH)  # brightness fades out; motion holds
            at = round(float(end - fade if bright else end), 4)
            v = _value_at(values, at)
            for f in [f for f in values if f > at]:
                del values[f]
            values[at] = v
            if bright:
                values[round(float(end), 4)] = 0.0

    def write(self):
        for (struct, path, index), ch in self.channels.items():
            frames = sorted(ch["values"])
            v0 = ch["values"][frames[0]]
            if path.startswith('["'):  # a custom property (a shaft's glow)
                struct[path[2:-2]] = v0
                struct.keyframe_insert(path, frame=frames[0])
            elif index >= 0:
                getattr(struct, path)[index] = v0
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


def _value_at(values, frame):
    """Linear interpolation of sparse {frame: value} keys, held past either end."""
    frames = sorted(values)
    i = bisect_right(frames, frame)
    if i == 0:
        return values[frames[0]]
    if i == len(frames):
        return values[frames[-1]]
    f0, f1 = frames[i - 1], frames[i]
    return values[f0] + (values[f1] - values[f0]) * (frame - f0) / (f1 - f0)


def _own_fcurve(idblock, path, index):
    ad = idblock.animation_data
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    want = 0 if index < 0 else index
    fc = next((f for f in bag.fcurves if f.data_path == path and f.array_index == want), None)
    if fc is None:
        raise RuntimeError(f"missing fcurve {path}[{index}] on {idblock.name}")
    return fc


def _fcurves_of(idblock):
    ad = idblock.animation_data
    if not (ad and ad.action):
        return []
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    return list(bag.fcurves) if bag else []


def _tag_action(idblock):
    ad = idblock.animation_data
    if ad and ad.action:
        ad.action[TAG] = VERSION


def _make_unit(coll, layer, slot, color, beams=False):
    """One pool slot: a tuple of real lights (a staggered cascade for Downward Trails,
    a single light for every other layer). Without Visible Beams nothing else is
    generated, so the effects can never alter the venue's own look; with it, spots
    narrow to their shaft's cone."""
    n = DOWN_LIGHTS if layer == "downward" else 1
    lights = []
    for j in range(n):
        name = f"{PREFIX}_{layer}_{slot}_light" + (str(j) if n > 1 else "")
        kind = "POINT" if beams and layer == "strikes" else LIGHT_KIND[layer]  # lit from its base
        li = bpy.data.lights.new(name, kind)
        li[TAG] = VERSION
        li.color = (color[0], color[1], color[2])
        li.energy = 0.0
        li.use_shadow = False  # dozens of shadow-casting lights would crawl; spill only
        if kind == "SPOT":
            li.spot_size = BEAM_SPOT_SIZE[layer] if beams else SPOT_SIZE[layer]
            li.spot_blend = BEAM_SPOT_BLEND if beams else SPOT_BLEND
        light_ob = bpy.data.objects.new(name, li)
        light_ob[TAG] = VERSION
        light_ob.rotation_mode = "QUATERNION"
        coll.objects.link(light_ob)
        lights.append(light_ob)
    return tuple(lights)


def _beam_material(layer, kind, color, glow, flow=None):
    """One additive glow material (emission over transparency) per beam part, shared by
    a layer's shafts. Each shaft's keyed GLOW property (an Object attribute) sets its
    brightness. kind: "filament" (a Strike's bright core) or "sleeve" (its faint glow),
    both broken into dashes that differ per shaft; "beads" (its companion: short beads, long
    gaps, a pattern of its own); or "tracer" (a Gun Burst cone, brightest at its light and
    on its core, fading to nothing at its edges and tail). flow = (start, end, fps, metres
    per second) streams the dash patterns down the Strike: the pattern's height offset is
    keyed to rise linearly through the song, so the same dashes slide steadily downward."""
    mat = bpy.data.materials.new(f"{PREFIX}_{layer}_{kind}")
    mat[TAG] = VERSION
    mat.surface_render_method = "BLENDED"
    mat.use_backface_culling = False
    nt = mat.node_tree
    nt.nodes.clear()
    N, L = nt.nodes, nt.links

    def op(name, a, b):
        n = N.new("ShaderNodeMath")
        n.operation = name
        for socket, v in zip(n.inputs, (a, b)):
            if isinstance(v, bpy.types.NodeSocket):
                L.new(v, socket)
            else:
                socket.default_value = v
        return n.outputs[0]

    facing = N.new("ShaderNodeLayerWeight")
    facing.inputs["Blend"].default_value = 0.5
    inside = op("SUBTRACT", 1.0, facing.outputs["Facing"])  # 1 facing the camera, 0 at the rim
    coords = N.new("ShaderNodeTexCoord")
    if kind == "tracer":
        z = N.new("ShaderNodeSeparateXYZ")
        L.new(coords.outputs["Generated"], z.inputs[0])  # Generated Z: 1 at the light, 0 tail
        shape = op("MULTIPLY", op("POWER", inside, TRACER_CORE), op("POWER", z.outputs["Z"], TRACER_FADE))
        gain = TRACER_EMISSION
    else:
        z = N.new("ShaderNodeSeparateXYZ")
        L.new(coords.outputs["Object"], z.inputs[0])     # metres along the Strike
        beads = kind == "beads"
        height = z.outputs["Z"]
        if flow:
            offset = N.new("ShaderNodeValue")
            offset.name = offset.label = "Flow"
            height = op("ADD", height, offset.outputs[0])  # sampling higher up = dashes move down
        line = N.new("ShaderNodeCombineXYZ")
        L.new(op("MULTIPLY", height, BEAD_SCALE if beads else DASH_SCALE), line.inputs["Z"])
        noise = N.new("ShaderNodeTexNoise")
        noise.noise_dimensions = "4D"
        noise.inputs["Scale"].default_value = 1.0
        noise.inputs["Detail"].default_value = 3.0
        L.new(line.outputs[0], noise.inputs["Vector"])
        seed = op("MULTIPLY", N.new("ShaderNodeObjectInfo").outputs["Random"], 100.0)
        L.new(op("ADD", seed, 37.0) if beads else seed, noise.inputs["W"])  # beads: their own pattern
        cut = BEAD_CUT if beads else DASH_CUT
        dash = N.new("ShaderNodeMapRange")              # a steep step: lit, or a gap
        dash.inputs["From Min"].default_value = cut
        dash.inputs["From Max"].default_value = cut + 0.02
        L.new(noise.outputs["Factor"], dash.inputs["Value"])
        if kind in ("filament", "beads"):
            shape = op("MULTIPLY", dash.outputs["Result"], inside)
            gain = BEAD_EMISSION if beads else FILAMENT_EMISSION
        else:
            shape = op("MULTIPLY", dash.outputs["Result"], op("POWER", inside, SLEEVE_POWER))
            gain = SLEEVE_EMISSION
    key = N.new("ShaderNodeAttribute")
    key.attribute_type, key.attribute_name = "OBJECT", GLOW
    emission = N.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (color[0], color[1], color[2], 1.0)
    L.new(op("MULTIPLY", shape, op("MULTIPLY", key.outputs["Factor"], gain * glow)), emission.inputs["Strength"])
    add = N.new("ShaderNodeAddShader")
    L.new(N.new("ShaderNodeBsdfTransparent").outputs[0], add.inputs[0])
    L.new(emission.outputs[0], add.inputs[1])
    out = N.new("ShaderNodeOutputMaterial")
    L.new(add.outputs[0], out.inputs["Surface"])
    if flow and kind != "tracer":
        start, end, fps, speed = flow
        sock = N["Flow"].outputs[0]
        for f in (start, end):
            sock.default_value = (f - start) / fps * speed
            sock.keyframe_insert("default_value", frame=f)
        for fc in _fcurves_of(nt):
            fc.extrapolation = "LINEAR"
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"
        _tag_action(nt)
    return mat


def _tube(rings, slot, centre=(0.0, 0.0)):
    """Open tube through rings [(z, radius), ...] along local Z, around centre in local XY:
    (verts, faces, slot)."""
    n = SHAFT_SEGMENTS
    cx, cy = centre
    verts = [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n), z)
             for z, r in rings for i in range(n)]
    faces = [(k * n + i, (k + 1) * n + i, (k + 1) * n + (i + 1) % n, k * n + (i + 1) % n)
             for k in range(len(rings) - 1) for i in range(n)]
    return verts, faces, slot


def _make_shaft(coll, light_ob, tubes, mats):
    """A glowing beam mesh parented to its light, so it follows every keyed move and aim.
    It is a glow you see, not a lamp: no shadows and no light on the scene (the light does
    the lighting), shown as a wireframe and unselectable so it never hides or blocks the
    venue. tubes: _tube() parts, each using mats[slot]."""
    verts, faces, slots = [], [], []
    for tv, tf, slot in tubes:
        base = len(verts)
        verts += tv
        faces += [tuple(base + i for i in f) for f in tf]
        slots += [slot] * len(tf)
    name = light_ob.name.replace("_light", "_shaft")
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.shade_smooth()  # flat faces would band the soft edge falloff
    me.polygons.foreach_set("material_index", slots)
    me[TAG] = VERSION
    for m in mats:
        me.materials.append(m)
    ob = bpy.data.objects.new(name, me)
    ob[TAG] = VERSION
    ob[GLOW] = 0.0
    ob.parent = light_ob
    for attr in ("visible_shadow", "visible_diffuse", "visible_transmission", "visible_volume_scatter"):
        setattr(ob, attr, False)  # camera and reflections see it; it lights nothing
    ob.display_type = "WIRE"
    ob.hide_select = True
    coll.objects.link(ob)
    return ob


def _beam_kinds(name, companions):
    """The material parts of a layer's beams, in mesh slot order."""
    if name == "strikes":
        return ("filament", "sleeve", "beads") if companions else ("filament", "sleeve")
    return ("tracer",)


def _strike_base(venue):
    """How high a Visible Beams Strike light sits above the floor, in metres."""
    return min(STRIKE_BASE, 0.25 * venue.span_z)


def _beam_tubes(name, venue, rng, companions):
    """One beam's tubes: a Strike's full-height filament inside its glow sleeve, from the
    floor to the venue top around its light (_strike_base up), plus a beaded companion filament
    beside it on a side drawn from rng; or a Gun tracer cone along the spot's aim (-Z)."""
    if name == "strikes":
        span = venue.span_z
        base = _strike_base(venue)
        lo, hi = -base, span - base
        core, sleeve = FILAMENT_RADIUS, SLEEVE_RADIUS
        tubes = [_tube(((lo, core), (hi, core)), 0), _tube(((lo, sleeve), (hi, sleeve)), 1)]
        if companions:
            a, d = 2 * math.pi * rng.random(), rng.uniform(*COMPANION_OFFSET)
            tubes.append(_tube(((lo, core), (hi, core)), 2, (d * math.cos(a), d * math.sin(a))))
        return tubes
    length = GUN_TRACER * venue.radius
    half = math.tan(BEAM_SPOT_SIZE[name] / 2)
    return [_tube(((0.0, 0.02 * length * half), (-length, length * half)), 0)]


def _assign(free, t0, dur):
    """(slot, busy): the first slot idle at t0, else the one closest to finishing, which
    is then still mid-event (busy). Bounded pool, no duplicates."""
    for i, until in enumerate(free):
        if until <= t0:
            free[i] = t0 + dur
            return i, False
    i = min(range(len(free)), key=free.__getitem__)
    free[i] = t0 + dur
    return i, True


def _envelope(t0, dur, attack, hold):
    """Brightness 0 -> 1 -> 1 -> 0 in seconds; BEZIER AUTO_CLAMPED keys give the pulse shape."""
    return [(t0, 0.0), (t0 + attack, 1.0), (t0 + dur * hold, 1.0), (t0 + dur, 0.0)]


def _bright(strength, intensity):
    return max(0.0, min(strength, 2.0)) * max(0.0, intensity)


def _key_appear(keys, lights, layer, b, env, frame_of):
    """Light energy follows the envelope; dark outside it. A Visible Beams shaft glows
    with the same envelope."""
    for light_ob in lights:
        shaft = keys.shafts.get(light_ob.name)
        for t, e in env:
            keys.add(light_ob.data, light_ob.data, "energy", -1, frame_of(t),
                     ENERGY[layer] * keys.energy * b * e, "BEZIER")
            if shaft:
                keys.add(shaft, shaft, GLOW_PATH, -1, frame_of(t), b * e, "BEZIER")


def _key_static_loc(keys, lights, pos, t0, dur, frame_of):
    for ob in lights:
        keys.add_vec(ob, ob, "location", frame_of(t0), pos, "LINEAR")
        keys.add_vec(ob, ob, "location", frame_of(t0 + dur), pos, "LINEAR")


SPAWN_GAP = 0.25   # m: a Strike on an object glows this far above its top
BLOOM_LIFT = 0.1   # a Bloom on an object floats at least this far above it


def _spawn(venue, rng, targets):
    """(ground point, top or None) where a footprint layer's event lands: one of the Spawn
    On objects when set (top = its top), else a random point inside the venue."""
    if targets:
        x, y, top = targets[min(int(rng.random() * len(targets)), len(targets) - 1)]
        return Vector((x, y, venue.z0)), top
    return venue.footprint(rng), None


def _layer_keys(name, keys, lights, venue, ev, b, timing, rng, fps, start):
    """Motion + brightness channels for one event of one layer. Lights only: no
    geometry is keyed, so the venue's own surfaces stay the only visible things."""
    t0, strength = ev
    dur = DUR[name] * max(timing, 0.05)
    frame_of = lambda t: start + t * fps  # noqa: E731
    env = _envelope(t0, dur, ATTACK[name], HOLD[name])
    bright = _bright(strength, b)
    span = venue.span_z
    if name in ("strikes", "blooms", "flashes"):
        if name == "strikes":
            p, top = _spawn(venue, rng, keys.targets)
            # the spot crowns the strike, aimed down; a Visible Beams strike glows from its base
            base = venue.z0 + _strike_base(venue) if top is None else top + SPAWN_GAP
            pos = Vector((p.x, p.y, base if keys.beams else venue.z1))
            shaft = keys.shafts.get(lights[0].name)
            if shaft:  # the filament ends at the venue top, wherever this strike lands
                reach = max(venue.z1 - base, 0.01 * span) / (span - _strike_base(venue))
                keys.add_vec(shaft, shaft, "scale", frame_of(t0), (1.0, 1.0, reach), "CONSTANT")
        elif name == "blooms":
            p, top = _spawn(venue, rng, keys.targets)
            z = venue.z0 + 0.45 * span
            pos = Vector((p.x, p.y, z if top is None else max(z, top + BLOOM_LIFT * span)))
        else:
            pos = Vector((venue.center.x, venue.center.y, venue.z0 + 0.85 * span))
        _key_static_loc(keys, lights, pos, t0, dur, frame_of)
    elif name == "trails":
        light_ob = lights[0]
        u0 = rng.random()
        direction = 1.0 if rng.random() < 0.5 else -1.0
        u_span = direction * (0.25 + 0.25 * rng.random())
        samples = int(max(6, min(48, round(dur * fps))))
        for i in range(samples):
            frac = i / (samples - 1)
            p = venue.path_point(u0 + u_span * frac)
            keys.add_vec(light_ob, light_ob, "location", frame_of(t0 + dur * frac), p, "LINEAR")
    elif name == "downward":
        p, land = _spawn(venue, rng, keys.targets)
        top = Vector((p.x, p.y, venue.z1))
        bot = Vector((p.x, p.y, venue.z0 + 0.02 * span if land is None else land))
        # A smooth cascade: every light glides the full height at the SAME constant
        # speed, started one DOWN_LAG apart, head brightest — a falling trail of
        # lights instead of one stretched, scaled cylinder.
        fall = dur * (1.0 - (len(lights) - 1) * DOWN_LAG)
        for j, light_ob in enumerate(lights):
            tj = t0 + j * DOWN_LAG * dur
            _key_appear(keys, (light_ob,), name, bright * DOWN_GAIN[j],
                        _envelope(tj, fall, ATTACK[name], HOLD[name]), frame_of)
            keys.add_vec(light_ob, light_ob, "location", frame_of(tj), top, "LINEAR")
            keys.add_vec(light_ob, light_ob, "location", frame_of(tj + fall), bot, "LINEAR")
        return
    else:  # gun: short, fast, directed rim -> centre
        light_ob = lights[0]
        muzzle = venue.path_point(rng.random())
        muzzle.z = venue.z0 + 0.55 * span
        target = Vector((venue.center.x, venue.center.y, venue.z0 + 0.30 * span))
        q = UP.rotation_difference((target - muzzle).normalized())
        q_flip = q @ Quaternion((1.0, 0.0, 0.0), math.pi)  # spots shine along -Z
        mid = muzzle.lerp(target, 0.5)
        for f, pos in ((frame_of(t0), muzzle), (frame_of(t0 + dur / 2), mid),
                       (frame_of(t0 + dur), target)):
            keys.add_vec(light_ob, light_ob, "location", f, pos, "LINEAR")
        keys.add_vec(light_ob, light_ob, "rotation_quaternion", frame_of(t0), q_flip, "LINEAR")
        keys.add_vec(light_ob, light_ob, "rotation_quaternion", frame_of(t0 + dur), q_flip, "LINEAR")
    _key_appear(keys, lights, name, bright, env, frame_of)


def _build_layer(coll, scene, venue, name, events, fps, start, end):
    fx = scene.afterglow.effects
    layer = getattr(fx, name)
    keys = _Keys()
    beams = fx.beams and name in BEAM_LAYERS
    units = [_make_unit(coll, name, slot, tuple(layer.color), beams)
             for slot in range(POOL[name])]
    keys.beams = beams
    keys.targets = _spawn_targets(scene)
    keys.energy = STRIKE_BEAM_ENERGY if beams and name == "strikes" else 1.0
    if beams:
        speed = FLOW_SPEED * fx.beam_flow
        flow = (start, end, fps, speed) if speed > 0 else None
        mats = [_beam_material(name, k, tuple(layer.color), fx.beam_glow, flow)
                for k in _beam_kinds(name, fx.beam_companions)]
        sides = random.Random(f"{fx.seed}:{name}:beams")
        for lights in units:
            for light_ob in lights:
                tubes = _beam_tubes(name, venue, sides, fx.beam_companions)
                keys.shafts[light_ob.name] = _make_shaft(coll, light_ob, tubes, mats)
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
        slot, busy = _assign(free, t0, dur)
        if busy:  # every slot still lit: cut the oldest event short rather than interleave
            keys.cut(units[slot], frame0, CUT_FADE * fps)
        _layer_keys(name, keys, units[slot], venue, (t0, strength), intensity, timing, rng, fps, start)
        placed += 1
    # baseline: every light dark outside its events, so silence renders as nothing
    b0, b1 = start - 1.0, end + 1.0
    for lights in units:
        for light_ob in lights:
            shaft = keys.shafts.get(light_ob.name)
            for f in (b0, b1):
                keys.add(light_ob.data, light_ob.data, "energy", -1, f, 0.0, "BEZIER")
                if shaft:
                    keys.add(shaft, shaft, GLOW_PATH, -1, f, 0.0, "BEZIER")
            light_ob.location = (venue.center.x, venue.center.y, venue.z0)
    keys.write()
    for lights in units:
        for light_ob in lights:
            _tag_action(light_ob)
            _tag_action(light_ob.data)
    for shaft in keys.shafts.values():
        _tag_action(shaft)
    return placed


def _animated(scene, path, value):
    """A keyframeable layer control as Build sees it: its keyframes when it has an F-curve
    (the current value would change with the frame), else its value."""
    fc = _fcurve(scene, path)
    if fc is None:
        return round(value, 4)
    return (fc.extrapolation, tuple(
        (kp.interpolation, kp.easing, *(round(x, 4) for v in (kp.co, kp.handle_left, kp.handle_right) for x in v))
        for kp in fc.keyframe_points))


def settings_sig(scene):
    """Everything a build depends on, for the panel's stale-build hint, including the
    keyframes of the Weight/Intensity/Timing controls Build samples per event. VERSION is
    included so builds from older add-on versions always report outdated."""
    s = scene.afterglow
    fx = s.effects
    vals = [VERSION, fx.venue_shape, fx.seed, round(fx.height, 4), round(fx.margin, 4),
            fx.beams, round(fx.beam_glow, 4), fx.beam_companions, round(fx.beam_flow, 4)]
    cage = fx.emitter                                   # moving the cage moves the venue
    vals += [tuple(round(x, 4) for row in cage.matrix_world for x in row) if cage else None]
    vals += [fx.spawn_on.name if fx.spawn_on else None,  # moving a target moves its spawns
             tuple(tuple(round(c, 4) for c in t) for t in _spawn_targets(scene))]
    for name in LAYERS:
        layer = getattr(fx, name)
        vals += [layer.enabled, layer.trigger_band, tuple(round(c, 4) for c in layer.color)]
        vals += [_animated(scene, f"afterglow.effects.{name}.{p}", getattr(layer, p))
                 for p in ("weight", "intensity", "timing")]
    vals += [s.sound.filepath if s.sound else "", s.start_frame,
             round(scene.render.fps / scene.render.fps_base, 4),
             s.collection.name if s.collection else ""]
    return repr(vals)


def _collections(scene):
    """This scene's effects collections. Every scene keeps its own build: other scenes'
    effects are never reported, rebuilt or removed from here."""
    return [c for c in scene.collection.children if c.get(TAG)]


def outdated(scene):
    coll = next(iter(_collections(scene)), None)
    return bool(coll) and coll.get(SIG) != settings_sig(scene)


def build(scene):
    """(Re)build the effects collection: analyse once, then key pooled real lights for
    every enabled layer, plus their glowing shafts when Visible Beams is on. The venue is
    left untouched. Deterministic for a given (sound, settings, seed)."""
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
    """Delete this scene's build and every tagged datablock it owns. User data and other
    scenes' builds are never touched. Tagged datablocks left without users are swept too,
    so builds from older, geometry-based versions of the add-on are removed cleanly."""
    n = 0
    for c in _collections(scene):
        for ob in [o for o in c.all_objects if o.get(TAG)]:
            bpy.data.objects.remove(ob, do_unlink=True)
            n += 1
        bpy.data.collections.remove(c)
    for c in [c for c in bpy.data.collections if c.get(TAG) and c.users == 0]:
        bpy.data.collections.remove(c)  # unlinked from every scene: nobody's build
    for ob in [o for o in bpy.data.objects if o.get(TAG) and o.users == 0]:
        bpy.data.objects.remove(ob)
        n += 1
    # lights before actions: a light's own energy action only loses its user with the light
    for store in (bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.actions):
        for db in [d for d in store if d.get(TAG) and d.users == 0]:
            store.remove(db)
    return n


