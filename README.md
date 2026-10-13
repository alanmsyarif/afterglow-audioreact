# Afterglow (v0.8.2)

Audio-reactive emission for Blender 5.2. Point Afterglow at a collection and a sound
file, and every emissive object in it lights up with the music, each one listening to
its own frequency band. On top of that, **Environment Effects** add stackable,
beat-triggered light layers over the whole venue.

Everything comes from the sound file itself: no live input, no network.

## Install

1. Requires **Blender 5.2** or newer.
2. Download `afterglow-<version>.zip` from [`dist/`](dist/).
3. In Blender: *Edit → Preferences → Get Extensions → ⌄ → Install from Disk…* and pick
   the zip (or drag the zip onto the Blender window).
4. Open the 3D Viewport sidebar (`N`) → **Afterglow** tab.

## Quick start

1. Put your glowing objects in one collection. Afterglow reacts on any mesh, curve or
   text object whose material has an **Emission** shader, or a **Principled BSDF** with
   Emission Strength above 0.
2. In the *Afterglow* panel pick the **Collection** and the **Sound** (the folder button
   loads a file; sounds packed into the `.blend` work too). Set **Start Frame** to where
   the music begins.
3. Press **Apply**. Afterglow adds a geometry-nodes modifier to each object, routes its
   emission through the audio level, and spreads frequency bands from bass to treble
   across the objects (sorted by name). It also adds the song as a sound strip, turns on
   *Sync to Audio*, extends the frame range to cover the song, and detects the tempo.
4. Press **Calibrate**. It measures the song and sets each object's Threshold and Gain
   so quiet parts go dark and peaks reach full brightness.
5. Turn on **Fast Playback** for smooth viewport playback (see below).
6. **Remove** takes the modifiers off and restores your original materials exactly.

## Scene controls

| Control | What it does |
|---|---|
| Ripple | Every kick sweeps across the collection by position instead of lighting everything at once. The sweep follows the song's detected tempo. |
| Beats per Sweep | How many beats one sweep takes to cross the collection. |
| Ripple From | Where the sweep starts: Longest Axis, X, Y, Z, an **Empty** (outward from an Origin object), or the Collection Center. Reverse flips it. |
| Through Objects | The wave crosses each object's surface pixel by pixel instead of lighting the whole object at once. Needs Fast Playback. |
| Fast Playback | Precomputes the reaction with numpy and keys it, so playback never evaluates Blender's slow Sample Sound node. Settings changes re-bake automatically once you stop dragging; after editing bands, Decay or Delay in the modifier, use the *Re-bake* button the panel shows. |
| Re-spread | Spreads bands bass → treble again across objects without **Lock Band**, and picks up moved objects or a moved Origin. |

## Per-object controls

Select an Afterglow object to tune it in the panel:

| Control | What it does |
|---|---|
| Lock Band | Keep this object's band when re-spreading. |
| Low / High | The frequency band (Hz) this object listens to. |
| Gain / Threshold | Level = (amplitude − Threshold) × Gain. While Ripple is on, the kick band's own Ripple Gain / Ripple Threshold pair is shown instead. |
| Decay | How long a peak takes to fade (seconds). |
| Delay | Shift this object's reaction later in time (seconds). |
| Use Colors / Color A / Color B | Tint from Color A (quiet) to Color B (loud). |
| Level Meter | Fill the object along an axis by loudness, like an equalizer bar (off while Through Objects is on). |

## Environment Effects

Audio-only environment effects for the whole venue: independent, stackable light layers
triggered by onsets in the track. Generated effects are **real lights only** (point and
spot lamps with keyed energy, location and rotation). No meshes, materials or other
geometry are ever added, so your venue stays exactly as you built it and the look comes
from real light spill on your existing objects.

### Layers

| Layer | What it does | Default trigger band |
|---|---|---|
| Data Strikes | Spots that snap on over the venue top and shine straight down for ground spill | Low |
| Data Blooms | Soft pulses of light that swell and fade inside the venue | Low |
| Rapid Flashes | Very short whole-venue bursts from the centre | Low |
| Travelling Light Trails | A bright moving light riding the venue rim path | Low |
| Downward Trails | A smooth cascade of lights falling from the venue top to the ground | Low |
| Gun Bursts | Short, fast, directed spots fired from the rim toward the centre | Low |

Layers are **not** a dropdown: enable any combination and they play simultaneously.
Each layer has its own Weight, Intensity, Color, Timing and Trigger Band. The floats are
keyframeable on the Scene, and Build samples their animation per event, so you can bring
layers in gradually as the track builds (e.g. keyframe Weight 0 → 1).

### Venue shapes

`Venue Shape` routes emitters in world space over the shared venue bounds (fitted to the
Afterglow collection, never per object):

- **Circular**: trails ride a circle; spawns fill the disc.
- **Square**: trails ride a square rim; spawns fill the square.
- **Rectangular**: trails ride the venue bounds' own rectangle.
- **Triangular**: trails ride a triangle; spawns fill the triangle.

### Usage

1. Pick your Collection and Sound in the *Afterglow* panel as usual.
2. Open the *Afterglow Environment Effects* panel, choose the venue shape, enable layers,
   and set colours and bands.
3. **Build Effects** analyses the sound once, then keys pooled real point/spot lights into
   a collection named `Afterglow Effects` in the current scene.
4. Scrub or play. The effects are plain F-curve animation on a layered action, so they
   render in any engine and never touch the Sample Sound node.
5. **Remove Effects** deletes this scene's effects collection and every datablock it owns
   (tagged `afterglow_effects`). Your objects, materials and animation, and other scenes'
   effects, are never modified.

Changing settings does not silently re-run: the panel shows *Build outdated* until you
press Build again. The same sound, settings and Seed always produce the same show.

## Demo

Repeatable generator (synthetic pulse fixture track, circular pillar venue, all six
layers, saved `.blend`, short sequential EEVEE motion proof):

```bash
blender -b --factory-startup --python-exit-code 1 --python tools/build_effects_demo.py
# clip + contact sheet from the rendered frames:
ffmpeg -framerate 24 -i <OUT>/frames/%04d.png -c:v libx264 -pix_fmt yuv420p <OUT>/motion.mp4
ffmpeg -i <OUT>/frames/%04d.png -vf "select='not(mod(n,8))',scale=320:-1,tile=4x3" \
    -frames:v 1 <OUT>/contact_sheet.png
```

(`blender` is your Blender 5.2 executable, e.g.
`"C:/Program Files/Blender Foundation/Blender 5.2/blender.exe"` on Windows.)

## Tests

Each `tests/test_*.py` file is a standalone headless suite. Run one with Blender:

```bash
blender -b --factory-startup --python-exit-code 1 --python tests/test_effects.py
```

Or run them all without installing Blender, using the `bpy` module from PyPI (Python 3.13).
This is what CI does on every push:

```bash
python3.13 -m venv .venv && . .venv/bin/activate
pip install bpy==5.2.2
for t in tests/test_*.py; do python "$t" || break; done
```

On Linux without a GPU, `tests/test_render.py` needs Mesa's software OpenGL
(`apt install libegl1 libgl1-mesa-dri`).

## Limitations (honest)

- Generated effects are **lights only**: Build never adds meshes, materials or any other
  geometry, so it cannot clutter or alter your venue. What you see is real light spill on
  your existing objects and floor.
- Because no geometry is generated, effects have no visible "beam" or streak shape of
  their own (the reference video's thin segmented shafts and beaded trails are a
  geometry/volumetric look). Downward Trails read as a smooth cascade of falling points
  of light instead; Strikes and Gun Bursts read as aimed spots.
- The effect timing and placement are an **interpretation** of the reference video's
  look (cool selective light in a dark venue).
- Effect lights have shadows disabled (dozens of shadow-casting lights would crawl); they
  provide environmental spill only.
- Keyframed layer controls are sampled **per event at Build time**: re-Build after
  animating Weight/Intensity/Timing. The *Build outdated* hint does not track keyframe
  edits or moved venue objects, only static settings.
- Event detection is onset-driven per trigger band and tempo-aware when the track
  provides enough repeated pulses to fit a reliable grid. Sparse, sustained, or ambiguous
  material stays onset-driven rather than forcing a guessed beat; silence produces no
  events.
- Pools are bounded (2–6 slots per layer; a Downward Trail event uses a cascade of 3
  lights in one slot). When every slot is still lit, a new event takes over the one
  closest to finishing: that event fades out quickly and the new one starts cleanly,
  rather than spawning unbounded lights.
- Blender 5.2 crashes when the Sample Sound node evaluates a missing file, so Afterglow
  never hands a missing sound to its modifiers; relink or pack the sound instead.
