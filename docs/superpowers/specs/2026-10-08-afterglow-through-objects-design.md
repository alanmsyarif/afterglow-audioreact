# Afterglow — Through Objects (addendum)

Date: 2026-10-08
Status: approved 2026-10-08; revised the same day for per-pixel blending (user choice)
Extends: the base, ripple and fast-playback specs in this folder.

## Goal

While Ripple is on, the wave travels *across each object's surface*, point by point, instead
of lighting a whole object at once. Neighbouring objects continue the same wave seamlessly.

## User decisions

- Effect: "wave passes through" — the collection ripple becomes continuous in space.
- Approach: baked taps. The effect needs Fast Playback. With Fast Playback off, objects still
  light whole, and the panel says "Through Objects needs Fast Playback".

## Verified facts (2026-10-08, Blender 5.2)

- `Sample Sound Frequencies` accepts a per-point (field) Time. An exact live version would
  need a sound lookup per point on a node that already costs 35–140 ms, so it was rejected.
- The world-space point position is
  `FunctionNodeTransformPoint(Position, GeometryNodeObjectInfo(GeometryNodeSelfObject).Transform)`.
  It follows object moves.
- `GeometryNodeIndexSwitch` (`data_type = "FLOAT"`, `index_switch_items.new()`) selects
  per point. An index **outside 0..N-1 returns 0, not a clamped value**, so callers must clamp.

## Design

### Wave coordinate (shared by Python and the node group)

- The metric `d(p)` for a world point `p` follows Ripple From:
  - Axis modes: `dot(p, axis)`. The axis is a unit X/Y/Z vector; Longest Axis picks the axis
    with the largest spread of object origins, exactly as `place()` does now.
  - Radial modes: `|p - origin|`. The origin is the Origin object's location for Empty, or the
    mean of the object origins for Collection Center.
- `pos(p) = clamp((d(p) - Lo) / Span, 0, 1)`, flipped to `1 - pos` when Reverse is on.
- `Lo` and `Span` come from the **world positions of every vertex of every Afterglow object**
  (evaluated mesh), so the wave covers the whole surface.
- Per object, `Tap Min` and `Tap Max` are the object's own `pos` range. After a Reverse flip,
  min and max swap.

### Node group v5 (append-only inputs)

- Scene-wide inputs: `Through` (Bool), `Wave Radial` (Bool), `Wave Axis` (Vector, default
  (1,0,0)), `Wave Origin` (Vector), `Wave Lo` (Float, **negative allowed**), `Wave Span`
  (Float), and `Wave Reverse` (Bool).
- Per-object inputs: `Tap Min` and `Tap Max` (Float 0..1), and `Baked Tap 0` to
  `Baked Tap 7` (Float).
- When `Through` **and** `Use Baked` are on, a lazy Switch also stores 9 FLOAT_VECTOR point
  attributes. They are constant per object; every point gets the same values:
  - `ar_taps0` = levels of taps 0, 1, 2
  - `ar_taps1` = levels of taps 3, 4, 5
  - `ar_taps2` = (level 6, level 7, through flag = 1)
  - `ar_wave_axis`, `ar_wave_origin`
  - `ar_wave_lsr` = (Lo, Span, Radial)
  - `ar_wave_rtt` = (Reverse, Tap Min, Tap Max)
  - `ar_color_a`, `ar_color_b`
  - A tap *level* is `max(tap - threshold, 0) * gain`, using the same gain pair as the object
    level (Ripple Gain/Threshold while rippling).
- Otherwise these attributes are absent. The shader reads 0 for them, so the through flag is 0.
- Migration as before: v1–v4 groups move to v5, keeping values and the tempo curve.

### Shader side: shared `Afterglow Wave` shader node group

- It is built once by Python, tagged, and has outputs `Level` (Float) and `Color` (Color).
- Per shading point it computes:
  - `p` = Geometry → Position, which is world space and matches Python's metric.
  - `d = dot(p, axis)`, or `|p - origin|` when radial.
  - `pos = clamp((d - Lo) / Span, 0, 1)`, flipped when Reverse is on.
  - `u = clamp((pos - TapMin) / (TapMax - TapMin), 0, 1) * 7`.
  - `blend = sum_k level_k * max(0, 1 - |u - k|)`, which is exact linear interpolation between
    neighbouring taps.
- `Level = mix(ar_level, blend, through)`.
- `Color = mix(ar_color, mix(ar_color_a, ar_color_b, clamp(blend)), through)`.
- **Material patch v2:** each emission target's Strength multiplier and color Mix read the Wave
  group's `Level` and `Color` outputs. `ar_tint` stays an Attribute node.
  - Patch nodes carry `afterglow = 2`.
  - Materials patched by v1 (`afterglow = 1`) are unpatched and re-patched on the next Apply.
  - Remove deletes only the tagged nodes in the material; the shared group stays.

### Python

- `core.wave(scene, objs)` computes the metric parameters, `Lo` and `Span`, and per-object tap
  ranges from evaluated world vertices. It writes these to the modifiers.
  - It runs from Apply and `replace()` (Ripple From, Origin, Reverse or Through changes, and
    Re-spread) when Through is on.
- `bake_all`, when Through is on: for each object and each tap `k` in 0..7,
  `pos_k = TapMin + (TapMax - TapMin) * k / 7` gives
  `lag_k = Ripple * pos_k * BeatsPerSweep * BeatLength`. The held peak at that lag is keyed
  onto `Baked Tap k`. `Baked Peak` is still baked, so Through off needs no re-bake.
- The signature adds Through, Tap Min and Tap Max. `unkey` removes the tap curves too.
- New scene property `through` (Bool, "Through Objects"). Its update re-runs wave placement
  and re-bakes. Sync writes `Through`.
- Panel: a "Through Objects" checkbox under the Ripple row. When it's on and Fast Playback is
  off, a hint row reads "Through Objects needs Fast Playback".

## Error handling and limits

| Case | Behavior |
|---|---|
| Through on, Fast Playback off | Whole-object lighting (as before) plus the hint |
| Ripple off, Through on | All taps share one lag, so it looks like whole-object lighting |
| Object moved after Apply | Wave coordinate is live (world position), but Lo/Span/tap ranges update on Re-spread or Apply, like Ripple Position |
| Mesh edited after Apply | Same: Re-spread updates the tap ranges |
| Very large single object spanning the whole sweep | 8 taps across it, about 1/8 beat each, linearly blended |

Known limits:
- Live (unbaked) preview has no through effect.
- Tap ranges use the evaluated mesh at Apply or Re-spread time; deforming animation isn't
  tracked.

## Testing

- Nodes:
  - With Through and Use Baked on, the stored attributes carry tap *levels* (gain and
    threshold applied), the wave parameters and the through flag.
  - With either one off, the attributes are absent.
- Materials:
  - Patch v2 routes Strength and Color through the Wave group.
  - A v1-patched material is re-patched on Apply.
  - Remove restores the original exactly.
- Core: X mode with a long bar plus a small cube gives the expected Lo, Span and tap ranges;
  radial ranges are correct; Reverse flips the ranges.
- Bake:
  - Taps are keyed, and tap `k` equals the held peak at `pos_k`.
  - Seam: the left bar's tap 7 curve equals the right bar's tap 0 curve exactly.
  - Re-bake and Remove leave no duplicate or leftover tap curves.
  - Toggling Through keeps the bake current.
- Render: a single 4-vertex quad with ramped tap levels renders monotonically brighter across
  the face. This proves per-pixel blending, which per-vertex could not do on 4 vertices.
