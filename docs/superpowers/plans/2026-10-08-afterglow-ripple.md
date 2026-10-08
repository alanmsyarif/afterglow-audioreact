# Afterglow Tempo Ripple Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An optional Ripple mode where every kick sweeps across the collection by position, and the sweep speeds up as the song's tempo rises.

**Architecture:**
- A pure-numpy tempo detector (`tempo.py`) produces a local BPM curve.
- The curve is keyed onto a `Beat Length` Value node inside the shared node group.
- In the group, Ripple swaps each object's band for the kick band (40–150 Hz) and adds `Ripple Position × Beats per Sweep × Beat Length(t)` to its delay.
- The group gets a version tag, so groups built by v0.2 migrate automatically.

**Tech Stack:** Blender 5.2 bpy, `aud`, numpy (bundled with Blender). The tests are headless assert scripts, as before.

**Spec:** `docs/superpowers/specs/2026-10-08-afterglow-ripple-design.md` (extends `2026-10-08-afterglow-design.md`)

## Global Constraints

- Blender 5.2.0 minimum. No new dependencies: `aud` and `numpy` ship with Blender.
- Kick band: 40–150 Hz. Beat Length default 0.5 s (120 BPM). Tempo range 60–180 BPM. Windows are 8 s every 1 s, FFT 2048, hop 512.
- New group inputs are appended after the existing 11, in this order: `Ripple`, `Beats per Sweep`, `Ripple Position`. This keeps the old socket identifiers.
- Group tag `afterglow = 2` (VERSION). v0.2 groups are tagged `1`.
- `nodes.set_input` must keep its `mod.id_data.update_tag()`: 5.2 doesn't tag the depsgraph on its own.
- The tests' `at()` must keep stepping through a neighbor frame (the Blender first-evaluation glitch).
- Test command, from the repo root in Git Bash:
  `B="/c/Program Files/Blender Foundation/Blender 5.2/blender.exe"; "$B" -b --factory-startup --python-exit-code 1 --python tests/test_<name>.py`

## Verified facts (2026-10-08)

- `aud.Sound(path).data()` returns float32 `(samples, channels)` in 0.07 s for 3 minutes of audio. `clip.specs[0]` is the sample rate.
- `socket.keyframe_insert("default_value", frame=f)` on a Value node inside the GN group animates the group, and every user follows it. After `scene.frame_set(f)` the original socket's `default_value` holds the animated value. `group.animation_data_clear()` removes the keys.
- Re-pointing `mod.node_group` to a group built with the same socket order keeps input values (identifiers match).
- The tempo prototype tracks a 120→160 BPM synthetic click track within about 1 BPM. On the user's song it reads 122 BPM mostly, with 165 BPM sections.

## Review Focus

1. **The user's existing v0.2 file:** opening it and pressing Apply must migrate the group and keep Calibrate's Gain and Threshold. Pinned by `test_old_group_migrates` (Task 2).
2. **Silent or sparse sections:** these must not produce wild BPM values that make the wave jump. They fall back to 120 BPM. Pinned by `test_silence_reads_default` (Task 1).
3. **Changing Start Frame or Sound after baking:** the curve must follow the audio, not stay at the old frames. Pinned by `test_start_frame_rekeys_curve` (Task 3).
4. **Sound packed or missing on disk:** Apply still succeeds, with a warning. Pinned by `test_bake_missing_file_raises` (Task 3) and `test_apply_reports_tempo_or_skip` (Task 4).
5. **Objects at the same location, or a collection lying along Y or Z:** the position math must not divide by zero, and the wave runs along the actual spread. Pinned by `test_place_*` (Task 4).

---

### Task 1: Tempo curve (pure numpy)

**Files:**
- Create: `afterglow/tempo.py`
- Test: `tests/test_tempo.py`

**Interfaces:**
- Produces: `tempo.tempo_curve(samples: np.ndarray, rate: float) -> list[tuple[float, float]]` returns `(seconds, bpm)` every 1 s. Also `tempo._median(curve, k=5)`, `tempo.DEFAULT_BPM = 120.0`.

- [ ] **Step 1: Write the failing tests** in `tests/test_tempo.py`:

```python
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


helpers.run(globals())
```

- [ ] **Step 2: Run, and confirm it fails** with `ImportError: cannot import name 'tempo'`.

- [ ] **Step 3: Implement `afterglow/tempo.py`:**

```python
"""Local tempo detection, baked into the node group as a Beat Length curve."""
import numpy as np

FFT, HOP = 2048, 512
WINDOW_S, STEP_S = 8.0, 1.0
LO_BPM, HI_BPM, DEFAULT_BPM = 60.0, 180.0, 120.0
CHUNK = 1024  # STFT frames per batch, bounds memory on long songs


def _onsets(mono):
    """Positive log-spectral flux per hop, minus its slow trend."""
    frames = 1 + (len(mono) - FFT) // HOP
    window = np.hanning(FFT).astype(np.float32)
    parts, prev = [], None
    for c in range(0, frames, CHUNK):
        idx = np.arange(FFT)[None, :] + HOP * np.arange(c, min(c + CHUNK, frames))[:, None]
        spec = np.log1p(100 * np.abs(np.fft.rfft(mono[idx] * window, axis=1)))
        if prev is not None:
            spec = np.vstack([prev, spec])
        parts.append(np.maximum(np.diff(spec, axis=0), 0).sum(axis=1))
        prev = spec[-1:]
    flux = np.concatenate(parts)
    return np.maximum(flux - np.convolve(flux, np.ones(16) / 16, mode="same"), 0)


def _median(curve, k=5):
    bpms = [b for _, b in curve]
    h = k // 2
    return [(t, float(np.median(bpms[max(0, i - h):i + h + 1]))) for i, (t, _) in enumerate(curve)]


def tempo_curve(samples, rate):
    """[(seconds, bpm)] every STEP_S from onset-strength autocorrelation over WINDOW_S."""
    mono = (samples.mean(axis=1) if samples.ndim == 2 else samples).astype(np.float32)
    fallback = [(len(mono) / rate / 2, DEFAULT_BPM)]
    if len(mono) < FFT * 4:
        return fallback
    flux = _onsets(mono)
    fps = rate / HOP
    width = min(int(WINDOW_S * fps), len(flux))
    lags = np.arange(int(fps * 60 / HI_BPM), int(fps * 60 / LO_BPM) + 1)
    lags = lags[lags < width - 1]
    if not len(lags):
        return fallback
    bpm = 60 * fps / lags
    prior = np.exp(-0.5 * (np.log2(bpm / DEFAULT_BPM) / 0.9) ** 2)  # settles octave errors
    out = []
    for start in range(0, len(flux) - width + 1, max(1, int(STEP_S * fps))):
        seg = flux[start:start + width] - flux[start:start + width].mean()
        ac = np.array([seg[:-lag] @ seg[lag:] for lag in lags]) * prior
        out.append(((start + width / 2) / fps, float(bpm[np.argmax(ac)]) if ac.max() > 0 else DEFAULT_BPM))
    return _median(out)
```

- [ ] **Step 4: Run, and confirm it passes:** `6/6 passed`. If `test_accelerating_tempo_tracked` misses by just over 5 at the first or last window only, that's the median filter's one-sided edge. Rule it, and widen the tolerance to 6 for the first and last entries only.

- [ ] **Step 5: Commit:** `feat: local tempo curve detection`

---

### Task 2: Node group v2 (ripple + migration)

**Files:**
- Modify: `afterglow/nodes.py` (replace whole file, shown below)
- Test: `tests/test_nodes.py` (append the tests below)

**Interfaces:**
- Produces: `nodes.VERSION = 2`, `nodes.BEAT_NODE = "Beat Length"`, `nodes.SCENE_INPUTS`, the new inputs `Ripple` / `Beats per Sweep` / `Ripple Position`, and a migrating `get_group()`. `OBJECT_INPUTS` excludes the scene-wide inputs and `Ripple Position`.

- [ ] **Step 1: Append the failing tests** to `tests/test_nodes.py`, before `helpers.run(globals())`:

```python
def test_ripple_uses_kick_band():
    ob = reactive("treble_but_ripple", TREBLE, {"Ripple": True})
    at(13)  # 100 Hz tone: outside the object's own band, inside the kick band
    assert attr(ob) > 0.3, attr(ob)


def test_ripple_delays_by_position():
    first = reactive("first", BASS, {"Ripple": True, "Decay": 0.0, "Ripple Position": 0.0})
    last = reactive("last", BASS, {"Ripple": True, "Decay": 0.0, "Ripple Position": 1.0})
    at(31)  # 1.25 s: bass ended for 'first'; 'last' lags 1 sweep x 0.5 s -> samples 0.75 s
    assert attr(first) < 0.02 and attr(last) > 0.3, (attr(first), attr(last))


def test_old_group_migrates():
    old = nodes.get_group()
    old[nodes.TAG] = 1  # what v0.2 wrote
    ob = reactive("kept", BASS, {"Gain": 3.0})
    new = nodes.get_group()
    assert new[nodes.TAG] == nodes.VERSION and new.name == nodes.GROUP_NAME
    assert ob.modifiers[0].node_group == new
    assert math.isclose(nodes.get_input(ob.modifiers[0], "Gain"), 3.0)
    assert len([g for g in bpy.data.node_groups if g.get(nodes.TAG)]) == 1
```

- [ ] **Step 2: Run, and confirm the three fail.** The ripple tests fail with `KeyError` on `"Ripple"`, and the migration test fails because `VERSION` is missing.

- [ ] **Step 3: Replace `afterglow/nodes.py` with:**

```python
"""The shared Afterglow geometry node group and modifier input helpers."""
import bpy

GROUP_NAME = "Afterglow AudioReact"
TAG = "afterglow"
VERSION = 2  # bump when the interface changes; get_group() migrates older groups
SAMPLES = 8  # ponytail: fixed smoothing sample count; expose it if long tails get steppy
BEAT_NODE = "Beat Length"
KICK = (40.0, 150.0)

# (name, socket type, default). Every float input is clamped to >= 0. Append only:
# socket identifiers follow creation order and migration relies on them.
INPUTS = (
    ("Sound", "NodeSocketSound", None),
    ("Start Frame", "NodeSocketInt", 1),
    ("Low", "NodeSocketFloat", 20.0),
    ("High", "NodeSocketFloat", 200.0),
    ("Gain", "NodeSocketFloat", 1.0),
    ("Threshold", "NodeSocketFloat", 0.0),
    ("Decay", "NodeSocketFloat", 0.15),
    ("Delay", "NodeSocketFloat", 0.0),
    ("Use Colors", "NodeSocketBool", False),
    ("Color A", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0)),
    ("Color B", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0)),
    ("Ripple", "NodeSocketBool", False),
    ("Beats per Sweep", "NodeSocketFloat", 1.0),
    ("Ripple Position", "NodeSocketFloat", 0.0),
)
SCENE_INPUTS = {"Sound", "Start Frame", "Ripple", "Beats per Sweep"}
OBJECT_INPUTS = [name for name, _, _ in INPUTS if name not in SCENE_INPUTS | {"Ripple Position"}]


def get_group():
    """The current Afterglow group, found by tag so renamed copies are reused. Groups from an
    older VERSION are replaced: their modifiers move to the current group, keeping values."""
    groups = [g for g in bpy.data.node_groups if g.get(TAG)]
    group = next((g for g in groups if g[TAG] >= VERSION), None) or _build()
    for old in groups:
        if old[TAG] < VERSION:
            for ob in bpy.data.objects:
                for m in ob.modifiers:
                    if m.type == "NODES" and m.node_group == old:
                        m.node_group = group
                        ob.update_tag()
            bpy.data.node_groups.remove(old)
            group.name = GROUP_NAME
    return group


def _socket(mod, name):
    return getattr(mod.properties.inputs, mod.node_group.interface.items_tree[name].identifier)


def set_input(mod, name, value):
    _socket(mod, name).value = value
    mod.id_data.update_tag()  # 5.2: setting properties.inputs from Python doesn't tag the depsgraph


def get_input(mod, name):
    return _socket(mod, name).value


def draw_input(layout, mod, name):
    layout.prop(_socket(mod, name), "value", text=name)


def _build():
    ng = bpy.data.node_groups.new(GROUP_NAME, "GeometryNodeTree")
    ng.is_modifier = True
    ng[TAG] = VERSION
    it = ng.interface
    it.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    it.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    for name, socket_type, default in INPUTS:
        s = it.new_socket(name, in_out="INPUT", socket_type=socket_type)
        if default is not None:
            s.default_value = default
        if socket_type == "NodeSocketFloat":
            s.min_value = 0.0
        if name == "Ripple Position":
            s.max_value = 1.0

    N, L = ng.nodes, ng.links

    def op(operation, a, b=0.0):
        n = N.new("ShaderNodeMath")
        n.operation = operation
        for socket, v in zip(n.inputs, (a, b)):
            if isinstance(v, bpy.types.NodeSocket):
                L.new(v, socket)
            else:
                socket.default_value = v
        return n.outputs[0]

    gi, go = N.new("NodeGroupInput"), N.new("NodeGroupOutput")
    g = gi.outputs
    beat = N.new("ShaderNodeValue")
    beat.name = beat.label = BEAT_NODE
    beat.outputs[0].default_value = 0.5  # seconds per beat (120 BPM) until tempo.bake keys it

    # Ripple: everyone listens to the kick band and lags by position x sweep length.
    low = op("ADD", g["Low"], op("MULTIPLY", g["Ripple"], op("SUBTRACT", KICK[0], g["Low"])))
    high = op("ADD", g["High"], op("MULTIPLY", g["Ripple"], op("SUBTRACT", KICK[1], g["High"])))
    lag = op("MULTIPLY", op("MULTIPLY", g["Ripple"], g["Ripple Position"]),
             op("MULTIPLY", g["Beats per Sweep"], beat.outputs[0]))
    delay = op("ADD", g["Delay"], lag)

    st = N.new("GeometryNodeInputSceneTime")
    # Geometry nodes can't read fps; Seconds / Frame recovers it.
    # At frame 0 this is 0/0 = 0; the frame gate below keeps frame 0 dark when Start Frame >= 1.
    spf = op("DIVIDE", st.outputs["Seconds"], st.outputs["Frame"])
    t = op("SUBTRACT", op("MULTIPLY", op("SUBTRACT", st.outputs["Frame"], g["Start Frame"]), spf), delay)
    step = op("MULTIPLY", g["Decay"], 0.25)
    decay = op("MAXIMUM", g["Decay"], 1e-4)

    # Peak-hold with exponential release: max over i of amp(t - i*step) * exp(-i*step / decay)
    ri, ro = N.new("GeometryNodeRepeatInput"), N.new("GeometryNodeRepeatOutput")
    ri.pair_with_output(ro)
    ro.repeat_items.new("FLOAT", "Peak")
    ri.inputs["Iterations"].default_value = SAMPLES
    age = op("MULTIPLY", ri.outputs["Iteration"], step)
    sample_t = op("SUBTRACT", t, age)
    ss = N.new("GeometryNodeSampleSoundFrequencies")
    L.new(g["Sound"], ss.inputs["Sound"])
    L.new(sample_t, ss.inputs["Time"])
    L.new(low, ss.inputs["Low"])
    L.new(high, ss.inputs["High"])
    weight = op("EXPONENT", op("DIVIDE", age, op("MULTIPLY", decay, -1.0)))
    # 0 before the audio starts: by sample time, and by frame (covers frame 0's missing fps)
    gate = op("MULTIPLY", op("SUBTRACT", 1.0, op("LESS_THAN", sample_t, 0.0)),
              op("SUBTRACT", 1.0, op("LESS_THAN", st.outputs["Frame"], g["Start Frame"])))
    weighted = op("MULTIPLY", op("MULTIPLY", ss.outputs["Amplitude"], weight), gate)
    L.new(op("MAXIMUM", ri.outputs["Peak"], weighted), ro.inputs["Peak"])

    level = op("MULTIPLY", op("MAXIMUM", op("SUBTRACT", ro.outputs["Peak"], g["Threshold"]), 0.0), g["Gain"])
    mix = N.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    L.new(level, mix.inputs[0])         # Factor (clamped to 0..1)
    L.new(g["Color A"], mix.inputs[6])  # A_Color
    L.new(g["Color B"], mix.inputs[7])  # B_Color

    realize = N.new("GeometryNodeRealizeInstances")
    L.new(g["Geometry"], realize.inputs[0])
    out = realize.outputs[0]
    for name, data_type, value in (
        ("ar_level", "FLOAT", level),
        ("ar_color", "FLOAT_COLOR", mix.outputs[2]),
        ("ar_tint", "FLOAT", g["Use Colors"]),
    ):
        store = N.new("GeometryNodeStoreNamedAttribute")
        store.data_type = data_type
        store.domain = "POINT"
        store.inputs["Name"].default_value = name
        L.new(out, store.inputs["Geometry"])
        L.new(value, store.inputs["Value"])
        out = store.outputs["Geometry"]
    L.new(out, go.inputs["Geometry"])

    for i, n in enumerate(N):  # ponytail: one row so the tree is at least untangled if opened
        n.location = (i * 180, 0)
    return ng
```

- [ ] **Step 4: Run all suites.** Expected: nodes 13/13. materials, core, ui and render unchanged and green.

- [ ] **Step 5: Commit:** `feat: ripple inputs, beat length node, versioned group migration`

---

### Task 3: Bake tempo into the group

**Files:**
- Modify: `afterglow/tempo.py` (add `bake`)
- Modify: `afterglow/props.py` (re-bake on sound, start frame or ripple changes; new props)
- Test: `tests/test_tempo.py` (append)

**Interfaces:**
- Consumes: `tempo.tempo_curve`, `nodes.get_group`, `nodes.BEAT_NODE`
- Produces:
  - `tempo.bake(scene) -> list[(seconds, bpm)]`. It raises `ValueError("Pick a sound file first")` or `ValueError("Tempo analysis needs the sound file on disk")`.
  - New scene properties `scene.afterglow.ripple` (Bool) and `scene.afterglow.beats_per_sweep` (Float, default 1.0, min 0.05, max 16).

- [ ] **Step 1: Append the failing tests** to `tests/test_tempo.py`, before `helpers.run(globals())`:

```python
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
```

- [ ] **Step 2: Run, and confirm they fail** (`AttributeError: ... has no attribute 'bake'` and `'ripple'`).

- [ ] **Step 3: Add `bake` to `afterglow/tempo.py`.** Extend the imports at the top to:

```python
import aud
import bpy
import numpy as np

from . import nodes
```

Then append:

```python
_cache = {}  # absolute path -> curve; re-keying after a Start Frame change skips re-analysis


def bake(scene):
    """Analyze the scene's sound and key the group's Beat Length (seconds per beat)."""
    s = scene.afterglow
    if s.sound is None:
        raise ValueError("Pick a sound file first")
    path = bpy.path.abspath(s.sound.filepath, library=s.sound.library)
    curve = _cache.get(path)
    if curve is None:
        try:
            clip = aud.Sound(path)
            samples, rate = np.asarray(clip.data()), clip.specs[0]
        except Exception:
            raise ValueError("Tempo analysis needs the sound file on disk") from None
        curve = _cache[path] = tempo_curve(samples, rate)
    group = nodes.get_group()
    socket = group.nodes[nodes.BEAT_NODE].outputs[0]
    fps = scene.render.fps / scene.render.fps_base
    group.animation_data_clear()  # the group carries no other animation
    for t, bpm in curve:
        socket.default_value = 60.0 / bpm
        socket.keyframe_insert("default_value", frame=s.start_frame + t * fps)
    return curve
```

- [ ] **Step 4: Update `afterglow/props.py`.** Replace the file with:

```python
import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, PointerProperty

from . import core, tempo


def _sync(self, context):
    core.sync(context.scene)


def _retempo(self, context):
    """Sound, Start Frame or Ripple changed: sync, and re-key the tempo curve if rippling."""
    core.sync(context.scene)
    if context.scene.afterglow.ripple:
        try:
            tempo.bake(context.scene)
        except ValueError:
            pass  # Apply reports it; keep the previous curve


class AfterglowScene(bpy.types.PropertyGroup):
    collection: PointerProperty(type=bpy.types.Collection, name="Collection",
                                description="Every emissive object in here reacts", update=_sync)
    sound: PointerProperty(type=bpy.types.Sound, name="Sound", update=_retempo)
    start_frame: IntProperty(name="Start Frame", default=1,
                             description="Frame where the audio starts playing", update=_retempo)
    ripple: BoolProperty(name="Ripple", description="Every kick sweeps across the collection by "
                         "position; the sweep follows the song's tempo", update=_retempo)
    beats_per_sweep: FloatProperty(name="Beats per Sweep", default=1.0, min=0.05, max=16.0,
                                   description="How many beats one sweep takes to cross the collection",
                                   update=_sync)


class AfterglowObject(bpy.types.PropertyGroup):
    locked: BoolProperty(name="Lock Band", description="Keep this object's frequency band when re-spreading")


CLASSES = (AfterglowScene, AfterglowObject)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.afterglow = PointerProperty(type=AfterglowScene)
    bpy.types.Object.afterglow = PointerProperty(type=AfterglowObject)


def unregister():
    del bpy.types.Object.afterglow
    del bpy.types.Scene.afterglow
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
```

- [ ] **Step 5: Run all suites.** Expected: tempo 9/9, everything else green.

- [ ] **Step 6: Commit:** `feat: bake tempo curve into Beat Length; re-key on sound/start/ripple change`

---

### Task 4: Placement, sync, Apply and panel

**Files:**
- Modify: `afterglow/core.py`, `afterglow/ui.py`
- Test: `tests/test_core.py`, `tests/test_ui.py` (append)

**Interfaces:**
- Consumes: `tempo.bake`, `nodes.set_input("Ripple Position" | "Ripple" | "Beats per Sweep")`
- Produces: `core.place(objs)`. `apply()` calls it after `respread`, and `sync()` also writes Ripple and Beats per Sweep.

- [ ] **Step 1: Append the failing tests.**

To `tests/test_core.py`:

```python
def ripple_position(name):
    return nodes.get_input(core.find_modifier(bpy.data.objects[name]), "Ripple Position")


def test_place_orders_by_location_on_longest_axis():
    scene, _ = stage()
    for name, y in (("a_bass", 10.0), ("b_mid", 0.0), ("c_treble", 5.0)):
        bpy.data.objects[name].location = (0.3, y, 0.0)
    core.apply(scene)
    assert [round(ripple_position(n), 3) for n in ("a_bass", "b_mid", "c_treble")] == [1.0, 0.0, 0.5]


def test_place_coincident_objects_all_zero():
    scene, _ = stage()
    core.apply(scene)
    assert all(ripple_position(n) == 0.0 for n in ("a_bass", "b_mid", "c_treble"))


def test_sync_ripple_settings():
    scene, _ = stage()
    core.apply(scene)
    scene.afterglow.ripple = True
    scene.afterglow.beats_per_sweep = 2.0
    for name in ("a_bass", "b_mid", "c_treble"):
        mod = core.find_modifier(bpy.data.objects[name])
        assert nodes.get_input(mod, "Ripple") is True
        assert nodes.get_input(mod, "Beats per Sweep") == 2.0
```

To `tests/test_ui.py`:

```python
def test_apply_reports_tempo_or_skip():
    stage()
    bpy.ops.afterglow.load_sound(filepath=tone_wav())
    assert bpy.ops.afterglow.apply() == {"FINISHED"}
    assert nodes.get_group().animation_data is not None
    bpy.context.scene.afterglow.sound.filepath = "//missing.wav"
    assert bpy.ops.afterglow.apply() == {"FINISHED"}  # tempo skipped is a warning, not an error
```

- [ ] **Step 2: Run, and confirm the new tests fail.** Positions are all 0, sync doesn't write Ripple, and Apply doesn't bake.

- [ ] **Step 3: Edit `afterglow/core.py`.**

Add after `respread`:

```python
def place(objs):
    """Ripple Position 0..1 along the axis where the objects' origins spread the most."""
    if not objs:
        return
    pts = [o.matrix_world.translation for o in objs]
    axis = max(range(3), key=lambda a: max(p[a] for p in pts) - min(p[a] for p in pts))
    lo = min(p[axis] for p in pts)
    span = max(p[axis] for p in pts) - lo
    for ob, p in zip(objs, pts):
        nodes.set_input(find_modifier(ob), "Ripple Position", (p[axis] - lo) / span if span > 1e-6 else 0.0)
```

In `apply`, replace:

```python
    respread(applied)
    setup_playback(scene)
```

with:

```python
    respread(applied)
    bpy.context.view_layer.update()  # matrix_world is stale for objects moved since the last eval
    place(applied)
    setup_playback(scene)
```

Also write the ripple settings in `apply`'s per-object block, right after `nodes.set_input(mod, "Start Frame", s.start_frame)`:

```python
        nodes.set_input(mod, "Ripple", s.ripple)
        nodes.set_input(mod, "Beats per Sweep", s.beats_per_sweep)
```

In `sync`, after `nodes.set_input(mod, "Start Frame", s.start_frame)`, add the same two lines (with `mod` as already named there).

- [ ] **Step 4: Edit `afterglow/ui.py`.**
  - Import: `from . import core, nodes, tempo`
  - In `AFTERGLOW_OT_apply.execute`, replace everything from `msg = f"{len(applied)} objects react"` up to the `return {"FINISHED"}` with:

```python
        msg = f"{len(applied)} objects react"
        if skipped:
            msg += f", {len(skipped)} skipped: no local emissive material"
        try:
            bpms = [b for _, b in tempo.bake(context.scene)]
            msg += f", tempo {min(bpms):.0f}-{max(bpms):.0f} BPM"
        except ValueError as e:
            self.report({"WARNING"}, f"Tempo analysis skipped: {e}")
        self.report({"INFO"}, msg)
        if outside:
            self.report({"WARNING"}, "Shared material also used outside the collection, these render dark: "
                        + ", ".join(o.name for o in outside))
        if linked:
            self.report({"WARNING"}, "Linked materials can't react, make them local: "
                        + ", ".join(m.name for m in linked))
        return {"FINISHED"}
```

  - In the panel's `draw`, after `col.prop(s, "start_frame")`, add:

```python
        row = col.row(align=True)
        row.prop(s, "ripple")
        sub = row.row(align=True)
        sub.active = s.ripple
        sub.prop(s, "beats_per_sweep", text="Beats")
```

- [ ] **Step 5: Run all six suites.** Expected all green: nodes 13, materials 7, core 22, ui 6, render 1, tempo 9.

- [ ] **Step 6: Bump the manifest to `version = "0.3.0"`, rebuild the zip, and commit:**

```bash
"$B" --factory-startup --command extension build --source-dir afterglow --output-dir dist
git add afterglow tests && git commit -m "feat: tempo ripple placement, sync, Apply bake and panel controls"
```

- [ ] **Step 7: Real-song check.** Apply to 8 planes along X with the user's song and Ripple on. Sample `ar_level` per object at 4 consecutive frames during a 122 BPM section. Confirm the object at position 0 peaks before the object at position 1, about one beat apart in total, and record the result in the ledger.
