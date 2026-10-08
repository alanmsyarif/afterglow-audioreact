# Afterglow Fast Playback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Playback that never evaluates the slow `Sample Sound Frequencies` node. A numpy bake keys each object's held peak per frame, and the node group switches to that keyed value.

**Architecture:**
- Group v4 adds `Use Baked` (scene-wide) and `Baked Peak` (per object).
- A lazy `Switch` picks the baked value over the live Repeat-Zone peak, so the sound node isn't evaluated.
- `bake.py` reproduces the node's amplitude with numpy (verified correlation 0.9992). It applies the same peak-hold, delay and ripple lag, and writes one key per frame onto `Baked Peak`.

**Tech Stack:** Blender 5.2 bpy, aud, numpy. Headless assert tests, as before.

**Spec:** `docs/superpowers/specs/2026-10-08-afterglow-fast-playback-design.md`

## Global Constraints

- Node amplitude formula: mix to mono, take a 4096-sample symmetric Hann window (`np.hanning(4096)`) centred on the time, then `sum(|rfft|[low <= f <= high]) * 2 / 4096`.
- Peak hold matches the node exactly: `SAMPLES = 8`, `step = Decay / 4`, `weight = exp(-i*step / max(Decay, 1e-4))`, amplitude 0 when the sample time is below 0.
- Append-only inputs: `Use Baked` (Bool), then `Baked Peak` (Float). `VERSION = 4`.
- Keep the earlier invariants: `set_input` tags the object; `sync` never pushes a missing-file sound; tests' `at()` steps through a neighbour frame.
- Test command (Git Bash, repo root): `B="/c/Program Files/Blender Foundation/Blender 5.2/blender.exe"; "$B" -b --factory-startup --python-exit-code 1 --python tests/test_<name>.py`

## Verified facts (2026-10-08)

- A GN `Switch` (`input_type = "FLOAT"`, inputs `Switch` / `False` / `True`) with Switch = True skips the Sample Sound branch: 0.12 ms/frame, against 62 ms.
- A modifier input can be keyframed with `nodes._socket(mod, name).keyframe_insert("value", frame=f)`. Its path is `modifiers["<mod>"].properties.inputs.<ident>.value`.
- F-curves are found with `bpy_extras.anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot).fcurves`. Moving `kp.co.x` and calling `fc.update()` works.
- Changing a value inside the group from Python without re-tagging doesn't re-evaluate. Benchmarks must rebuild, not poke the group.

## Review Focus

1. **The user's own object animation (for example keyed transforms):** bake and Remove must leave it untouched. Pinned by `test_remove_keeps_user_animation` (Task 3).
2. **Baked and live must look the same, so Calibrate values carry over.** Pinned by `test_baked_matches_live` and `test_baked_ripple_matches_live` (Task 2).
3. **Frames before Start Frame, or after the song ends, must be dark when baked.** Pinned by `test_before_start_dark_when_baked` and `test_after_song_dark_when_baked` (Task 2).
4. **Editing a baked setting (Decay or a band) in the modifier panel must be flagged.** Pinned by `test_outdated_after_edit` (Task 2).
5. **A v3 file (the current release) must migrate to v4 keeping its values.** Covered by the existing `test_real_v1_group_migrates` and `test_old_group_migrates`, which use VERSION generically (Task 1).

---

### Task 1: Node group v4 (baked switch)

**Files:** Modify `afterglow/nodes.py`. Test in `tests/test_nodes.py`.

**Interfaces:**
- Produces: inputs `Use Baked` (scene-wide, in `SCENE_INPUTS`) and `Baked Peak` (per object, excluded from `OBJECT_INPUTS`), and `VERSION = 4`.

- [ ] **Step 1: Append the failing test** to `tests/test_nodes.py`, before `helpers.run(globals())`:

```python
def test_baked_peak_replaces_live_sampling():
    ob = reactive("baked", BASS, {"Use Baked": True, "Baked Peak": 0.7, "Decay": 0.0})
    at(37)  # 1 kHz playing, so live bass would be ~0; baked says 0.7
    assert math.isclose(attr(ob), 0.7, abs_tol=1e-4), attr(ob)
```

- [ ] **Step 2: Run, and confirm it fails** with `KeyError` on "Use Baked".

- [ ] **Step 3: Edit `afterglow/nodes.py`.**
  - Change `VERSION = 3` to `VERSION = 4`.
  - Append these to `INPUTS`, after `("Ripple Threshold", ...)`:

```python
    ("Use Baked", "NodeSocketBool", False),    # Fast Playback: read Baked Peak, skip the sound node
    ("Baked Peak", "NodeSocketFloat", 0.0),    # keyed per frame by bake.py
```

  - Change `SCENE_INPUTS` to include `"Use Baked"`:

```python
SCENE_INPUTS = {"Sound", "Start Frame", "Ripple", "Beats per Sweep", "Use Baked"}
```

  - Change `OBJECT_INPUTS` to exclude `"Baked Peak"`:

```python
OBJECT_INPUTS = [name for name, _, _ in INPUTS
                 if name not in SCENE_INPUTS | {"Ripple Position", "Baked Peak"} | set(RIPPLE_PAIR.values())]
```

  - In `_build`, replace the line `level = op("MULTIPLY", op("MAXIMUM", op("SUBTRACT", ro.outputs["Peak"], threshold), 0.0), gain)` with:

```python
    # Fast Playback: a lazy Switch, so the Sample Sound branch isn't evaluated when baked.
    baked = N.new("GeometryNodeSwitch")
    baked.input_type = "FLOAT"
    L.new(g["Use Baked"], baked.inputs["Switch"])
    L.new(ro.outputs["Peak"], baked.inputs["False"])
    L.new(g["Baked Peak"], baked.inputs["True"])
    level = op("MULTIPLY", op("MAXIMUM", op("SUBTRACT", baked.outputs[0], threshold), 0.0), gain)
```

- [ ] **Step 4: Run all suites.** Expected: nodes 16/16, and everything else green.
- [ ] **Step 5: Commit:** `feat: group v4 with Use Baked switch and Baked Peak input`

---

### Task 2: numpy bake engine

**Files:** Create `afterglow/bake.py`. Test in `tests/test_bake.py`.

**Interfaces:**
- Consumes: `core._managed`, `core.find_modifier`, `core.sound_ok`, `nodes.get_input/_socket/KICK/SAMPLES/BEAT_NODE/get_group`, `tempo._fcurve`
- Produces:
  - `bake.band_series(mono, rate, bands) -> (times, ndarray[len(bands), hops])`
  - `bake.held_peak(times, amp, t, decay) -> ndarray`
  - `bake.bake_all(scene) -> int`, which raises `ValueError`
  - `bake.signature(mod, scene) -> str`
  - `bake.outdated(scene) -> list[Object]`
  - `bake.unkey(ob, mod)`
  - `bake.SIG = "afterglow_bake"`
- Note: `bake_all` doesn't set `Use Baked`. Task 3's sync writes it from `scene.afterglow.fast_playback`. Until then, the tests here set it directly.

- [ ] **Step 1: Write the failing tests** in `tests/test_bake.py`:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aud  # noqa: E402
import bpy  # noqa: E402
import numpy as np  # noqa: E402
import helpers  # noqa: E402
from helpers import at, attr, emission_material, plane, sound  # noqa: E402
from afterglow import bake, core, nodes  # noqa: E402

NAMES = ("a_bass", "b_mid", "c_treble")
FRAMES = (13, 31, 37, 49, 61)


def stage():
    scene = bpy.context.scene
    coll = bpy.data.collections.new("Lights")
    scene.collection.children.link(coll)
    for i, name in enumerate(NAMES):
        plane(name, coll, emission_material("mat_" + name)).location.x = 5.0 * i
    scene.afterglow.collection = coll
    scene.afterglow.sound = sound()
    core.apply(scene)
    return scene


def use_baked(scene, on=True):
    for ob in scene.objects:
        mod = core.find_modifier(ob)
        if mod:
            nodes.set_input(mod, "Use Baked", on)


def levels():
    out = {}
    for f in FRAMES:
        at(f)
        for n in NAMES:
            out[(n, f)] = attr(bpy.data.objects[n])
    return out


def assert_close(live, baked):
    for key in live:
        a, b = live[key], baked[key]
        assert abs(a - b) <= max(0.03, 0.05 * abs(a)), (key, a, b)


def test_band_series_matches_node():
    scene = stage()
    for n in NAMES:
        nodes.set_input(core.find_modifier(bpy.data.objects[n]), "Decay", 0.0)
    clip = aud.Sound(helpers.tone_wav())
    mono = np.asarray(clip.data()).mean(axis=1)
    bands = [tuple(nodes.get_input(core.find_modifier(bpy.data.objects[n]), k) for k in ("Low", "High")) for n in NAMES]
    times, series = bake.band_series(mono, clip.specs[0], bands)
    for f in FRAMES:
        at(f)
        for b, n in enumerate(NAMES):
            live = attr(bpy.data.objects[n])
            mine = float(np.interp((f - 1) / 24.0, times, series[b]))
            assert abs(live - mine) <= max(0.02, 0.05 * live), (n, f, live, mine)


def test_baked_matches_live():
    scene = stage()
    live = levels()
    assert bake.bake_all(scene) == 3
    use_baked(scene)
    assert_close(live, levels())


def test_baked_ripple_matches_live():
    scene = stage()
    scene.afterglow.ripple = True  # also bakes a tempo curve; both paths read the same curve
    live = levels()
    bake.bake_all(scene)
    use_baked(scene)
    assert_close(live, levels())


def test_before_start_dark_when_baked():
    scene = stage()
    scene.afterglow.start_frame = 25
    bake.bake_all(scene)
    use_baked(scene)
    at(10)
    assert all(attr(bpy.data.objects[n]) == 0.0 for n in NAMES)


def test_after_song_dark_when_baked():
    scene = stage()
    bake.bake_all(scene)
    use_baked(scene)
    at(1 + 3 * 24 + 3 * 24)  # 3 s past the 3 s song
    assert all(attr(bpy.data.objects[n]) < 1e-4 for n in NAMES)


def test_outdated_after_edit():
    scene = stage()
    bake.bake_all(scene)
    assert bake.outdated(scene) == []
    a = bpy.data.objects["a_bass"]
    nodes.set_input(core.find_modifier(a), "Decay", 0.4)
    assert bake.outdated(scene) == [a]
    bake.bake_all(scene)
    assert bake.outdated(scene) == []


def test_rebake_replaces_keys():
    scene = stage()
    bake.bake_all(scene)
    bake.bake_all(scene)
    ob = bpy.data.objects["a_bass"]
    paths = [fc.data_path for fc in helpers.fcurves(ob)]
    assert paths.count(nodes._socket(core.find_modifier(ob), "Baked Peak").path_from_id("value")) == 1, paths


helpers.run(globals())
```

Also append to `tests/helpers.py`:

```python
def fcurves(idblock):
    """F-curves of an ID's active action slot (5.x layered actions)."""
    from bpy_extras import anim_utils
    ad = idblock.animation_data
    if not (ad and ad.action):
        return []
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    return list(bag.fcurves) if bag else []
```

`outdated()` doesn't look at Fast Playback; the panel decides whether to show it (Task 3).

- [ ] **Step 2: Run, and confirm it fails** with `ImportError: cannot import name 'bake'`.

- [ ] **Step 3: Create `afterglow/bake.py`:**

```python
"""Fast Playback: precompute each object's held peak per frame with numpy and key it onto the
modifier's Baked Peak input, so playback never evaluates the slow Sample Sound node."""
import aud
import bpy
import numpy as np
from bpy_extras import anim_utils

from . import core, nodes, tempo

FFT, HOP, CHUNK = 4096, 256, 256  # FFT and Hann window match the node defaults
TAIL_S = 2.0  # keep keying past the song end so decay/delay tails finish
SIG = "afterglow_bake"


def band_series(mono, rate, bands):
    """Node-equivalent band amplitude every HOP samples: Hann window centred on each hop,
    sum(|rfft|) over low <= f <= high, times 2 / FFT. Returns (times, array[bands, hops])."""
    padded = np.pad(mono.astype(np.float32), (FFT // 2, FFT // 2))
    hops = 1 + len(mono) // HOP
    freqs = np.fft.rfftfreq(FFT, 1 / rate)
    edges = [(np.searchsorted(freqs, lo, "left"), np.searchsorted(freqs, hi, "right")) for lo, hi in bands]
    window = np.hanning(FFT).astype(np.float32)
    out = np.zeros((len(bands), hops), np.float32)
    for c in range(0, hops, CHUNK):
        k = np.arange(c, min(c + CHUNK, hops))
        mag = np.abs(np.fft.rfft(padded[np.arange(FFT)[None, :] + HOP * k[:, None]] * window, axis=1))
        cum = np.concatenate([np.zeros((len(k), 1), np.float32), np.cumsum(mag, axis=1, dtype=np.float32)], axis=1)
        for b, (i0, i1) in enumerate(edges):
            out[b, k] = (cum[:, i1] - cum[:, i0]) * 2 / FFT
    return np.arange(hops) * HOP / rate, out


def held_peak(times, amp, t, decay):
    """The node's Repeat-Zone peak hold, evaluated at audio times t (array)."""
    step, d = decay / 4, max(decay, 1e-4)
    peak = np.zeros_like(t)
    for i in range(nodes.SAMPLES):
        ts = t - i * step
        a = np.interp(ts, times, amp, right=0.0)
        a[ts < 0] = 0.0
        peak = np.maximum(peak, a * np.exp(-i * step / d))
    return peak


def signature(mod, scene):
    s = scene.afterglow
    vals = [nodes.get_input(mod, n) for n in
            ("Low", "High", "Decay", "Delay", "Ripple", "Ripple Position", "Beats per Sweep", "Start Frame")]
    vals = [round(v, 5) if isinstance(v, float) else v for v in vals]
    return repr(vals + [s.sound.filepath if s.sound else ""])


def outdated(scene):
    out = []
    for ob in core._managed(scene):
        mod = core.find_modifier(ob)
        if mod and ob.get(SIG) != signature(mod, scene):
            out.append(ob)
    return sorted(out, key=lambda o: o.name)


def _path(mod):
    return nodes._socket(mod, "Baked Peak").path_from_id("value")


def _bake_fcurve(ob, mod):
    ad = ob.animation_data
    if not (ad and ad.action):
        return None, None
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    path = _path(mod)
    return bag, bag and next((fc for fc in bag.fcurves if fc.data_path == path), None)


def unkey(ob, mod):
    """Delete only the Baked Peak F-curve; the object's other animation stays."""
    bag, fc = _bake_fcurve(ob, mod)
    if fc:
        bag.fcurves.remove(fc)
    ob.pop(SIG, None)


def _write(ob, mod, frames, values):
    unkey(ob, mod)
    sock = nodes._socket(mod, "Baked Peak")
    sock.value = float(values[0])
    sock.keyframe_insert("value", frame=float(frames[0]))
    _, fc = _bake_fcurve(ob, mod)
    fc.keyframe_points.add(len(frames) - 1)
    co = np.empty(2 * len(frames), np.float32)
    co[0::2], co[1::2] = frames, values
    fc.keyframe_points.foreach_set("co", co)
    fc.update()


def _beat_lengths(frames):
    group = nodes.get_group()
    fc = tempo._fcurve(group)
    if not fc:
        return np.full(len(frames), group.nodes[nodes.BEAT_NODE].outputs[0].default_value)
    return np.array([fc.evaluate(float(f)) for f in frames])


def bake_all(scene):
    """Bake every object carrying an Afterglow modifier. Returns how many were baked."""
    s = scene.afterglow
    if s.sound is None:
        raise ValueError("Pick a sound file first")
    if not core.sound_ok(s.sound):
        raise ValueError("Sound file not found")
    objs = sorted((o for o in core._managed(scene) if core.find_modifier(o)), key=lambda o: o.name)
    if not objs:
        return 0
    try:
        clip = aud.Sound(bpy.path.abspath(s.sound.filepath, library=s.sound.library))
        data, rate = np.asarray(clip.data()), clip.specs[0]
    except Exception:
        raise ValueError("Fast Playback needs the sound file on disk") from None
    mono = data.mean(axis=1) if data.ndim == 2 else data
    fps = scene.render.fps / scene.render.fps_base
    end = s.start_frame + int(np.ceil((len(mono) / rate + TAIL_S) * fps))
    frames = np.arange(s.start_frame - 1, end + 1, dtype=np.float64)
    mods = [core.find_modifier(o) for o in objs]

    def band_of(m):
        return nodes.KICK if nodes.get_input(m, "Ripple") else (nodes.get_input(m, "Low"), nodes.get_input(m, "High"))

    bands = sorted({band_of(m) for m in mods})
    times, series = band_series(mono, rate, bands)
    beat = _beat_lengths(frames)
    for ob, m in zip(objs, mods):
        def g(name):
            return nodes.get_input(m, name)
        lag = float(g("Ripple")) * g("Ripple Position") * g("Beats per Sweep") * beat
        t = (frames - g("Start Frame")) / fps - g("Delay") - lag
        _write(ob, m, frames, held_peak(times, series[bands.index(band_of(m))], t, g("Decay")))
        ob[SIG] = signature(m, scene)
    return len(objs)
```

- [ ] **Step 4: Run, and confirm it passes:** `7/7`, with the other suites green.
  - If `test_baked_matches_live` misses only at frame 31 (decay tail at a tone boundary) by under 0.05, check the node's peak against `held_peak`'s sample times before loosening anything.
  - The hop interpolation (5.8 ms) is the only expected source of error.
- [ ] **Step 5: Commit:** `feat: numpy bake engine reproducing the node's amplitude and peak hold`

---

### Task 3: Wiring (property, sync, operators, panel, Remove)

**Files:** Modify `afterglow/props.py`, `afterglow/core.py` and `afterglow/ui.py`. Tests in `tests/test_bake.py` and `tests/test_ui.py`.

**Interfaces:**
- Consumes: `bake.bake_all/outdated/unkey`.
- Produces: `scene.afterglow.fast_playback` and operator `afterglow.bake`. Sync writes `Use Baked`, and Remove unkeys.

- [ ] **Step 1: Append the failing tests.**

To `tests/test_bake.py`, before `helpers.run(globals())`:

```python
def test_fast_playback_toggle_bakes_and_switches():
    scene = stage()
    scene.afterglow.fast_playback = True
    for n in NAMES:
        mod = core.find_modifier(bpy.data.objects[n])
        assert nodes.get_input(mod, "Use Baked") is True
        assert bpy.data.objects[n].get(bake.SIG)


def test_baked_playback_much_faster():
    import time
    scene = stage()

    def per_frame():
        scene.frame_set(99)
        t0 = time.perf_counter()
        for f in range(100, 130):
            scene.frame_set(f)
            for n in NAMES:
                bpy.data.objects[n].evaluated_get(bpy.context.evaluated_depsgraph_get())
        return time.perf_counter() - t0

    live = per_frame()
    scene.afterglow.fast_playback = True
    fast = per_frame()
    assert fast * 20 < live, (live, fast)


def test_remove_keeps_user_animation():
    scene = stage()
    a = bpy.data.objects["a_bass"]
    a.keyframe_insert("location", frame=1)
    scene.afterglow.fast_playback = True
    core.remove(scene)
    paths = {fc.data_path for fc in helpers.fcurves(a)}
    assert "location" in paths and not any("Socket" in p for p in paths), paths
    assert bake.SIG not in a
```

To `tests/test_ui.py`, before `helpers.run(globals())`:

```python
def test_bake_operator_and_outdated_panel_data():
    stage()
    bpy.ops.afterglow.load_sound(filepath=tone_wav())
    bpy.ops.afterglow.apply()
    bpy.context.scene.afterglow.fast_playback = True
    assert bpy.ops.afterglow.bake() == {"FINISHED"}
    from afterglow import bake
    assert bake.outdated(bpy.context.scene) == []
```

- [ ] **Step 2: Run, and confirm they fail** with `AttributeError: ... 'fast_playback'`.

- [ ] **Step 3: Edit `afterglow/props.py`.**
  - Change the import to `from . import bake, core, tempo`.
  - Add after `_sync`:

```python
def _rebake(scene):
    if scene.afterglow.fast_playback:
        try:
            bake.bake_all(scene)
        except ValueError:
            pass  # the panel's Re-bake button reports it


def _sync_rebake(self, context):
    core.sync(context.scene)
    _rebake(context.scene)


def _fast(self, context):
    core.sync(context.scene)  # writes Use Baked
    _rebake(context.scene)
```

  - In `_retempo`, add `_rebake(context.scene)` as the last line (after the try/except).
  - Change the `beats_per_sweep` property's `update=_sync` to `update=_sync_rebake`.
  - Add the property after `beats_per_sweep`:

```python
    fast_playback: BoolProperty(name="Fast Playback", description="Precompute the reaction so playback "
                                "never evaluates the slow sound node. Re-bake after editing bands, "
                                "Decay or Delay in the modifier panel", update=_fast)
```

- [ ] **Step 4: Edit `afterglow/core.py`.**
  - In `apply`'s per-object block, after `nodes.set_input(mod, "Beats per Sweep", s.beats_per_sweep)`, add `nodes.set_input(mod, "Use Baked", s.fast_playback)`.
  - In `sync`, after `nodes.set_input(mod, "Beats per Sweep", s.beats_per_sweep)`, add the same line.
  - In `remove`, replace:

```python
    for ob in _managed(scene):
        mod = find_modifier(ob)
        if mod:
            ob.modifiers.remove(mod)
```

with:

```python
    from . import bake  # bake imports core
    for ob in _managed(scene):
        mod = find_modifier(ob)
        if mod:
            bake.unkey(ob, mod)
            ob.modifiers.remove(mod)
```

- [ ] **Step 5: Edit `afterglow/ui.py`.**
  - Change the import to `from . import bake, core, nodes, tempo`.
  - In `AFTERGLOW_OT_apply.execute`, right before `self.report({"INFO"}, msg)`, insert:

```python
        if context.scene.afterglow.fast_playback:
            try:
                bake.bake_all(context.scene)
                msg += ", baked"
            except ValueError as e:
                self.report({"WARNING"}, f"Bake skipped: {e}")
```

  - In `AFTERGLOW_OT_respread.execute`, before `return {"FINISHED"}`, insert:

```python
        if context.scene.afterglow.fast_playback:
            try:
                bake.bake_all(context.scene)
            except ValueError as e:
                self.report({"WARNING"}, f"Bake skipped: {e}")
```

  - Add the operator class before `AFTERGLOW_OT_remove`:

```python
class AFTERGLOW_OT_bake(bpy.types.Operator):
    bl_idname = "afterglow.bake"
    bl_label = "Re-bake"
    bl_description = "Recompute the Fast Playback bake for every Afterglow object"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        import time
        t0 = time.perf_counter()
        try:
            n = bake.bake_all(context.scene)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Baked {n} objects in {time.perf_counter() - t0:.1f} s")
        return {"FINISHED"}
```

  - In the panel, after the Ripple row (after `sub.prop(s, "beats_per_sweep", text="Beats")`), insert:

```python
        row = col.row(align=True)
        row.prop(s, "fast_playback")
        stale = bake.outdated(context.scene) if s.fast_playback else []
        if stale:
            warn = col.row(align=True)
            warn.alert = True
            warn.label(text=f"Bake outdated ({len(stale)} objects)", icon="ERROR")
            warn.operator("afterglow.bake", text="", icon="FILE_REFRESH")
```

  - Add `AFTERGLOW_OT_bake` to `CLASSES`, right before `AFTERGLOW_OT_remove`.

- [ ] **Step 6: Run all seven suites.** Expected all green: nodes 16, materials 7, core 26, ui 7, render 1, tempo 10, bake 10.
- [ ] **Step 7: Bump the manifest to `0.4.0`, rebuild `dist/afterglow-0.4.0.zip`, and commit:** `feat: Fast Playback toggle, Re-bake operator, outdated warning`
- [ ] **Step 8: Real-song check.**
  - 8 planes, Ripple on, Fast Playback on, with the user's song. Measure the bake time and ms/frame over 48 sequential frames, against live.
  - Compare live and baked levels at 20 frames.
  - Record the results in the ledger.
