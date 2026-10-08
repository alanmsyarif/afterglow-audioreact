# Afterglow Through Objects Implementation Plan (per-pixel)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** With Ripple, Through Objects and Fast Playback on, the wave crosses each object's surface *per rendered pixel*, crisp even on single-quad faces. It continues seamlessly into neighbouring objects.

**Architecture:**
- Python computes one wave coordinate for the collection and each object's tap range.
- The bake keys 8 taps per object.
- Group v5 stores the tap *levels* plus the wave parameters as 9 constant vector attributes, behind a lazy Switch, only when Through and baked.
- A shared `Afterglow Wave` shader group computes each pixel's wave position from its world position and hat-blends the taps.
- Material patch v2 routes Strength and Color through that group.

**Tech Stack:** Blender 5.2 bpy, mathutils, numpy. Headless assert tests, including Eevee renders.

**Spec:** `docs/superpowers/specs/2026-10-08-afterglow-through-objects-design.md` (revised for per-pixel)

## Global Constraints

- `TAPS = 8`. GN `VERSION = 5`. Material `PATCH_VERSION = 2`.
- GN inputs are append-only, in this order: `Through`, `Wave Radial`, `Wave Axis`, `Wave Origin`, `Wave Lo`, `Wave Span`, `Wave Reverse`, `Tap Min`, `Tap Max`, `Baked Tap 0` … `Baked Tap 7`. `Wave Lo` is the only float allowed below 0. `Tap Min` and `Tap Max` max out at 1.
- Attribute names (FLOAT_VECTOR, point domain):
  - `ar_taps0`, `ar_taps1`, `ar_taps2` (taps 6, 7, then the through flag)
  - `ar_wave_axis`, `ar_wave_origin`
  - `ar_wave_lsr` (Lo, Span, Radial)
  - `ar_wave_rtt` (Reverse, Tap Min, Tap Max)
  - `ar_color_a`, `ar_color_b`
- **Both** the GN group and the shader group carry the `afterglow` tag. `nodes._current()` and `nodes.migrate()` must only consider `bl_idname == "GeometryNodeTree"`, or migration would delete the shader group.
- The metric matches `core.place()`, as in the spec. Keep all earlier invariants (`set_input` tags, the missing-sound guard, `at()` settle, the crash-guard test last in `test_core.py`).
- Test command: `B="/c/Program Files/Blender Foundation/Blender 5.2/blender.exe"; "$B" -b --factory-startup --python-exit-code 1 --python tests/test_<name>.py`

## Verified facts (2026-10-08)

- An Attribute node (GEOMETRY) inside a shader node group reads a FLOAT_VECTOR point attribute stored by the modifier, through its `Vector` output.
- `ShaderNodeNewGeometry.outputs["Position"]` is world space and varies per pixel. In Eevee, a 4-vertex quad rendered a smooth gradient from 0.22 to 0.99.
- In both GN and shader trees, `ShaderNodeMix` RGBA uses inputs `[0]` factor, `[6]` A and `[7]` B, and output `[2]`.

## Review Focus

1. **Per-pixel, not per-vertex.** Pinned by `test_through_blends_per_pixel_on_one_quad` (Task 3), a render of a 4-vertex quad.
2. **Seams between touching objects.** Pinned by `test_through_seam_taps_match` (Task 4): left tap 7 equals right tap 0.
3. **Old patches and old groups.** v1 material patches are re-patched, v4 groups migrate, and the shader group survives migration. Pinned by `test_v1_patch_is_repatched` and `test_shader_group_survives_migration` (Task 3).
4. **No cost when Through is off.** Attributes are absent. Pinned by `test_through_attributes_absent_unless_baked_and_through` (Task 1).
5. **Remove and re-bake** leave no leftover or duplicated tap curves. Pinned by `test_through_remove_clears_taps` (Task 4).

---

### Task 1: Group v5 — inputs and attribute packing

**Files:** `afterglow/nodes.py`, `tests/helpers.py`, `tests/test_nodes.py`

**Interfaces:**
- Produces: `nodes.TAPS`, `nodes.BAKE_INPUTS`, `nodes.WAVE_INPUTS`, the new inputs, and the 9 attributes when Through and Use Baked are both on. `helpers.attr` returns a tuple for vector attributes.

- [ ] **Step 1: Write the failing tests.**

In `tests/helpers.py`, change the return line of `attr` to:

```python
        return tuple(item.color) if hasattr(item, "color") else (tuple(item.vector) if hasattr(item, "vector") else item.value)
```

and append:

```python
def has_attr(ob, name):
    ev = ob.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    try:
        return name in me.attributes
    finally:
        ev.to_mesh_clear()
```

Append to `tests/test_nodes.py`, before `helpers.run(globals())`:

```python
def close(a, b):
    return all(abs(x - y) < 1e-4 for x, y in zip(a, b))


def test_through_stores_tap_levels_and_wave():
    inputs = {"Use Baked": True, "Through": True, "Gain": 2.0, "Threshold": 0.1,
              "Wave Axis": (0.0, 1.0, 0.0), "Wave Origin": (1.0, 2.0, 3.0), "Wave Lo": -3.0, "Wave Span": 6.0,
              "Wave Radial": True, "Wave Reverse": True, "Tap Min": 0.25, "Tap Max": 0.75,
              "Color A": (1.0, 0.0, 0.0, 1.0), "Color B": (0.0, 0.0, 1.0, 1.0)}
    inputs.update({f"Baked Tap {k}": k * 0.1 for k in range(8)})
    ob = reactive("w", BASS, inputs)
    at(5)
    # level = max(tap - 0.1, 0) * 2  ->  0, 0, .2, .4, .6, .8, 1.0, 1.2
    assert close(helpers.attr(ob, "ar_taps0"), (0.0, 0.0, 0.2)), helpers.attr(ob, "ar_taps0")
    assert close(helpers.attr(ob, "ar_taps1"), (0.4, 0.6, 0.8))
    assert close(helpers.attr(ob, "ar_taps2"), (1.0, 1.2, 1.0))  # through flag
    assert close(helpers.attr(ob, "ar_wave_axis"), (0.0, 1.0, 0.0))
    assert close(helpers.attr(ob, "ar_wave_origin"), (1.0, 2.0, 3.0))
    assert close(helpers.attr(ob, "ar_wave_lsr"), (-3.0, 6.0, 1.0))
    assert close(helpers.attr(ob, "ar_wave_rtt"), (1.0, 0.25, 0.75))
    assert close(helpers.attr(ob, "ar_color_a"), (1.0, 0.0, 0.0))
    assert close(helpers.attr(ob, "ar_color_b"), (0.0, 0.0, 1.0))


def test_through_attributes_absent_unless_baked_and_through():
    only_through = reactive("t", BASS, {"Through": True})
    only_baked = reactive("b", BASS, {"Use Baked": True})
    at(5)
    assert not helpers.has_attr(only_through, "ar_taps0") and not helpers.has_attr(only_baked, "ar_taps0")
```

- [ ] **Step 2: Run, and confirm they fail** with `KeyError` on "Through".

- [ ] **Step 3: Edit `afterglow/nodes.py`.**
  - Set `VERSION = 5` and add `TAPS = 8` under `KICK`.
  - Replace the closing `)` of `INPUTS` (the line after `("Baked Peak", ...)`) with:

```python
    ("Through", "NodeSocketBool", False),          # Through Objects: the wave crosses each surface
    ("Wave Radial", "NodeSocketBool", False),      # wave coordinate, written by core.wave()
    ("Wave Axis", "NodeSocketVector", (1.0, 0.0, 0.0)),
    ("Wave Origin", "NodeSocketVector", (0.0, 0.0, 0.0)),
    ("Wave Lo", "NodeSocketFloat", 0.0),
    ("Wave Span", "NodeSocketFloat", 1.0),
    ("Wave Reverse", "NodeSocketBool", False),
    ("Tap Min", "NodeSocketFloat", 0.0),           # this object's range along the wave
    ("Tap Max", "NodeSocketFloat", 1.0),
) + tuple((f"Baked Tap {k}", "NodeSocketFloat", 0.0) for k in range(8))  # keyed by bake.py
```

  - Below `INPUTS`, add:

```python
BAKE_INPUTS = ("Baked Peak",) + tuple(f"Baked Tap {k}" for k in range(TAPS))
WAVE_INPUTS = ("Wave Radial", "Wave Axis", "Wave Origin", "Wave Lo", "Wave Span", "Wave Reverse")
```

  - Replace `SCENE_INPUTS` and `OBJECT_INPUTS` with:

```python
SCENE_INPUTS = {"Sound", "Start Frame", "Ripple", "Beats per Sweep", "Use Baked", "Through", *WAVE_INPUTS}
RIPPLE_PAIR = {"Gain": "Ripple Gain", "Threshold": "Ripple Threshold"}
OBJECT_INPUTS = [name for name, _, _ in INPUTS
                 if name not in SCENE_INPUTS | {"Ripple Position", "Tap Min", "Tap Max", *BAKE_INPUTS}
                 | set(RIPPLE_PAIR.values())]
```

    Delete the old `RIPPLE_PAIR` line, which was defined just above, so it isn't duplicated.
  - Restrict group lookup to geometry trees, because the shader Wave group carries the same tag:

```python
def _current():
    return next((g for g in bpy.data.node_groups
                 if g.bl_idname == "GeometryNodeTree" and g.get(TAG, 0) >= VERSION), None)
```

    In `migrate()`, change the `old = [...]` line to:

```python
    old = [g for g in bpy.data.node_groups if g.bl_idname == "GeometryNodeTree" and 0 < g.get(TAG, 0) < VERSION]
```

  - In `_build`, change the socket-limits block to:

```python
        if socket_type == "NodeSocketFloat" and name != "Wave Lo":
            s.min_value = 0.0
        if name in {"Ripple Position", "Tap Min", "Tap Max"}:
            s.max_value = 1.0
```

  - In `_build`, after the attribute store loop (`for name, data_type, value in (...)` ending with `out = store.outputs["Geometry"]`) and **before** `L.new(out, go.inputs["Geometry"])`, insert:

```python
    # Through Objects: constant per-object vectors the shader's Afterglow Wave group reads.
    # A lazy Switch skips them unless Through and Fast Playback are both on.
    def vec(a, b, c):
        n = N.new("ShaderNodeCombineXYZ")
        for socket, v in zip(n.inputs, (a, b, c)):
            if isinstance(v, bpy.types.NodeSocket):
                L.new(v, socket)
            else:
                socket.default_value = v
        return n.outputs[0]

    lv = [op("MULTIPLY", op("MAXIMUM", op("SUBTRACT", g[f"Baked Tap {k}"], threshold), 0.0), gain)
          for k in range(TAPS)]
    on = N.new("FunctionNodeBooleanMath")
    on.operation = "AND"
    L.new(g["Through"], on.inputs[0])
    L.new(g["Use Baked"], on.inputs[1])
    waved = out
    for name, value in (
        ("ar_taps0", vec(lv[0], lv[1], lv[2])),
        ("ar_taps1", vec(lv[3], lv[4], lv[5])),
        ("ar_taps2", vec(lv[6], lv[7], 1.0)),
        ("ar_wave_axis", g["Wave Axis"]),
        ("ar_wave_origin", g["Wave Origin"]),
        ("ar_wave_lsr", vec(g["Wave Lo"], g["Wave Span"], g["Wave Radial"])),
        ("ar_wave_rtt", vec(g["Wave Reverse"], g["Tap Min"], g["Tap Max"])),
        ("ar_color_a", g["Color A"]),
        ("ar_color_b", g["Color B"]),
    ):
        store = N.new("GeometryNodeStoreNamedAttribute")
        store.data_type = "FLOAT_VECTOR"
        store.domain = "POINT"
        store.inputs["Name"].default_value = name
        L.new(waved, store.inputs["Geometry"])
        L.new(value, store.inputs["Value"])
        waved = store.outputs["Geometry"]
    pick = N.new("GeometryNodeSwitch")
    pick.input_type = "GEOMETRY"
    L.new(on.outputs[0], pick.inputs["Switch"])
    L.new(out, pick.inputs["False"])
    L.new(waved, pick.inputs["True"])
    out = pick.outputs[0]
```

- [ ] **Step 4: Run all suites.** Expected: nodes 18/18, everything else green.
- [ ] **Step 5: Commit:** `feat: group v5 stores tap levels and wave parameters for Through Objects`

---

### Task 2: Wave coordinate in Python

**Files:** `afterglow/core.py`, `afterglow/props.py`, `tests/test_core.py`

**Interfaces:**
- Produces: `core.wave(scene, objs)`, which writes `WAVE_INPUTS` plus `Tap Min` / `Tap Max`; `core._metric(pts, mode, origin)`; `scene.afterglow.through`.
- Sync and Apply write `Through`. `replace()` and Apply call `wave()` when Through is on.

- [ ] **Step 1: Write the failing tests.** Insert them before `def test_sync_skips_missing_sound():` in `tests/test_core.py`:

```python
def bar(name, coll, x0, x1, n=11):
    me = bpy.data.meshes.new(name)
    me.from_pydata([(x0 + (x1 - x0) * i / (n - 1), 0.0, 0.0) for i in range(n)], [], [])
    me.materials.append(emission_material("mat_" + name))
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


def bars_stage(specs, **settings):
    scene = bpy.context.scene
    coll = bpy.data.collections.new("Bars")
    scene.collection.children.link(coll)
    for name, x0, x1 in specs:
        bar(name, coll, x0, x1)
    s = scene.afterglow
    s.collection = coll
    s.sound = sound()
    for k, v in settings.items():
        setattr(s, k, v)
    core.apply(scene)
    return scene


def tap_range(name):
    mod = core.find_modifier(bpy.data.objects[name])
    return round(nodes.get_input(mod, "Tap Min"), 4), round(nodes.get_input(mod, "Tap Max"), 4)


def test_wave_axis_ranges():
    bars_stage([("long", 0.0, 10.0), ("small", 20.0, 21.0)], ripple_from="X", through=True)
    mod = core.find_modifier(bpy.data.objects["long"])
    assert tuple(nodes.get_input(mod, "Wave Axis")) == (1.0, 0.0, 0.0)
    assert (nodes.get_input(mod, "Wave Lo"), nodes.get_input(mod, "Wave Span")) == (0.0, 21.0)
    assert tap_range("long") == (0.0, round(10 / 21, 4)) and tap_range("small") == (round(20 / 21, 4), 1.0)


def test_wave_radial_ranges():
    scene = bpy.context.scene
    origin = bpy.data.objects.new("origin", None)
    scene.collection.objects.link(origin)
    bars_stage([("near", 2.0, 4.0), ("far", 6.0, 8.0)], ripple_from="EMPTY", ripple_origin=origin, through=True)
    mod = core.find_modifier(bpy.data.objects["near"])
    assert nodes.get_input(mod, "Wave Radial") is True
    assert tap_range("near") == (0.0, round(2 / 6, 4)) and tap_range("far") == (round(4 / 6, 4), 1.0)


def test_wave_reverse_flips_ranges():
    bars_stage([("long", 0.0, 10.0), ("small", 20.0, 21.0)], ripple_from="X", ripple_reverse=True, through=True)
    assert tap_range("long") == (round(1 - 10 / 21, 4), 1.0) and tap_range("small") == (0.0, round(1 / 21, 4))


def test_through_toggle_rewaves():
    scene = bars_stage([("long", 0.0, 10.0), ("small", 20.0, 21.0)], ripple_from="X")
    scene.afterglow.through = True
    assert tap_range("long") == (0.0, round(10 / 21, 4))
    assert nodes.get_input(core.find_modifier(bpy.data.objects["long"]), "Through") is True
```

- [ ] **Step 2: Run, and confirm they fail** (`AttributeError: ... 'through'`).

- [ ] **Step 3: Edit `afterglow/core.py`.**
  - Add `import numpy as np` after `import aud`.
  - Above `place()`, add:

```python
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


def _world_dists(ob, depsgraph, radial, axis, centre):
    """Metric of every evaluated vertex in world space (the object's origin if it has none)."""
    ev = ob.evaluated_get(depsgraph)
    me = ev.to_mesh()
    try:
        co = np.empty(len(me.vertices) * 3, np.float32)
        me.vertices.foreach_get("co", co)
    finally:
        ev.to_mesh_clear()
    mw = np.array(ob.matrix_world, dtype=np.float64)
    pts = co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3] if len(co) else mw[None, :3, 3]
    c, a = np.array(centre), np.array(axis)
    return np.linalg.norm(pts - c, axis=1) if radial else pts @ a


def wave(scene, objs):
    """Through Objects: one wave coordinate over every vertex of every object (Lo, Span) and
    each object's own range along it (Tap Min/Max), written to the modifiers."""
    if not objs:
        return
    s = scene.afterglow
    radial, axis, centre = _metric([o.matrix_world.translation.copy() for o in objs],
                                   s.ripple_from, s.ripple_origin)
    dg = bpy.context.evaluated_depsgraph_get()
    ranges = [(float(d.min()), float(d.max())) for d in (_world_dists(o, dg, radial, axis, centre) for o in objs)]
    lo = min(r[0] for r in ranges)
    span = max(r[1] for r in ranges) - lo
    span = span if span > 1e-6 else 1.0
    for ob, (a, b) in zip(objs, ranges):
        tmin, tmax = (a - lo) / span, (b - lo) / span
        if s.ripple_reverse:
            tmin, tmax = 1.0 - tmax, 1.0 - tmin
        mod = find_modifier(ob)
        for name, value in (("Wave Radial", radial), ("Wave Axis", tuple(axis)), ("Wave Origin", tuple(centre)),
                            ("Wave Lo", lo), ("Wave Span", span), ("Wave Reverse", s.ripple_reverse),
                            ("Tap Min", tmin), ("Tap Max", tmax)):
            nodes.set_input(mod, name, value)
```

  - In `place()`, replace the lines from `if mode == "CENTER" or (mode == "EMPTY" and origin):` through `dist = [p[axis] for p in pts]` with:

```python
    radial, axis, centre = _metric(pts, mode, origin)
    dist = [(p - centre).length for p in pts] if radial else [p.dot(axis) for p in pts]
```

  - In `replace()`, after the `place(...)` call, add `if s.through:` then `wave(scene, objs)`.
  - In `apply()`, after `place(applied, s.ripple_from, s.ripple_origin, s.ripple_reverse)`, add `if s.through:` then `wave(scene, applied)`.
  - In both `apply()`'s per-object block and `sync()`, after the `"Use Baked"` line, add `nodes.set_input(mod, "Through", s.through)`.

- [ ] **Step 4: Edit `afterglow/props.py`.**
  - Add after `ripple_reverse`:

```python
    through: BoolProperty(name="Through Objects", description="The wave crosses each object's surface "
                          "pixel by pixel instead of lighting it all at once (needs Fast Playback)",
                          update=_replace)
```

  - Insert `core.sync(context.scene)` as the first line of `_replace`.

- [ ] **Step 5: Run all suites.** Expected: core 36/36, everything else green.
- [ ] **Step 6: Commit:** `feat: wave coordinate and per-object tap ranges for Through Objects`

---

### Task 3: Afterglow Wave shader group and material patch v2

**Files:** `afterglow/materials.py`, `tests/test_materials.py`, `tests/test_render.py`

**Interfaces:**
- Produces: `materials.PATCH_VERSION = 2`, `materials.WAVE_GROUP = "Afterglow Wave"`, `materials.wave_group()`. `patch_material` re-patches older versions.

- [ ] **Step 1: Write the failing tests.**

In `tests/test_materials.py`, replace these lines in `test_emission_patch_and_restore`:

```python
    names = {n.attribute_name for n in tagged(mat) if n.bl_idname == "ShaderNodeAttribute"}
    assert names == {"ar_level", "ar_tint", "ar_color"}, names
```

with:

```python
    names = {n.attribute_name for n in tagged(mat) if n.bl_idname == "ShaderNodeAttribute"}
    assert names == {"ar_tint"}, names
    groups = [n for n in tagged(mat) if n.bl_idname == "ShaderNodeGroup"]
    assert len(groups) == 1 and groups[0].node_tree == materials.wave_group()
    assert mul.inputs[1].links[0].from_node == groups[0]
```

Append before `helpers.run(globals())`:

```python
def test_v1_patch_is_repatched():
    mat = emission_material("m")
    before = snapshot(mat)
    materials.patch_material(mat)
    for n in tagged(mat):
        n[materials.TAG] = 1  # what v0.5 wrote
    assert materials.patch_material(mat)  # re-patched to v2
    assert all(n[materials.TAG] == materials.PATCH_VERSION for n in tagged(mat))
    assert materials.unpatch_material(mat)
    assert snapshot(mat) == before


def test_shader_group_survives_migration():
    from afterglow import nodes
    wave = materials.wave_group()
    old = nodes.get_group()
    old[nodes.TAG] = nodes.VERSION - 1
    nodes.get_group()  # migrates the geometry group
    assert materials.WAVE_GROUP in bpy.data.node_groups and bpy.data.node_groups[materials.WAVE_GROUP] == wave
```

Append to `tests/test_render.py`, before `helpers.run(globals())`:

```python
def test_through_blends_per_pixel_on_one_quad():
    from afterglow import materials, nodes
    scene = bpy.context.scene
    mat = emission_material("m", strength=1.0, color=(1.0, 1.0, 1.0, 1.0))
    me = bpy.data.meshes.new("quad")
    me.from_pydata([(-2, -0.5, 0), (2, -0.5, 0), (2, 0.5, 0), (-2, 0.5, 0)], [], [(0, 1, 2, 3)])
    me.materials.append(mat)
    ob = bpy.data.objects.new("quad", me)
    scene.collection.objects.link(ob)
    mod = ob.modifiers.new("Afterglow", "NODES")
    mod.node_group = nodes.get_group()
    nodes.set_input(mod, "Sound", sound())
    for name, v in (("Use Baked", True), ("Through", True), ("Wave Lo", -2.0), ("Wave Span", 4.0),
                    ("Tap Min", 0.0), ("Tap Max", 1.0)):
        nodes.set_input(mod, name, v)
    for k in range(nodes.TAPS):
        nodes.set_input(mod, f"Baked Tap {k}", k / 7)
    materials.patch_material(mat)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.type, cam.data.ortho_scale = "ORTHO", 4.0
    cam.location = (0, 0, 5)
    scene.collection.objects.link(cam)
    scene.camera = cam
    scene.world = bpy.data.worlds.new("w")
    scene.world.color = (0, 0, 0)
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x, scene.render.resolution_y = 64, 16
    scene.view_settings.view_transform = "Standard"
    scene.render.filepath = os.path.join(tempfile.gettempdir(), "afterglow_through.png")
    at(5)
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(scene.render.filepath)
    w, h = img.size
    row = [img.pixels[((h // 2) * w + x) * 4] for x in range(2, w - 2, 6)]
    bpy.data.images.remove(img)
    # 4 vertices only: a monotonic ramp across the face proves per-pixel blending
    assert all(b >= a - 1e-3 for a, b in zip(row, row[1:])) and row[0] < 0.3 and row[-1] > 0.8, row
```

- [ ] **Step 2: Run, and confirm they fail.** `wave_group` and `PATCH_VERSION` are missing, and the render is flat.

- [ ] **Step 3: Edit `afterglow/materials.py`.**
  - Add `import bpy` at the top, and constants after `TAG`:

```python
PATCH_VERSION = 2  # 2: Strength/Color come from the Afterglow Wave shader group
WAVE_GROUP = "Afterglow Wave"
TAPS = 8
```

  - In `_new`, change `n[TAG] = 1` to `n[TAG] = PATCH_VERSION`.
  - Add the shader group builder:

```python
def wave_group():
    """Shared shader group: per-pixel wave level/color for Through Objects, else pass-through."""
    return next((g for g in bpy.data.node_groups if g.bl_idname == "ShaderNodeTree"
                 and g.get(TAG) == PATCH_VERSION), None) or _build_wave()


def _build_wave():
    ng = bpy.data.node_groups.new(WAVE_GROUP, "ShaderNodeTree")
    ng[TAG] = PATCH_VERSION
    ng.interface.new_socket("Level", in_out="OUTPUT", socket_type="NodeSocketFloat")
    ng.interface.new_socket("Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    N, L = ng.nodes, ng.links
    out = N.new("NodeGroupOutput")

    def op(operation, a, b=0.0):
        n = N.new("ShaderNodeMath")
        n.operation = operation
        for socket, v in zip(n.inputs, (a, b)):
            if isinstance(v, bpy.types.NodeSocket):
                L.new(v, socket)
            else:
                socket.default_value = v
        return n.outputs[0]

    def clamp01(x):
        return op("MINIMUM", op("MAXIMUM", x, 0.0), 1.0)

    def attr(name, output):
        a = N.new("ShaderNodeAttribute")
        a.attribute_type = "GEOMETRY"
        a.attribute_name = name
        return a.outputs[output]

    def xyz(name):
        s = N.new("ShaderNodeSeparateXYZ")
        L.new(attr(name, "Vector"), s.inputs[0])
        return s.outputs

    def vmath(operation, a, b=None):
        n = N.new("ShaderNodeVectorMath")
        n.operation = operation
        L.new(a, n.inputs[0])
        if b is not None:
            L.new(b, n.inputs[1])
        return n

    def mix(factor, a, b):
        m = N.new("ShaderNodeMix")
        m.data_type = "RGBA"
        L.new(factor, m.inputs[0])
        L.new(a, m.inputs[6])
        L.new(b, m.inputs[7])
        return m.outputs[2]

    p = N.new("ShaderNodeNewGeometry").outputs["Position"]  # world space, per pixel
    lo, span, radial = xyz("ar_wave_lsr")
    rev, tmin, tmax = xyz("ar_wave_rtt")
    t0, t1, t2 = xyz("ar_taps0"), xyz("ar_taps1"), xyz("ar_taps2")
    taps = [t0[0], t0[1], t0[2], t1[0], t1[1], t1[2], t2[0], t2[1]]
    through = t2[2]  # 0 when the attributes are absent (Through or Fast Playback off)
    along = vmath("DOT_PRODUCT", p, attr("ar_wave_axis", "Vector")).outputs["Value"]
    radius = vmath("LENGTH", vmath("SUBTRACT", p, attr("ar_wave_origin", "Vector")).outputs["Vector"]).outputs["Value"]
    dist = op("ADD", along, op("MULTIPLY", radial, op("SUBTRACT", radius, along)))
    pos = clamp01(op("DIVIDE", op("SUBTRACT", dist, lo), op("MAXIMUM", span, 1e-6)))
    pos = op("ADD", pos, op("MULTIPLY", rev, op("SUBTRACT", 1.0, op("MULTIPLY", pos, 2.0))))
    u = op("MULTIPLY", clamp01(op("DIVIDE", op("SUBTRACT", pos, tmin),
                                  op("MAXIMUM", op("SUBTRACT", tmax, tmin), 1e-6))), TAPS - 1)
    blend = None
    for k, tap in enumerate(taps):  # hat weights = linear interpolation between neighbouring taps
        term = op("MULTIPLY", tap, op("MAXIMUM", op("SUBTRACT", 1.0, op("ABSOLUTE", op("SUBTRACT", u, float(k)))), 0.0))
        blend = term if blend is None else op("ADD", blend, term)
    level = attr("ar_level", "Fac")
    L.new(op("ADD", level, op("MULTIPLY", through, op("SUBTRACT", blend, level))), out.inputs["Level"])
    wave_color = mix(blend, attr("ar_color_a", "Vector"), attr("ar_color_b", "Vector"))
    L.new(mix(through, attr("ar_color", "Color"), wave_color), out.inputs["Color"])
    for i, n in enumerate(N):  # ponytail: one row so the tree is at least untangled if opened
        n.location = (i * 180, 0)
    return ng
```

  - Replace `patch_material` with:

```python
def _patch_version(mat):
    return min(n[TAG] for n in mat.node_tree.nodes if n.get(TAG))


def patch_material(mat):
    if not has_emission(mat):
        return False
    if is_patched(mat):
        if _patch_version(mat) >= PATCH_VERSION:
            return False
        unpatch_material(mat)  # older patch layout: rebuild it
    nt = mat.node_tree
    group = wave_group()
    for node, strength, color in list(_targets(nt)):
        wave = _new(nt, "ShaderNodeGroup", node, 500)
        wave.node_tree = group
        tint = _new(nt, "ShaderNodeAttribute", node, 500, 200)
        tint.attribute_type = "GEOMETRY"
        tint.attribute_name = "ar_tint"
        mul = _new(nt, "ShaderNodeMath", node, 250)
        mul.operation = "MULTIPLY"
        nt.links.new(wave.outputs["Level"], mul.inputs[1])
        _route(nt, mul, 0, mul.outputs[0], node, strength)
        mix = _new(nt, "ShaderNodeMix", node, 250, 200)
        mix.data_type = "RGBA"
        nt.links.new(tint.outputs["Fac"], mix.inputs[0])
        nt.links.new(wave.outputs["Color"], mix.inputs[7])
        _route(nt, mix, 6, mix.outputs[2], node, color)
    return True
```

    Note that `_targets` already skips tagged nodes, and `unpatch_material` removes every tagged node, including group nodes.

- [ ] **Step 4: Run all suites.** Expected: materials 9/9, render 2/2, everything else green.
- [ ] **Step 5: Commit:** `feat: per-pixel Afterglow Wave shader group; material patch v2 with re-patching`

---

### Task 4: Bake taps, signature, Remove, panel

**Files:** `afterglow/bake.py`, `afterglow/ui.py`, `afterglow/blender_manifest.toml`, `tests/test_bake.py`

**Interfaces:**
- Consumes: `nodes.BAKE_INPUTS`, `nodes.TAPS`, `Tap Min` / `Tap Max`, `core.wave`.
- Produces: `bake_all` keys `Baked Tap 0..7` when Through is on. `unkey` removes every bake curve. The signature includes Through, Tap Min and Tap Max.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_bake.py`, before `helpers.run(globals())`:

```python
def through_stage(specs, n=21, **settings):
    scene = bpy.context.scene
    coll = bpy.data.collections.new("Bars")
    scene.collection.children.link(coll)
    for name, x0, x1 in specs:
        me = bpy.data.meshes.new(name)
        me.from_pydata([(x0 + (x1 - x0) * i / (n - 1), 0.0, 0.0) for i in range(n)], [], [])
        me.materials.append(emission_material("mat_" + name))
        coll.objects.link(bpy.data.objects.new(name, me))
    s = scene.afterglow
    s.collection, s.sound, s.ripple_from = coll, sound(), "X"
    s.ripple = True
    for k, v in settings.items():
        setattr(s, k, v)
    core.apply(scene)
    s.through = True
    s.fast_playback = True
    return scene


def tap_curve(name, k):
    ob = bpy.data.objects[name]
    path = nodes._socket(core.find_modifier(ob), f"Baked Tap {k}").path_from_id("value")
    return next(fc for fc in helpers.fcurves(ob) if fc.data_path == path)


def test_through_seam_taps_match():
    through_stage([("left", 0.0, 10.0), ("right", 10.0, 20.0)])
    left, right = tap_curve("left", 7), tap_curve("right", 0)  # both sit at wave position 0.5
    for f in range(1, 80, 3):
        assert abs(left.evaluate(f) - right.evaluate(f)) < 1e-5, (f, left.evaluate(f), right.evaluate(f))


def test_through_taps_lag_along_the_bar():
    through_stage([("bar", 0.0, 20.0)], beats_per_sweep=0.5)
    f = 5  # bass onset: the near end of the bar is lit before the far end
    vals = [tap_curve("bar", k).evaluate(f) for k in range(nodes.TAPS)]
    assert all(b <= a + 1e-6 for a, b in zip(vals, vals[1:])) and vals[0] > vals[-1] + 0.1, vals


def test_through_remove_clears_taps():
    scene = through_stage([("bar", 0.0, 20.0)])
    bake.bake_all(scene)  # re-bake must not duplicate tap curves
    ob = bpy.data.objects["bar"]
    paths = [fc.data_path for fc in helpers.fcurves(ob)]
    assert len(paths) == len(set(paths)) == 1 + nodes.TAPS, paths
    core.remove(scene)
    assert not helpers.fcurves(ob), [fc.data_path for fc in helpers.fcurves(ob)]


def test_through_toggle_keeps_bake_current():
    scene = through_stage([("bar", 0.0, 20.0)])
    assert bake.outdated(scene) == []
    scene.afterglow.through = False
    assert bake.outdated(scene) == []
```

- [ ] **Step 2: Run, and confirm they fail.** No tap curves exist (`StopIteration` in `tap_curve`), and the path count is 1.

- [ ] **Step 3: Edit `afterglow/bake.py`.**
  - Replace `_path`, `_bake_fcurve` and `unkey` with:

```python
def _path(mod, name="Baked Peak"):
    return nodes._socket(mod, name).path_from_id("value")


def _bake_fcurve(ob, mod, name="Baked Peak"):
    ad = ob.animation_data
    if not (ad and ad.action):
        return None, None
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    path = _path(mod, name)
    return bag, bag and next((fc for fc in bag.fcurves if fc.data_path == path), None)


def unkey(ob, mod):
    """Delete only Afterglow's bake curves; the object's other animation stays."""
    for name in nodes.BAKE_INPUTS:
        bag, fc = _bake_fcurve(ob, mod, name)
        if fc:
            bag.fcurves.remove(fc)
    ob.pop(SIG, None)
```

  - Replace `_write` (keep `_own_action` unchanged) with:

```python
def _write(ob, mod, frames, values, name="Baked Peak"):
    sock = nodes._socket(mod, name)
    sock.value = float(values[0])
    sock.keyframe_insert("value", frame=float(frames[0]))
    _, fc = _bake_fcurve(ob, mod, name)
    fc.keyframe_points.add(len(frames) - 1)
    co = np.empty(2 * len(frames), np.float32)
    co[0::2], co[1::2] = frames, values
    fc.keyframe_points.foreach_set("co", co)
    fc.update()
```

  - Replace `_times` with:

```python
def _times(m, frames, fps, beat, pos=None):
    """Audio time each frame samples for this modifier: delay plus the ripple lag."""
    def g(name):
        return nodes.get_input(m, name)
    pos = g("Ripple Position") if pos is None else pos
    lag = float(g("Ripple")) * pos * g("Beats per Sweep") * beat
    return (frames - g("Start Frame")) / fps - g("Delay") - lag
```

  - In `signature`, extend the names to `("Low", "High", "Decay", "Delay", "Ripple", "Ripple Position", "Beats per Sweep", "Start Frame", "Through", "Tap Min", "Tap Max")`.
  - In `bake_all`, change the tail term `nodes.get_input(m, "Ripple Position")` to `1.0`, since every position is at most 1. Then replace the per-object loop with:

```python
    for ob, m in zip(objs, mods):
        _own_action(ob)
        unkey(ob, m)
        amp = series[bands.index(_band_of(m))]
        decay = nodes.get_input(m, "Decay")
        _write(ob, m, frames, held_peak(times, amp, _times(m, frames, fps, beat), decay))
        if nodes.get_input(m, "Through"):
            tmin, tmax = nodes.get_input(m, "Tap Min"), nodes.get_input(m, "Tap Max")
            for k in range(nodes.TAPS):
                pos = tmin + (tmax - tmin) * k / (nodes.TAPS - 1)
                _write(ob, m, frames, held_peak(times, amp, _times(m, frames, fps, beat, pos), decay),
                       name=f"Baked Tap {k}")
        ob[SIG] = signature(m, scene)
```

- [ ] **Step 4: Edit `afterglow/ui.py`.** Inside the panel's `if s.ripple:` block, after the `ripple_origin` lines, add:

```python
            col.prop(s, "through")
            if s.through and not s.fast_playback:
                hint = col.row()
                hint.alert = True
                hint.label(text="Through Objects needs Fast Playback", icon="INFO")
```

- [ ] **Step 5: Run all seven suites.** Expected: bake 18/18, everything else green.
- [ ] **Step 6: Bump the manifest to `0.6.0`, rebuild the zip, and commit:** `feat: bake Through Objects taps; panel toggle and hint`
- [ ] **Step 7: Real-song check.**
  - Use 3 single-quad bars (x 0–10, 10–20, 20–30) with the user's song, Ripple from X, Through and Fast Playback on, and calibrate.
  - Render a 96×8 Eevee strip at 8 consecutive frames of a 122 BPM section. Print each frame's brightness profile as ASCII and confirm the band moves across the faces and over the seams.
  - Record the result in the ledger.
