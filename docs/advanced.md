# Noctune Free advanced controls

Open **Noctune** in the 3D Viewport sidebar (`N`) to prepare your objects, apply a
look and build their music reaction with **Build Reaction** or **Update Reaction**.

For the illustrated beginner guide, download the [offline HTML manual](../dist/noctune-free-documentation.zip?raw=true),
extract it and open `index.html`.

Noctune Pro adds generated environment lights, beams, lasers and signal effects.
Those tools are maintained separately and are not included in this public Free
source tree. See [edition details](editions.md) for the Pro workflow and delivery.

## Where the manual tools live

The default workflow follows **Music → Objects → Reaction → Appearance**. Load **Track**,
choose Objects with **Use Selected**, then build the selected Object Look.

Turn on **Advanced Controls** for:

- **Selected Object:** appearance first; expand **Audio Response** for bands,
  Gain, Threshold, Decay and Delay.
- **Ripple Motion:** choose direction and surface travel.
- **Glow:** set **Add Soft Glow on Build**, or add/remove compositor glow manually.
- **My Object Looks:** save, apply, import or export object styling.
- **Playback & Tools:** refresh playback, calibrate or use manual build actions.

**Start Frame** appears in the Music card while Advanced Controls is on.
Build/Update Reaction keeps current appearance and recalibrates audio. Build first,
then choose an optional preset under **Appearance → Apply Look**. Add compositor
glow separately under **Advanced Controls → Glow → Add Glow**.

Free saves portable object looks that also work in Pro without changing
environment settings. Free 0.30.0 imports object-only looks and older complete
formats 1–3. Use an object-only export from current Pro; its complete/environment
format 7 is unsupported by this Free version. **Remove Object Reaction** restores prepared materials and original light
data, while keeping existing generated environment data in the scene.

Existing scenes and custom look files remain usable. Objects from earlier built-in
stages remain ordinary editable scene objects: choose their collection to keep using
them. Your active camera and world stay under your control.
Commands below run from the repository root.

## Rendering

Use Blender's **Output Properties** for resolution, frame range, output format and
destination, then choose **Render → Render Animation**. Playback loops use Blender's
timeline preview range. Object builds preserve native output settings and timeline
ranges. Automatic object glow is skipped when native compositing is disabled;
existing compositor nodes stay in place.

## Prepare your objects

**Use Selected** prepares ordinary meshes, curves and text automatically. Missing
materials receive an emission-ready material. Existing non-emissive materials use
local copies with emission added, retaining their shader connections and textures.
Shared materials and geometry are isolated when needed so unselected objects keep
their appearance. Existing editable emission is reused where safe; repeating the
operation does not keep adding materials or reaction collections.

**Remove Object Reaction** restores the original material assignments for slots still
using automatically prepared materials, including after saving and reopening the
project. Materials you reassigned manually and geometry edits stay in place.

**Use Selected** also accepts **Point**, **Spot**, **Area** and **Sun** lights,
including mixed selections of lights and geometry. Lights need no materials: the
same **Build Reaction** or **Update Reaction** calibrates their music response and
animates native power and color. Preview native lighting in **Rendered** shading.

Each reacting light uses its own light-data copy, so unselected objects sharing the
original data keep their appearance. The light's type, aim, size and transforms stay
unchanged. **Remove Object Reaction** restores its original light data and prior
animation, including after saving and reopening. Light data you replaced manually
stays in place.

Choosing a collection directly accepts lights and geometry with emissive materials
already. Select geometry and use **Use Selected** if its materials need preparation.
Library-linked objects must be made local before creating a reaction.

## Automatic calibration

**Build Reaction** and **Update Reaction** calibrate every Noctune object and light
in the scene, including reactions moved outside the current Objects collection.
The manual **Calibrate** button uses the same analysis. Generated environment
effects keep their separate build workflow.

Calibration measures each frequency band across the full source track. Delay,
Start Frame and Ripple placement affect playback timing without changing the
measured audio. Every source frame is sampled once, and the analysis also checks
peaks between frames.

Normally, Threshold uses the band's median level and Gain maps its 90th percentile
to a target response of 1. Sparse tracks use their active audio when that estimate
has too little range; steady tones receive a stable response. Negligible bands
stay dark rather than receiving extreme gain.

Full-track peaks keep the automatically calibrated response within 4 before
**Intensity**, **Glow**, material strength and light **Base Power** affect the final
brightness. You can still raise Gain or Intensity manually. Calibration does not
set scene exposure or equalize the final appearance of different lights and materials.
**Glow** deliberately leaves a minimum brightness during quiet sections or silence.

If Gain or Threshold has keyframes, a driver or NLA animation, calibration reports
the conflict before changing the scene. While Ripple is enabled, this protection
covers Ripple Gain and Ripple Threshold instead. Remove the conflicting animation
before using automatic calibration; the authored curves remain intact when a build
is rejected.

## Object reaction controls

| Control | What it does |
|---|---|
| Ripple | Every kick sweeps across the collection by position instead of lighting everything at once. The sweep follows the song's detected tempo. |
| Beats per Sweep | How many beats one sweep takes to cross the collection. |
| Ripple From | Where the sweep starts: Longest Axis, X, Y, Z, an **Empty** (outward from an Origin object), or the Collection Center. Reverse flips it. |
| Through Objects | The wave crosses geometry surfaces pixel by pixel instead of lighting the whole object at once. Needs Fast Playback. Lights respond at their origins. |
| Fast Playback | Precomputes the reaction with numpy and keys it, so playback never evaluates Blender's slow Sample Sound node. Settings changes re-bake automatically once you stop dragging; after editing bands, Decay or Delay in the modifier, use the **Refresh Playback** button the panel shows. |
| Spread Bands | Spreads bands bass → treble again across objects without **Lock Band**, and picks up moved objects or a moved Origin. |

Lights always use baked playback, including when **Fast Playback** is off for
geometry. After changing a light's band, Decay or Delay, press **Refresh Playback**. Light origins
participate in Ripple placement; surface-only Level Meter controls apply to geometry.

## Per-object controls

Select a Noctune object and open **Advanced Controls → Selected Object**.
Appearance controls come first. Expand **Audio Response** for its frequency band,
sensitivity and timing:

| Group | Control | What it does |
|---|---|---|
| Audio Response | Low / High | The frequency band (Hz) this object listens to. |
| Audio Response | Lock (padlock) | Keep this object's band when re-spreading. |
| Audio Response | Gain / Threshold | Level = (amplitude − Threshold) × Gain. While Ripple is on, the kick band's own Ripple Gain / Ripple Threshold pair is shown instead. |
| Audio Response | Decay | How long a peak takes to fade (seconds). |
| Audio Response | Delay | Shift this object's reaction later in time (seconds). |
| Appearance | Intensity | Brightness multiplier for geometry emission or native light power. Unlike Gain it leaves the colour mix and Level Meter fill alone. |
| Appearance | Glow | Idle glow: brightness never drops below this level, so quiet parts glow dimly instead of going black. |
| Appearance | Use Colors / Color A / Color B | Tint from Color A (quiet) to Color B (loud). |
| Appearance | Level Meter | Fill geometry along an axis by loudness, like an equalizer bar (off while Through Objects is on; not shown for lights). |

For a selected light, **Base Power** starts at its original native power and sets
the response's brightness scale. Adjust it under **Advanced Controls → Selected
Object**. Power is `Base Power × max(Level, Glow) × Intensity`; Gain can raise it
above Base Power. Sun lights keep Blender's Sun energy units.

In the main tab's advanced controls, open **Ripple Motion** to adjust these settings.

## Tests

Each `tests/test_*.py` file is a standalone headless suite. Run one with Blender:

```bash
blender -b --factory-startup --python-exit-code 1 --python tests/test_core.py
```

Or run them all without installing Blender, using the `bpy` module from PyPI (Python 3.13).
This is what CI does on every push:

```bash
python3.13 -m venv .venv && . .venv/bin/activate
pip install bpy==5.2.2
for t in tests/test_*.py; do python "$t" || break; done
```

On Linux without a GPU, render suites need Mesa's software OpenGL:

```bash
sudo apt install libegl1 libgl1-mesa-dri libglx-mesa0
```

## Releasing

1. Bump `version` in `afterglow/blender_manifest.toml` (and the README title), and commit.
2. Tag the commit with that version and push the tag:

   ```bash
   git tag v0.30.0 && git push origin v0.30.0
   ```

[`release.yml`](../.github/workflows/release.yml) runs the full test suite, checks the tag
matches the manifest, builds and validates the Free ZIP, and publishes it as a
GitHub Release. Pro packages are built from the separate private Pro repository
for paid delivery. See [edition packaging](editions.md) for build commands and
license details.

## Limitations

- Library-linked objects must be made local before creating a reaction.
- Geometry in a directly chosen Objects collection needs emissive materials already;
  lights need no materials. Use **Use Selected** to prepare ordinary materials.
- Blender 5.2 crashes when the Sample Sound node evaluates a missing file, so Noctune
  never hands a missing sound to its modifiers; relink or pack the sound instead.
