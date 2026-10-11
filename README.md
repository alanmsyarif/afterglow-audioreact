# Afterglow Environment Effects (v0.8.2)

Audio-only environment effects for the whole venue: independent, stackable light
layers triggered by onsets in the track. Generated effects are **real lights only**
(point/spot lamps with keyed energy, location and rotation) no meshes, materials
or other geometry are ever added, so your venue stays exactly as you built it and
the look comes from real light spill on your existing objects. No weather, no
network everything comes from the sound file you already picked in the Afterglow
panel.

## Layers

| Layer | What it does | Default trigger band |
|---|---|---|
| Data Strikes | Spots that snap on over the venue top and shine straight down for ground spill | Low |
| Data Blooms | Soft pulses of light that swell and fade inside the venue | Low |
| Rapid Flashes | Very short whole-venue bursts from the centre | Low |
| Travelling Light Trails | A bright moving light riding the venue rim path | Low |
| Downward Trails | A smooth cascade of lights falling from the venue top to the ground | Low |
| Gun Bursts | Short, fast, directed spots fired from the rim toward the centre | Low |

Layers are **not** a dropdown: enable any combination and they play simultaneously.
Each layer has its own Weight, Intensity, Color, Timing and Trigger Band. Those
floats are keyframeable on the Scene Build samples their animation per event, so
you can bring layers in gradually as the track builds (e.g. keyframe Weight 0 → 1).

## Venue shapes

`Venue Shape` genuinely routes emitters in world space over the shared venue bounds
(fitted to the Afterglow collection, never per-object):

- **Circular** trails ride a circle; spawns fill the disc.
- **Square** trails ride a square rim; spawns fill the square.
- **Rectangular** trails ride the venue bounds' own rectangle.
- **Triangular** trails ride a triangle; spawns fill the triangle.

## Usage

1. Pick your Collection and Sound in the *Afterglow* sidebar panel as usual.
2. Open the *Afterglow Environment Effects* panel, choose the venue shape, enable
   layers, set colours/bands.
3. **Build Effects** analyses the sound once, then keys pooled real point/spot
   lights into a collection named `Afterglow Effects`. Only lights are generated:
   no meshes or materials are ever created, so existing scenes are never altered.
4. Scrub/play. The effects are plain F-curve animation on a layered action, so they
   render in any engine and never touch the Sample Sound node.
5. **Remove Effects** deletes the collection and every datablock it owns (tagged
   `afterglow_effects`). Your objects, materials and animation are never modified.

Changing settings does not silently re-run: the panel shows *Build outdated* until
you press Build again. Same sound + settings + Seed always produces the same show.

## Demo

Repeatable generator (synthetic pulse fixture track, circular pillar venue, all six
layers, saved `.blend`, short sequential EEVEE motion proof):

```bash
"C:/Program Files/Blender Foundation/Blender 5.2/blender.exe" -b --factory-startup \
    --python-exit-code 1 --python tools/build_effects_demo.py
# clip + contact sheet from the rendered frames:
ffmpeg -framerate 24 -i <OUT>/frames/%04d.png -c:v libx264 -pix_fmt yuv420p <OUT>/motion.mp4
ffmpeg -i <OUT>/frames/%04d.png -vf "select='not(mod(n,8))',scale=320:-1,tile=4x3" \
    -frames:v 1 <OUT>/contact_sheet.png
```

## Tests

```bash
"C:/Program Files/Blender Foundation/Blender 5.2/blender.exe" -b --factory-startup \
    --python-exit-code 1 --python tests/test_effects.py
```

## Limitations (honest)

- Generated effects are **lights only**: Build never adds meshes, materials or any
  other geometry, so it cannot clutter or alter your venue. What you see is real
  light spill on your existing objects and floor.
- Because no geometry is generated, effects have no visible "beam" or streak shape
  of their own (the reference video's thin segmented shafts and beaded trails are a
  geometry/volumetric look). Downward Trails read as a smooth cascade of falling
  points of light instead; Strikes and Gun Bursts read as aimed spots.
- The effect timing and placement are an **interpretation** of the reference
  video's look (cool selective light in a dark venue).
- Effect lights have shadows disabled (dozens of shadow-casting lights would crawl);
  they provide environmental spill only.
- Keyframed layer controls are sampled **per event at Build time** re-Build after
  animating Weight/Intensity/Timing. The *Build outdated* hint does not track
  keyframe edits, only static settings.
- Event detection is onset-driven per trigger band and tempo-aware when the track provides
  enough repeated pulses to fit a reliable grid. Sparse, sustained, or ambiguous material
  stays onset-driven rather than forcing a guessed beat; silence produces no events.
- Pools are bounded (2–6 slots per layer; a Downward Trail event uses a cascade of
  3 lights in one slot); overlapping events beyond the pool share slots rather than
  spawning unbounded lights.
