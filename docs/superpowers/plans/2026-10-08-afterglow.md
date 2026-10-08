# Afterglow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Blender 5.2 extension that makes every emissive object in one collection react to an audio file, each object on its own frequency band and with its own settings.

**Architecture:** A shared geometry node group (`Afterglow AudioReact`) is added as a modifier on every target object. It samples Blender 5.2's `Sample Sound Frequencies` node inside a Repeat Zone (peak-hold with exponential decay) and writes `ar_level`, `ar_color` and `ar_tint` point attributes. Each emission input in the object's materials is patched to read those attributes through Attribute nodes. The patch is fully reversible.

**Tech Stack:** Blender 5.2.0 LTS Python API (bpy). No third-party packages. Tests are assert-based scripts run headless with `blender -b`.

**Spec:** `docs/superpowers/specs/2026-10-08-afterglow-design.md`

## Global Constraints

- Blender 5.2.0 minimum (`blender_version_min = "5.2.0"`). Pure Python, no dependencies.
- Node group name: `Afterglow AudioReact`. Modifier name: `Afterglow`. Attribute names: `ar_level`, `ar_color`, `ar_tint`, all on the point domain.
- Auto-spread: log-spaced from 20 Hz to 16000 Hz, objects sorted by name, locked objects skipped.
- Smoothing: 8 samples per frame, step = `Decay / 4`, weight = `exp(-age / max(Decay, 1e-4))`.
- Supported object types: `MESH`, `CURVE`, `FONT`. Everything else is skipped silently.
- Material patch nodes carry the custom property `afterglow = 1` and are labelled "Afterglow".
- Modifier inputs in 5.2 are set with `getattr(mod.properties.inputs, socket_identifier).value = v`. **`mod[identifier] = v` raises TypeError in 5.2.** (Verified 2026-10-08.)
- All work is under `C:\Users\user\Documents\afterglow\`. The extension source is the `afterglow/` package, and tests are in `tests/`.
- Test command, run from the repo root in Git Bash:
  ```bash
  B="/c/Program Files/Blender Foundation/Blender 5.2/blender.exe"
  "$B" -b --factory-startup --python-exit-code 1 --python tests/test_<name>.py
  ```
  `--python-exit-code 1` must come before `--python`. A failing test makes Blender exit 1.

## Verified facts (probed 2026-10-08, Blender 5.2.0 headless)

- `GeometryNodeSampleSoundFrequencies` inputs: `Sound`, `Time`, `All Channels`, `Channel`, `Low`, `High`, `FFT Size`, `Window Function`. Output: `Amplitude`.
- It evaluates headless and inside a Repeat Zone with a per-iteration time. A 0.61-amplitude sine yields about 0.64 in-band and about 5e-5 out-of-band.
- Scene Time `Seconds` = `Frame / fps` (frame 1 at 24 fps = 0.0417).
- Repeat Zone: `ri.pair_with_output(ro)`, `ro.repeat_items.new("FLOAT", name)`, `ri.outputs["Iteration"]`, `ri.inputs["Iterations"]`.
- `ShaderNodeMix` with `data_type = "RGBA"`: inputs `[0]` Factor, `[6]` A color, `[7]` B color; output `[2]` Result color. This is the same in geometry and shader trees.
- Store Named Attribute has the inputs `Geometry`, `Selection`, `Name` and `Value` (one `Value` socket for any data_type).
- Shader Attribute node outputs: `Color`, `Vector`, `Fac`, `Alpha`. A point attribute stored by the modifier reaches the Eevee render (level 0 renders black, 0.5 renders 0.663).
- Mesh, text (FONT) and curve-with-bevel objects all expose the attribute through `evaluated_get(dg).to_mesh().attributes`.
- Principled BSDF sockets: `Emission Color`, `Emission Strength`. Emission node sockets: `Color`, `Strength`.

## Review Focus

1. **Material shared with an object outside the collection.** That object has no attributes, so it renders dark. Apply must name it in a warning. Pinned in Task 3 by `test_shared_material_outside_reported`.
2. **Frames before the audio starts** (Start Frame > current frame, or Delay pushing sample time below 0). Level must be exactly 0, not audio-start bleed. Pinned in Task 1 by `test_delay_and_start_frame`.
3. **Re-apply after manual tweaks.** Locked bands survive, with no duplicate modifiers or double patches. Pinned in Task 3 by `test_reapply_no_duplicates` and `test_locked_band_survives`.
4. **Object with mixed emissive and non-emissive materials.** Only the emissive material is touched. Pinned in Task 3 by `test_multi_material_object`.
5. **User deletes a patched Emission node before Remove.** No crash, and all Afterglow nodes are still removed. Pinned in Task 2 by `test_unpatch_survives_deleted_target`.

## File structure

```
afterglow/                     repo root (git)
  .gitignore
  afterglow/                   extension package (what gets zipped)
    blender_manifest.toml      extension metadata
    __init__.py                register/unregister only
    nodes.py                   builds the node group; modifier input get/set/draw
    materials.py               patch/unpatch emission inputs
    core.py                    collection ops: targets, apply, respread, sync, remove
    props.py                   Scene/Object PropertyGroups + sync callback
    ui.py                      operators + N-panel
  tests/
    helpers.py                 path setup, fixtures, tiny runner
    test_nodes.py
    test_materials.py
    test_core.py
    test_ui.py
    test_render.py
```

---

### Task 1: Node group + test harness

**Files:**
- Create: `.gitignore`, `afterglow/blender_manifest.toml`, `afterglow/__init__.py`, `afterglow/nodes.py`
- Test: `tests/helpers.py`, `tests/test_nodes.py`

**Interfaces:**
- Produces (in `afterglow.nodes`):
  - `GROUP_NAME: str = "Afterglow AudioReact"`
  - `INPUTS: tuple[(name, socket_type, default)]`, `OBJECT_INPUTS: list[str]` (all inputs except Sound and Start Frame)
  - `get_group() -> bpy.types.GeometryNodeTree`, which builds the group on first call and reuses it afterwards
  - `set_input(mod, name: str, value)`, `get_input(mod, name: str)`, `draw_input(layout, mod, name: str)`
- Produces (in `tests/helpers.py`): `fresh()`, `run(namespace)`, `tone_wav() -> str`, `sound() -> bpy.types.Sound`, `plane(name, collection=None, material=None) -> Object`, `emission_material(name, strength=5.0, color=(1,0.5,0.2,1)) -> Material`, `attr(ob, name="ar_level")`, `at(frame)`
- Test audio `tone.wav`: 100 Hz for 0–1 s, 1 kHz for 1–2 s, 8 kHz for 2–3 s, amplitude 20000/32767. At 24 fps, frame 13 = 0.5 s, frame 37 = 1.5 s, frame 61 = 2.5 s.

- [ ] **Step 1: Write scaffolding and test helpers**

`.gitignore`:
```
__pycache__/
dist/
```

`afterglow/blender_manifest.toml`:
```toml
schema_version = "1.0.0"

id = "afterglow"
version = "0.1.0"
name = "Afterglow"
tagline = "Audio-reactive emission for every object in a collection"
maintainer = "alanmaulanasyarif@gmail.com"
type = "add-on"
blender_version_min = "5.2.0"
license = ["SPDX:GPL-3.0-or-later"]
tags = ["Animation", "Material"]

[build]
paths_exclude_pattern = ["__pycache__/"]
```

`afterglow/__init__.py`:
```python
def register():
    pass


def unregister():
    pass
```

`tests/helpers.py`:
```python
"""Shared fixtures and a tiny runner for headless Blender tests."""
import math
import os
import struct
import sys
import tempfile
import traceback
import wave

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import afterglow  # noqa: E402


def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if "afterglow" not in bpy.types.Scene.bl_rna.properties:
        afterglow.register()


def run(namespace):
    tests = [(k, v) for k, v in namespace.items() if k.startswith("test_") and callable(v)]
    failed = []
    for name, fn in tests:
        fresh()
        try:
            fn()
            print("PASS", name)
        except Exception:
            traceback.print_exc()
            print("FAIL", name)
            failed.append(name)
    print(f"{len(tests) - len(failed)}/{len(tests)} passed")
    if failed:
        raise RuntimeError(f"failed: {failed}")


def tone_wav():
    path = os.path.join(tempfile.gettempdir(), "afterglow_tone.wav")
    if not os.path.exists(path):
        rate = 44100
        frames = bytearray()
        for i in range(rate * 3):
            t = i / rate
            freq = 100 if t < 1 else (1000 if t < 2 else 8000)
            frames += struct.pack("<h", int(20000 * math.sin(2 * math.pi * freq * t)))
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(frames))
    return path


def sound():
    return bpy.data.sounds.load(tone_wav(), check_existing=True)


def plane(name, collection=None, material=None):
    me = bpy.data.meshes.new(name)
    me.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
    ob = bpy.data.objects.new(name, me)
    (collection or bpy.context.scene.collection).objects.link(ob)
    if material:
        me.materials.append(material)
    return ob


def emission_material(name, strength=5.0, color=(1.0, 0.5, 0.2, 1.0)):
    mat = bpy.data.materials.new(name)
    nt = mat.node_tree
    nt.nodes.clear()
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = strength
    em.inputs["Color"].default_value = color
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    return mat


def at(frame):
    bpy.context.scene.frame_set(frame)


def attr(ob, name="ar_level"):
    """Value of a point attribute on the first vertex of ob's evaluated mesh."""
    ev = ob.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    try:
        item = me.attributes[name].data[0]
        return tuple(item.color) if hasattr(item, "color") else item.value
    finally:
        ev.to_mesh_clear()
```

- [ ] **Step 2: Write the failing tests**

`tests/test_nodes.py`:
```python
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import helpers  # noqa: E402
from helpers import at, attr, plane, sound  # noqa: E402
from afterglow import nodes  # noqa: E402

BASS = (20.0, 184.0)
TREBLE = (1692.0, 16000.0)


def reactive(name, band, inputs=None, data=None):
    ob = bpy.data.objects.new(name, data) if data else plane(name)
    if data:
        bpy.context.scene.collection.objects.link(ob)
    mod = ob.modifiers.new("Afterglow", "NODES")
    mod.node_group = nodes.get_group()
    nodes.set_input(mod, "Sound", sound())
    nodes.set_input(mod, "Low", band[0])
    nodes.set_input(mod, "High", band[1])
    for k, v in (inputs or {}).items():
        nodes.set_input(mod, k, v)
    return ob


def test_group_has_inputs_and_is_reused():
    g = nodes.get_group()
    names = [i.name for i in g.interface.items_tree if i.in_out == "INPUT" and i.name != "Geometry"]
    assert names == [n for n, _, _ in nodes.INPUTS], names
    assert nodes.get_group() == g
    assert len([n for n in bpy.data.node_groups if n.name.startswith(nodes.GROUP_NAME)]) == 1


def test_set_get_roundtrip():
    ob = reactive("a", BASS, {"Decay": 0.4})
    mod = ob.modifiers[0]
    assert math.isclose(nodes.get_input(mod, "Decay"), 0.4, rel_tol=1e-6)
    assert nodes.get_input(mod, "Sound") == sound()


def test_bands_separate():
    bass, treble = reactive("bass", BASS), reactive("treble", TREBLE)
    at(13)
    assert attr(bass) > 0.3 and attr(treble) < 0.05, (attr(bass), attr(treble))
    at(61)
    assert attr(treble) > 0.3 and attr(bass) < 0.05, (attr(bass), attr(treble))


def test_decay_holds_after_tone_stops():
    held = reactive("held", BASS, {"Decay": 0.3})
    dry = reactive("dry", BASS, {"Decay": 0.0})
    at(31)  # 1.25 s: bass tone ended at 1.0 s
    assert attr(held) > 0.1, attr(held)
    assert attr(dry) < 0.02, attr(dry)


def test_threshold_and_gain():
    base = reactive("base", BASS)
    doubled = reactive("doubled", BASS, {"Gain": 2.0})
    gated = reactive("gated", BASS, {"Threshold": 1.0})
    at(13)
    assert math.isclose(attr(doubled), 2 * attr(base), rel_tol=1e-3)
    assert attr(gated) == 0.0


def test_delay_and_start_frame():
    delayed = reactive("delayed", BASS, {"Delay": 1.0})
    late = reactive("late", BASS, {"Start Frame": 25})
    at(37)  # 1.5 s scene time = 0.5 s into the audio for both
    assert attr(delayed) > 0.3 and attr(late) > 0.3, (attr(delayed), attr(late))
    at(13)  # sample time is negative for both: before the audio starts
    assert attr(delayed) == 0.0 and attr(late) == 0.0, (attr(delayed), attr(late))


def test_colors_and_tint():
    tinted = reactive("tinted", BASS, {"Use Colors": True, "Gain": 10.0,
                                       "Color A": (0.0, 0.0, 0.0, 1.0), "Color B": (1.0, 0.0, 0.0, 1.0)})
    plain = reactive("plain", BASS)
    at(13)
    assert all(math.isclose(a, b, abs_tol=1e-4) for a, b in zip(attr(tinted, "ar_color"), (1, 0, 0, 1)))
    assert attr(tinted, "ar_tint") == 1.0
    assert attr(plain, "ar_tint") == 0.0


def test_text_and_curve_get_attribute():
    txt = bpy.data.curves.new("t", "FONT")
    txt.body = "HI"
    crv = bpy.data.curves.new("c", "CURVE")
    crv.dimensions = "3D"
    crv.bevel_depth = 0.1
    spline = crv.splines.new("POLY")
    spline.points.add(1)
    spline.points[1].co = (1, 0, 0, 1)
    text_ob = reactive("text", BASS, data=txt)
    curve_ob = reactive("curve", BASS, data=crv)
    at(13)
    assert attr(text_ob) > 0.3 and attr(curve_ob) > 0.3


helpers.run(globals())
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `"$B" -b --factory-startup --python-exit-code 1 --python tests/test_nodes.py`
Expected: exit 1 with `ImportError: cannot import name 'nodes' from 'afterglow'`.

- [ ] **Step 4: Implement `afterglow/nodes.py`**

```python
"""The shared Afterglow geometry node group and modifier input helpers."""
import bpy

GROUP_NAME = "Afterglow AudioReact"
SAMPLES = 8  # ponytail: fixed smoothing sample count; expose it if long tails get steppy

# (name, socket type, default). Every float input is clamped to >= 0.
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
)
OBJECT_INPUTS = [name for name, _, _ in INPUTS if name not in {"Sound", "Start Frame"}]


def get_group():
    return bpy.data.node_groups.get(GROUP_NAME) or _build()


def _socket(mod, name):
    return getattr(mod.properties.inputs, mod.node_group.interface.items_tree[name].identifier)


def set_input(mod, name, value):
    _socket(mod, name).value = value


def get_input(mod, name):
    return _socket(mod, name).value


def draw_input(layout, mod, name):
    layout.prop(_socket(mod, name), "value", text=name)


def _build():
    ng = bpy.data.node_groups.new(GROUP_NAME, "GeometryNodeTree")
    ng.is_modifier = True
    it = ng.interface
    it.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    it.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    for name, socket_type, default in INPUTS:
        s = it.new_socket(name, in_out="INPUT", socket_type=socket_type)
        if default is not None:
            s.default_value = default
        if socket_type == "NodeSocketFloat":
            s.min_value = 0.0

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
    st = N.new("GeometryNodeInputSceneTime")
    # Geometry nodes can't read fps; Seconds / Frame recovers it.
    # ponytail: at frame 0 this is 0/0 = 0, so frame 0 samples the audio start
    spf = op("DIVIDE", st.outputs["Seconds"], st.outputs["Frame"])
    t = op("SUBTRACT", op("MULTIPLY", op("SUBTRACT", st.outputs["Frame"], g["Start Frame"]), spf), g["Delay"])
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
    L.new(g["Low"], ss.inputs["Low"])
    L.new(g["High"], ss.inputs["High"])
    weight = op("EXPONENT", op("DIVIDE", age, op("MULTIPLY", decay, -1.0)))
    gate = op("SUBTRACT", 1.0, op("LESS_THAN", sample_t, 0.0))  # 0 before the audio starts
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

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `"$B" -b --factory-startup --python-exit-code 1 --python tests/test_nodes.py`
Expected: `8/8 passed`, exit 0.

If `test_decay_holds_after_tone_stops` fails on `dry`, the FFT window (4096 samples ≈ 93 ms) is reaching back across the tone boundary. Move that check to frame 32 (1.29 s), not changing the node.

- [ ] **Step 6: Commit**

```bash
git add .gitignore afterglow tests
git commit -m "feat: AudioReact node group with decay, gain, threshold, delay, colors

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Material patcher

**Files:**
- Create: `afterglow/materials.py`
- Modify: `tests/helpers.py` (append `snapshot`)
- Test: `tests/test_materials.py`

**Interfaces:**
- Consumes: `helpers.emission_material`, `helpers.run`
- Produces (in `afterglow.materials`):
  - `TAG = "afterglow"`
  - `has_emission(mat) -> bool`: true for an Emission node, or for a Principled BSDF whose Emission Strength is linked or > 0
  - `is_patched(mat) -> bool`
  - `patch_material(mat) -> bool`: False if there's nothing to patch or the material is already patched
  - `unpatch_material(mat) -> bool`: False if the material isn't patched
- Produces (in `tests/helpers.py`): `snapshot(mat) -> (node_names, links, input_values)` for exact before/after comparison

- [ ] **Step 1: Add `snapshot` to `tests/helpers.py`** (append at the end)

```python
def snapshot(mat):
    """Node names, links, and unlinked input values; equal snapshots mean identical trees."""
    nt = mat.node_tree
    links = sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier)
                   for l in nt.links)
    values = {}
    for n in nt.nodes:
        for s in n.inputs:
            if s.is_linked or not hasattr(s, "default_value"):
                continue
            v = s.default_value
            if hasattr(v, "__len__") and not isinstance(v, str):
                v = tuple(round(x, 5) for x in v)
            elif isinstance(v, float):
                v = round(v, 5)
            values[(n.name, s.identifier)] = v
    return sorted(n.name for n in nt.nodes), links, values
```

- [ ] **Step 2: Write the failing tests**

`tests/test_materials.py`:
```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import helpers  # noqa: E402
from helpers import emission_material, snapshot  # noqa: E402
from afterglow import materials  # noqa: E402


def tagged(mat):
    return [n for n in mat.node_tree.nodes if n.get(materials.TAG)]


def test_emission_patch_and_restore():
    mat = emission_material("m", strength=5.0)
    before = snapshot(mat)
    assert materials.patch_material(mat)
    em = mat.node_tree.nodes["Emission"]
    mul = em.inputs["Strength"].links[0].from_node
    assert mul.bl_idname == "ShaderNodeMath" and mul.inputs[0].default_value == 5.0
    assert em.inputs["Color"].links[0].from_node.bl_idname == "ShaderNodeMix"
    names = {n.attribute_name for n in tagged(mat) if n.bl_idname == "ShaderNodeAttribute"}
    assert names == {"ar_level", "ar_tint", "ar_color"}, names
    assert materials.unpatch_material(mat)
    assert snapshot(mat) == before
    assert not tagged(mat)


def test_principled_linked_strength_restored():
    mat = bpy.data.materials.new("p")
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    val = nt.nodes.new("ShaderNodeValue")
    val.outputs[0].default_value = 3.0
    nt.links.new(val.outputs[0], bsdf.inputs["Emission Strength"])
    bsdf.inputs["Emission Color"].default_value = (0.2, 0.4, 1.0, 1.0)
    before = snapshot(mat)
    assert materials.has_emission(mat)
    assert materials.patch_material(mat)
    assert bsdf.inputs["Emission Strength"].links[0].from_node.bl_idname == "ShaderNodeMath"
    assert materials.unpatch_material(mat)
    assert snapshot(mat) == before


def test_principled_without_emission_ignored():
    mat = bpy.data.materials.new("plain")
    before = snapshot(mat)
    assert not materials.has_emission(mat)
    assert not materials.patch_material(mat)
    assert snapshot(mat) == before


def test_idempotent():
    mat = emission_material("m")
    assert materials.patch_material(mat)
    count = len(mat.node_tree.nodes)
    assert not materials.patch_material(mat)
    assert len(mat.node_tree.nodes) == count
    assert materials.unpatch_material(mat)
    assert not materials.unpatch_material(mat)


def test_multiple_emitters_all_patched():
    mat = emission_material("m")
    nt = mat.node_tree
    em2 = nt.nodes.new("ShaderNodeEmission")
    add = nt.nodes.new("ShaderNodeAddShader")
    out = nt.nodes["Material Output"]
    nt.links.new(nt.nodes["Emission"].outputs[0], add.inputs[0])
    nt.links.new(em2.outputs[0], add.inputs[1])
    nt.links.new(add.outputs[0], out.inputs["Surface"])
    before = snapshot(mat)
    assert materials.patch_material(mat)
    assert len([n for n in tagged(mat) if n.bl_idname == "ShaderNodeMath"]) == 2
    assert materials.unpatch_material(mat)
    assert snapshot(mat) == before


def test_unpatch_survives_deleted_target():
    mat = emission_material("m")
    materials.patch_material(mat)
    mat.node_tree.nodes.remove(mat.node_tree.nodes["Emission"])
    assert materials.unpatch_material(mat)
    assert not tagged(mat)


helpers.run(globals())
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `"$B" -b --factory-startup --python-exit-code 1 --python tests/test_materials.py`
Expected: exit 1 with `ImportError: cannot import name 'materials'`.

- [ ] **Step 4: Implement `afterglow/materials.py`**

```python
"""Patch emission inputs to read Afterglow attributes, and undo it exactly."""
TAG = "afterglow"


def _targets(nt):
    """Yield (node, strength socket, color socket) for every emission input in the tree."""
    for n in nt.nodes:
        if n.get(TAG):
            continue
        if n.bl_idname == "ShaderNodeEmission":
            yield n, n.inputs["Strength"], n.inputs["Color"]
        elif n.bl_idname == "ShaderNodeBsdfPrincipled":
            s = n.inputs["Emission Strength"]
            if s.is_linked or s.default_value > 0:
                yield n, s, n.inputs["Emission Color"]


def has_emission(mat):
    return bool(mat and mat.node_tree and next(_targets(mat.node_tree), None))


def is_patched(mat):
    return bool(mat and mat.node_tree) and any(n.get(TAG) for n in mat.node_tree.nodes)


def _new(nt, idname, near, dx, dy=0):
    n = nt.nodes.new(idname)
    n[TAG] = 1
    n.label = "Afterglow"
    n.location = (near.location.x - dx, near.location.y - dy)
    return n


def _route(nt, patch, orig_index, out, node, target):
    """Move target's original link/value into patch.inputs[orig_index], then feed target from out."""
    src = patch.inputs[orig_index]
    if target.is_linked:
        nt.links.new(target.links[0].from_socket, src)
    else:
        src.default_value = target.default_value
    nt.links.new(out, target)
    patch[TAG + "_to"] = node.name + "|" + target.identifier
    patch[TAG + "_from"] = orig_index


def patch_material(mat):
    if not has_emission(mat) or is_patched(mat):
        return False
    nt = mat.node_tree
    for node, strength, color in list(_targets(nt)):
        attrs = {}
        for i, name in enumerate(("ar_level", "ar_tint", "ar_color")):
            a = _new(nt, "ShaderNodeAttribute", node, 500, i * 160)
            a.attribute_type = "GEOMETRY"
            a.attribute_name = name
            attrs[name] = a
        mul = _new(nt, "ShaderNodeMath", node, 250)
        mul.operation = "MULTIPLY"
        nt.links.new(attrs["ar_level"].outputs["Fac"], mul.inputs[1])
        _route(nt, mul, 0, mul.outputs[0], node, strength)
        mix = _new(nt, "ShaderNodeMix", node, 250, 200)
        mix.data_type = "RGBA"
        nt.links.new(attrs["ar_tint"].outputs["Fac"], mix.inputs[0])
        nt.links.new(attrs["ar_color"].outputs["Color"], mix.inputs[7])
        _route(nt, mix, 6, mix.outputs[2], node, color)
    return True


def unpatch_material(mat):
    if not is_patched(mat):
        return False
    nt = mat.node_tree
    tagged = [n for n in nt.nodes if n.get(TAG)]
    for patch in tagged:
        to = patch.get(TAG + "_to")
        if not to:
            continue
        node_name, ident = to.rsplit("|", 1)
        node = nt.nodes.get(node_name)
        if node is None:
            continue  # user deleted the target; nothing to restore
        target = next(s for s in node.inputs if s.identifier == ident)
        src = patch.inputs[patch[TAG + "_from"]]
        if src.is_linked:
            nt.links.new(src.links[0].from_socket, target)
        else:
            target.default_value = src.default_value
    for n in tagged:
        nt.nodes.remove(n)
    return True
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `"$B" -b --factory-startup --python-exit-code 1 --python tests/test_materials.py`
Expected: `6/6 passed`. Also re-run `tests/test_nodes.py`, which should still show `8/8 passed`.

- [ ] **Step 6: Commit**

```bash
git add afterglow/materials.py tests/helpers.py tests/test_materials.py
git commit -m "feat: reversible emission material patcher

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Properties + collection operations

**Files:**
- Create: `afterglow/core.py`, `afterglow/props.py`
- Modify: `afterglow/__init__.py`
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: `nodes.get_group/set_input/get_input/GROUP_NAME`, `materials.has_emission/patch_material/unpatch_material/is_patched`, `helpers.*`, `helpers.snapshot`
- Produces (in `afterglow.core`):
  - `SUPPORTED = {"MESH", "CURVE", "FONT"}`, `MOD_NAME = "Afterglow"`
  - `targets(collection) -> list[Object]`: recursive, supported types only, sorted by name
  - `find_modifier(ob) -> Modifier | None`
  - `band(k: int, n: int) -> (low_hz, high_hz)`
  - `apply(scene) -> (applied, skipped, outside)`: three lists of Objects. Raises `ValueError("Pick a collection first")` or `ValueError("Pick a sound file first")`.
  - `respread(objs)`, `sync(scene)`, `remove(collection)`
- Produces (in `afterglow.props`): `scene.afterglow.collection`, `.sound`, `.start_frame` (updates call `core.sync`), `object.afterglow.locked`, and `register()` / `unregister()`

- [ ] **Step 1: Write the failing tests**

`tests/test_core.py`:
```python
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import helpers  # noqa: E402
from helpers import at, attr, emission_material, plane, snapshot, sound  # noqa: E402
from afterglow import core, materials, nodes  # noqa: E402


def stage():
    """Collection 'Lights' with 3 emissive planes, 1 non-emissive plane, 1 empty."""
    scene = bpy.context.scene
    coll = bpy.data.collections.new("Lights")
    scene.collection.children.link(coll)
    for name in ("a_bass", "b_mid", "c_treble"):
        plane(name, coll, emission_material("mat_" + name))
    plane("d_plain", coll, bpy.data.materials.new("mat_plain"))
    coll.objects.link(bpy.data.objects.new("e_empty", None))
    s = scene.afterglow
    s.collection = coll
    s.sound = sound()
    return scene, coll


def band_of(name):
    mod = core.find_modifier(bpy.data.objects[name])
    return nodes.get_input(mod, "Low"), nodes.get_input(mod, "High")


def test_apply_requires_collection_and_sound():
    scene = bpy.context.scene
    for expected in ("Pick a collection first", "Pick a sound file first"):
        try:
            core.apply(scene)
        except ValueError as e:
            assert str(e) == expected, str(e)
        else:
            raise AssertionError("no ValueError")
        scene.afterglow.collection = bpy.data.collections.new("C")


def test_apply_spreads_and_skips():
    scene, _ = stage()
    applied, skipped, outside = core.apply(scene)
    assert [o.name for o in applied] == ["a_bass", "b_mid", "c_treble"]
    assert [o.name for o in skipped] == ["d_plain"]
    assert outside == []
    assert math.isclose(band_of("a_bass")[0], 20.0, rel_tol=1e-4)
    assert math.isclose(band_of("c_treble")[1], 16000.0, rel_tol=1e-4)
    assert math.isclose(band_of("a_bass")[1], band_of("b_mid")[0], rel_tol=1e-4)
    assert core.find_modifier(bpy.data.objects["d_plain"]) is None
    assert materials.is_patched(bpy.data.materials["mat_a_bass"])
    assert not materials.is_patched(bpy.data.materials["mat_plain"])


def test_end_to_end_levels():
    scene, _ = stage()
    core.apply(scene)
    a, c = bpy.data.objects["a_bass"], bpy.data.objects["c_treble"]
    at(13)
    assert attr(a) > 0.3 and attr(c) < 0.05, (attr(a), attr(c))
    at(61)
    assert attr(c) > 0.3 and attr(a) < 0.05, (attr(a), attr(c))


def test_reapply_no_duplicates():
    scene, _ = stage()
    core.apply(scene)
    counts = {m.name: len(m.node_tree.nodes) for m in bpy.data.materials}
    core.apply(scene)
    for name in ("a_bass", "b_mid", "c_treble"):
        mods = [m for m in bpy.data.objects[name].modifiers if m.type == "NODES"]
        assert len(mods) == 1, (name, len(mods))
    assert counts == {m.name: len(m.node_tree.nodes) for m in bpy.data.materials}


def test_locked_band_survives():
    scene, _ = stage()
    core.apply(scene)
    b = bpy.data.objects["b_mid"]
    b.afterglow.locked = True
    mod = core.find_modifier(b)
    nodes.set_input(mod, "Low", 500.0)
    nodes.set_input(mod, "High", 600.0)
    core.apply(scene)
    assert band_of("b_mid") == (500.0, 600.0)
    assert all(math.isclose(x, y, rel_tol=1e-4) for x, y in zip(band_of("a_bass"), core.band(0, 2)))
    assert all(math.isclose(x, y, rel_tol=1e-4) for x, y in zip(band_of("c_treble"), core.band(1, 2)))


def test_sync_start_frame_and_sound():
    scene, _ = stage()
    core.apply(scene)
    scene.afterglow.start_frame = 25
    for name in ("a_bass", "b_mid", "c_treble"):
        assert nodes.get_input(core.find_modifier(bpy.data.objects[name]), "Start Frame") == 25


def test_remove_restores():
    scene, coll = stage()
    before = {m.name: snapshot(m) for m in bpy.data.materials}
    core.apply(scene)
    core.remove(coll)
    assert before == {m.name: snapshot(m) for m in bpy.data.materials}
    assert all(core.find_modifier(o) is None for o in coll.all_objects)
    core.remove(coll)  # second remove is a no-op


def test_shared_material_outside_reported():
    scene, _ = stage()
    plane("outsider", None, bpy.data.materials["mat_a_bass"])
    _, _, outside = core.apply(scene)
    assert [o.name for o in outside] == ["outsider"]


def test_nested_collection_included():
    scene, coll = stage()
    child = bpy.data.collections.new("Child")
    coll.children.link(child)
    plane("f_child", child, emission_material("mat_f"))
    applied, _, _ = core.apply(scene)
    assert "f_child" in [o.name for o in applied]


def test_multi_material_object():
    scene, coll = stage()
    plain = bpy.data.materials.new("mat_plain2")
    before = snapshot(plain)
    ob = plane("g_multi", coll, emission_material("mat_g"))
    ob.data.materials.append(plain)
    core.apply(scene)
    assert materials.is_patched(bpy.data.materials["mat_g"])
    assert snapshot(plain) == before


helpers.run(globals())
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `"$B" -b --factory-startup --python-exit-code 1 --python tests/test_core.py`
Expected: exit 1 with `ImportError: cannot import name 'core'`.

- [ ] **Step 3: Implement `afterglow/core.py`**

```python
"""Collection-level operations: apply, respread, sync, remove."""
from . import materials, nodes

SUPPORTED = {"MESH", "CURVE", "FONT"}
MOD_NAME = "Afterglow"
LOW_HZ, HIGH_HZ = 20.0, 16000.0


def targets(collection):
    return sorted((o for o in collection.all_objects if o.type in SUPPORTED), key=lambda o: o.name)


def find_modifier(ob):
    return next((m for m in ob.modifiers
                 if m.type == "NODES" and m.node_group and m.node_group.name == nodes.GROUP_NAME), None)


def _materials(ob):
    return {slot.material for slot in ob.material_slots if slot.material}


def band(k, n):
    """Band k of n, log-spaced between LOW_HZ and HIGH_HZ."""
    ratio = HIGH_HZ / LOW_HZ
    return LOW_HZ * ratio ** (k / n), LOW_HZ * ratio ** ((k + 1) / n)


def apply(scene):
    """Add or refresh the modifier and patch materials on every emissive object in the collection.

    Returns (applied, skipped, outside). 'outside' lists objects outside the collection that
    share a patched material; they have no Afterglow attributes, so they render dark.
    """
    s = scene.afterglow
    if s.collection is None:
        raise ValueError("Pick a collection first")
    if s.sound is None:
        raise ValueError("Pick a sound file first")
    group = nodes.get_group()
    applied, skipped, patched = [], [], set()
    for ob in targets(s.collection):
        mats = {m for m in _materials(ob) if materials.has_emission(m)}
        if not mats:
            skipped.append(ob)
            continue
        mod = find_modifier(ob) or ob.modifiers.new(MOD_NAME, "NODES")
        mod.node_group = group
        nodes.set_input(mod, "Sound", s.sound)
        nodes.set_input(mod, "Start Frame", s.start_frame)
        for m in mats:
            materials.patch_material(m)
        patched |= mats
        applied.append(ob)
    respread(applied)
    outside = [o for o in scene.objects if o not in applied and _materials(o) & patched]
    return applied, skipped, outside


def respread(objs):
    free = [o for o in objs if not o.afterglow.locked]
    for k, ob in enumerate(free):
        low, high = band(k, len(free))
        mod = find_modifier(ob)
        nodes.set_input(mod, "Low", low)
        nodes.set_input(mod, "High", high)


def sync(scene):
    """Push the scene-wide Sound and Start Frame into every Afterglow modifier."""
    s = scene.afterglow
    if s.collection is None:
        return
    for ob in targets(s.collection):
        mod = find_modifier(ob)
        if mod:
            nodes.set_input(mod, "Sound", s.sound)
            nodes.set_input(mod, "Start Frame", s.start_frame)


def remove(collection):
    for ob in targets(collection):
        mod = find_modifier(ob)
        if mod:
            ob.modifiers.remove(mod)
        for m in _materials(ob):
            materials.unpatch_material(m)
```

- [ ] **Step 4: Implement `afterglow/props.py`**

```python
import bpy
from bpy.props import BoolProperty, IntProperty, PointerProperty

from . import core


def _sync(self, context):
    core.sync(context.scene)


class AfterglowScene(bpy.types.PropertyGroup):
    collection: PointerProperty(type=bpy.types.Collection, name="Collection",
                                description="Every emissive object in here reacts", update=_sync)
    sound: PointerProperty(type=bpy.types.Sound, name="Sound", update=_sync)
    start_frame: IntProperty(name="Start Frame", default=1,
                             description="Frame where the audio starts playing", update=_sync)


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

- [ ] **Step 5: Replace `afterglow/__init__.py`**

```python
from . import props


def register():
    props.register()


def unregister():
    props.unregister()
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `"$B" -b --factory-startup --python-exit-code 1 --python tests/test_core.py`
Expected: `10/10 passed`. Re-run `test_nodes.py` (8/8) and `test_materials.py` (6/6).

- [ ] **Step 7: Commit**

```bash
git add afterglow tests/test_core.py
git commit -m "feat: collection apply/respread/sync/remove with scene properties

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Operators + N-panel

**Files:**
- Create: `afterglow/ui.py`
- Modify: `afterglow/__init__.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `core.apply/respread/remove/targets/find_modifier`, `nodes.OBJECT_INPUTS/draw_input`, `props`
- Produces: operators `afterglow.load_sound(filepath)`, `afterglow.apply`, `afterglow.respread`, `afterglow.remove`, and the panel `AFTERGLOW_PT_panel` (View3D > Sidebar > Afterglow)

- [ ] **Step 1: Write the failing tests**

`tests/test_ui.py`:
```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import afterglow  # noqa: E402
import helpers  # noqa: E402
from helpers import emission_material, plane, tone_wav  # noqa: E402
from afterglow import core, nodes  # noqa: E402


def stage():
    coll = bpy.data.collections.new("Lights")
    bpy.context.scene.collection.children.link(coll)
    plane("a", coll, emission_material("ma"))
    plane("b", coll, emission_material("mb"))
    bpy.context.scene.afterglow.collection = coll
    return coll


def test_register_roundtrip():
    afterglow.unregister()
    assert "afterglow" not in bpy.types.Scene.bl_rna.properties
    afterglow.register()
    assert hasattr(bpy.types, "AFTERGLOW_PT_panel")


def test_apply_without_sound_reports_error():
    stage()
    # An operator that reports {'ERROR'} makes bpy.ops raise RuntimeError instead of returning.
    try:
        bpy.ops.afterglow.apply()
    except RuntimeError as e:
        assert "Pick a sound file first" in str(e), str(e)
    else:
        raise AssertionError("expected an error report")


def test_load_sound_reuses_datablock():
    assert bpy.ops.afterglow.load_sound(filepath=tone_wav()) == {"FINISHED"}
    assert bpy.ops.afterglow.load_sound(filepath=tone_wav()) == {"FINISHED"}
    assert bpy.context.scene.afterglow.sound is not None
    assert len(bpy.data.sounds) == 1


def test_operator_cycle():
    coll = stage()
    bpy.ops.afterglow.load_sound(filepath=tone_wav())
    assert bpy.ops.afterglow.apply() == {"FINISHED"}
    assert all(core.find_modifier(o) for o in coll.all_objects)
    assert bpy.ops.afterglow.respread() == {"FINISHED"}
    assert bpy.ops.afterglow.remove() == {"FINISHED"}
    assert not any(core.find_modifier(o) for o in coll.all_objects)


def test_modifier_inputs_drawable():
    stage()
    bpy.ops.afterglow.load_sound(filepath=tone_wav())
    bpy.ops.afterglow.apply()
    mod = core.find_modifier(bpy.data.objects["a"])
    for name in nodes.OBJECT_INPUTS:
        assert "value" in nodes._socket(mod, name).bl_rna.properties, name


helpers.run(globals())
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `"$B" -b --factory-startup --python-exit-code 1 --python tests/test_ui.py`
Expected: exit 1. `test_register_roundtrip` fails because `AFTERGLOW_PT_panel` is missing, and the operator tests fail because `bpy.ops.afterglow` has no operators.

- [ ] **Step 3: Implement `afterglow/ui.py`**

```python
import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper

from . import core, nodes


class AFTERGLOW_OT_load_sound(bpy.types.Operator, ImportHelper):
    bl_idname = "afterglow.load_sound"
    bl_label = "Load Sound"
    bl_description = "Load an audio file to drive the collection"

    filter_glob: StringProperty(default="*.wav;*.mp3;*.flac;*.ogg;*.m4a;*.aac", options={"HIDDEN"})

    def execute(self, context):
        try:
            context.scene.afterglow.sound = bpy.data.sounds.load(self.filepath, check_existing=True)
        except RuntimeError as e:
            self.report({"ERROR"}, f"Could not load sound: {e}")
            return {"CANCELLED"}
        return {"FINISHED"}


class AFTERGLOW_OT_apply(bpy.types.Operator):
    bl_idname = "afterglow.apply"
    bl_label = "Apply"
    bl_description = "Make every emissive object in the collection react to the sound"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            applied, skipped, outside = core.apply(context.scene)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        msg = f"{len(applied)} objects react"
        if skipped:
            msg += f", {len(skipped)} skipped: no emission"
        self.report({"INFO"}, msg)
        if outside:
            self.report({"WARNING"}, "Shared material also used outside the collection, these render dark: "
                        + ", ".join(o.name for o in outside))
        return {"FINISHED"}


class AFTERGLOW_OT_respread(bpy.types.Operator):
    bl_idname = "afterglow.respread"
    bl_label = "Re-spread"
    bl_description = "Spread frequency bands bass to treble across unlocked objects"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.scene.afterglow.collection is not None

    def execute(self, context):
        coll = context.scene.afterglow.collection
        core.respread([o for o in core.targets(coll) if core.find_modifier(o)])
        return {"FINISHED"}


class AFTERGLOW_OT_remove(bpy.types.Operator):
    bl_idname = "afterglow.remove"
    bl_label = "Remove"
    bl_description = "Remove Afterglow modifiers and restore the original materials"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.scene.afterglow.collection is not None

    def execute(self, context):
        core.remove(context.scene.afterglow.collection)
        return {"FINISHED"}


class AFTERGLOW_PT_panel(bpy.types.Panel):
    bl_label = "Afterglow"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Afterglow"

    def draw(self, context):
        s = context.scene.afterglow
        col = self.layout.column()
        col.prop(s, "collection")
        row = col.row(align=True)
        row.prop(s, "sound")
        row.operator("afterglow.load_sound", text="", icon="FILEBROWSER")
        col.prop(s, "start_frame")
        row = col.row(align=True)
        row.operator("afterglow.apply")
        row.operator("afterglow.respread")
        row.operator("afterglow.remove")

        ob = context.object
        mod = core.find_modifier(ob) if ob else None
        if mod:
            box = self.layout.box()
            box.label(text=ob.name, icon="OBJECT_DATA")
            box.prop(ob.afterglow, "locked")
            for name in nodes.OBJECT_INPUTS:
                nodes.draw_input(box, mod, name)


CLASSES = (AFTERGLOW_OT_load_sound, AFTERGLOW_OT_apply, AFTERGLOW_OT_respread,
           AFTERGLOW_OT_remove, AFTERGLOW_PT_panel)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
```

- [ ] **Step 4: Replace `afterglow/__init__.py`**

```python
from . import props, ui


def register():
    props.register()
    ui.register()


def unregister():
    ui.unregister()
    props.unregister()
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `"$B" -b --factory-startup --python-exit-code 1 --python tests/test_ui.py`
Expected: `5/5 passed`. Re-run the other three test files, which should all pass.

- [ ] **Step 6: Commit**

```bash
git add afterglow tests/test_ui.py
git commit -m "feat: operators and N-panel

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Render smoke test + extension package

**Files:**
- Test: `tests/test_render.py`
- Output (not committed): `dist/afterglow-0.1.0.zip`

**Interfaces:**
- Consumes: `core.apply`, `helpers.*`

- [ ] **Step 1: Write the render test**

`tests/test_render.py`:
```python
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import helpers  # noqa: E402
from helpers import at, emission_material, plane, sound  # noqa: E402
from afterglow import core  # noqa: E402


def center_brightness(scene, frame):
    at(frame)
    scene.render.filepath = os.path.join(tempfile.gettempdir(), f"afterglow_render_{frame}.png")
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(scene.render.filepath)
    w, h = img.size
    i = ((h // 2) * w + w // 2) * 4
    value = sum(img.pixels[i:i + 3]) / 3
    bpy.data.images.remove(img)
    return value


def test_bass_plane_glows_on_bass_only():
    scene = bpy.context.scene
    coll = bpy.data.collections.new("Lights")
    scene.collection.children.link(coll)
    for name in ("a_bass", "b_mid", "c_treble"):
        plane(name, coll, emission_material("mat_" + name))
    bpy.data.objects["b_mid"].hide_render = True
    bpy.data.objects["c_treble"].hide_render = True
    scene.afterglow.collection = coll
    scene.afterglow.sound = sound()
    core.apply(scene)

    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    cam.location = (0, 0, 4)
    scene.camera = cam
    scene.world = bpy.data.worlds.new("w")
    scene.world.color = (0, 0, 0)
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = scene.render.resolution_y = 64

    bass = center_brightness(scene, 13)    # 0.5 s, 100 Hz playing
    mid = center_brightness(scene, 37)     # 1.5 s, 1 kHz playing
    # AgX view transform compresses highlights, so compare loosely, not against raw strength.
    assert bass > 0.2 and mid < 0.05, (bass, mid)


helpers.run(globals())
```

- [ ] **Step 2: Run it**

Run: `"$B" -b --factory-startup --python-exit-code 1 --python tests/test_render.py`
Expected: `1/1 passed`. The code under test already exists, so this test guards the attribute bridge at render time. If it fails, `ar_level` is not reaching the shader. Check that the Attribute node type is `GEOMETRY` and the name matches exactly before changing anything else.

- [ ] **Step 3: Validate and build the extension**

```bash
"$B" --command extension validate afterglow
"$B" --command extension build --source-dir afterglow --output-dir dist
```
Expected: validate prints no errors, and `dist/afterglow-0.1.0.zip` exists.

- [ ] **Step 4: Run the full suite once more**

```bash
for t in nodes materials core ui render; do "$B" -b --factory-startup --python-exit-code 1 --python tests/test_$t.py 2>&1 | tail -1; done
```
Expected: `8/8`, `6/6`, `10/10`, `5/5`, `1/1 passed`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_render.py
git commit -m "test: Eevee render smoke test for the attribute bridge

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Hand off for a manual check in the Blender UI** (this can't be automated)

Ask the user to:
1. Install `dist/afterglow-0.1.0.zip` via Edit > Preferences > Get Extensions > Install from Disk.
2. Put the emissive objects in a collection. Open the 3D Viewport N-panel, Afterglow tab, then pick the collection, load a song, and click Apply.
3. Switch the viewport to Material Preview and scrub the timeline. Objects should pulse on different bands.
4. To hear the audio while scrubbing, add the same file to the Video Sequencer at the Start Frame and set Playback Sync to "Sync to Audio".
