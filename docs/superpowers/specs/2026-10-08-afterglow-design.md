# Afterglow — Audio-Reactive Emission for Blender 5.2

Date: 2026-10-08
Status: design approved in chat, awaiting spec review

## Goal

Make every emissive object in one collection react to an audio file, with each object
reacting differently (frequency band, gain, threshold, decay, delay, color shift), built
on Blender 5.2's native `Sample Sound Frequencies` geometry node.

Output target: rendered animation (Eevee and Cycles). Not a live performance tool.

## Verified platform facts (Blender 5.2.0 LTS, probed headless 2026-10-08)

`GeometryNodeSampleSoundFrequencies`, geometry nodes only:

| Socket | Type | Default |
|---|---|---|
| Sound (in) | NodeSocketSound | — |
| Time (in) | NodeSocketFloatTimeAbsolute | 0.0 |
| All Channels (in) | Bool | True |
| Channel (in) | Int | 0 |
| Low (in) | NodeSocketFloatFrequency | 0.0 |
| High (in) | NodeSocketFloatFrequency | 10000.0 |
| FFT Size (in) | Menu | "4096" |
| Window Function (in) | Menu | "Hann" |
| Amplitude (out) | Float | — |

Consequences:
- Shader trees have no sound node. Audio must be bridged from geometry nodes to the
  material through named attributes.
- Input is a Sound data-block (file). No live microphone input.
- No built-in smoothing or gain; Afterglow implements both.

## Non-goals

Live audio input, presets, spectrum preview UI, non-emission reactions (scale,
displacement), light objects.

## Architecture

Blender 5.2 extension, package `afterglow`, at `C:\Users\user\Documents\afterglow\`.

### 1. `AudioReact` geometry node group

Built from Python, one shared group named `Afterglow AudioReact`, used as a modifier on
every target object.

Group inputs (all exposed on the modifier):

| Input | Type | Default | Scope |
|---|---|---|---|
| Geometry | Geometry | — | — |
| Sound | Sound | — | scene-wide, written by addon |
| Start Frame | Int | 1 | scene-wide, written by addon |
| Low | Float (Hz) | 20 | per object |
| High | Float (Hz) | 200 | per object |
| Gain | Float | 1.0 | per object |
| Threshold | Float | 0.0 | per object |
| Decay | Float (s) | 0.15 | per object |
| Delay | Float (s) | 0.0 | per object |
| Use Colors | Bool | False | per object (False keeps the material's own color) |
| Color A | Color | white | per object |
| Color B | Color | white | per object |

Evaluation:
1. `t = (Frame - Start Frame) * (Seconds / Frame) - Delay`, using the Scene Time node. Geometry nodes cannot read
   fps, so `Seconds / Frame` recovers it. Known ceiling: at frame 0 that is 0/0 = 0, so frame 0 samples the
   audio start. Samples with `t < 0` are gated to 0, so frames before the audio starts stay dark.
2. Smoothing: a Repeat Zone with N = 8 iterations, step = `decay / 4` (fixed count, so
   cost is predictable). Iteration i samples amplitude at `t - i*step`, weights it by
   `exp(-(i*step) / decay)`, and keeps the running max. This gives peak-hold with
   exponential release. The divisor is `max(decay, 1e-4)` so decay = 0 never divides
   by zero. With decay = 0 the step is 0, so all samples land on `t`, which equals no
   smoothing.
3. `level = max(0, amp - threshold) * gain`.
4. `color = mix(Color A, Color B, clamp(level, 0, 1))`.
5. Realize Instances, then Store Named Attribute `ar_level` (float), `ar_color` (color) and
   `ar_tint` (float, from Use Colors), all on the point domain. Geometry otherwise passes through unchanged.

Fallback, if spike task 1 shows the node misbehaves in a Repeat Zone: 8 unrolled sample
nodes.

### 2. Material patcher

For each material slot of each target object:
- Find every `ShaderNodeEmission` and every `ShaderNodeBsdfPrincipled` with emission strength > 0.
- Strength input becomes `original_strength * ar_level`. The original value or link is preserved
  as the multiply's first input.
- Color input becomes `mix(original color, ar_color, ar_tint)`. The color choice therefore lives
  per object in the modifier, and shared materials stay correct.
- Every inserted node is tagged with the custom property `afterglow = 1` and labelled
  "Afterglow". No frame is used, because targets can be scattered across the tree. Each patch
  node stores its target node name, socket identifier and original-value input index, so Remove
  can restore the original.
- Shared materials work per object, because attributes come from each object's own
  evaluated geometry.
- A material that is already patched is skipped, so patching is idempotent.

### 3. Operators

- `afterglow.apply`: requires a collection and a sound. For each mesh, curve or text object
  in the collection (recursive):
  - No emission found: skip and count it. Report "N objects skipped: no emission".
  - Unsupported type: skip silently.
  - Otherwise add the modifier, or refresh it if one exists (never stack a second), then
    patch the materials.
  - Afterwards run auto-spread over unlocked objects.
- `afterglow.respread`: auto-spread only. Objects are sorted by name. Object k of n
  (unlocked only) gets the band `[f(k), f(k+1)]`, with `f(x) = 20 * (16000/20)^(x/n)`
  (log-spaced, 20 Hz to 16 kHz).
- `afterglow.remove`: removes Afterglow modifiers and tagged material nodes and restores
  the original links and values. Running it twice is harmless.

### 4. Properties and UI

- Scene: `afterglow.collection` (Collection), `afterglow.sound` (Sound),
  `afterglow.start_frame` (Int). An update callback writes Sound and Start Frame into every
  Afterglow modifier.
- Object: `afterglow.locked` (Bool; re-spread skips it).
- N-panel tab "Afterglow":
  - Scene box: the scene properties, plus Apply / Re-spread / Remove buttons.
  - Active-object box: that object's modifier inputs, plus its locked flag.

## Error handling

| Case | Behavior |
|---|---|
| No sound or no collection | Apply refuses with an explicit message |
| Object without emission | Skipped and counted in the report; no modifier added |
| Empty, light, camera | Skipped silently |
| Instances, text, curves | Realize Instances in the group so the attribute reaches the render |
| Re-apply | Refreshes in place, no duplicate modifiers or patches |
| Remove twice | No-op the second time |
| Patched material also used by an object outside the collection | Apply warns by name; that object renders dark because it has no attributes |

## Testing

Headless `blender -b --factory-startup` scripts under `tests/`, one runner, assert-based.

- Fixture: generate `tone.wav` with Python's `wave` module: 100 Hz for 0–1 s, 1 kHz for
  1–2 s, 8 kHz for 2–3 s, 44.1 kHz mono.
- Scene: 3 cubes in a collection, each with an Emission material. Run Apply with
  auto-spread over 3 objects.
- Assertions, reading `ar_level` from the evaluated mesh:
  - At 0.5 s, the bass cube's level is greater than 5× the treble cube's. At 2.5 s it is
    the reverse.
  - At 1.05 s with decay 0.3, the bass level is still > 0. With decay 0 it is about 0.
  - Re-apply leaves the modifier count at 1 and the patch count unchanged.
  - Remove restores each material's node count and links to the pre-apply snapshot.
  - Object without emission: no modifier, and it appears in the skip count.
- Render smoke test: a 64×64 Eevee render of the bass cube at 0.5 s is brighter than at
  1.5 s.

## Risks

1. The node might not evaluate in background mode, or might not accept a varying time
   inside a Repeat Zone. This is plan task 1, a spike; the fallback is to unroll the samples.
2. Cost: 8 FFTs × objects per frame. Acceptable for tens of objects. If this becomes slow,
   lower N or the FFT size.
3. Text and curve objects without fill produce no faces, so nothing renders. This is a
   documented limitation.
