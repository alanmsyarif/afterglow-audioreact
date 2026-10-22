# Afterglow 0.27

**Turn music into lighting.**

Audio-reactive lighting for Blender 5.2+. Choose music and a look, then build.

| | Afterglow Free | Afterglow Pro |
|---|:---:|:---:|
| Automatic emissive materials and music reactions | ✓ | ✓ |
| Object looks, custom looks, ripple motion and glow | ✓ | ✓ |
| Environment lights, beams, lasers and signal effects | | ✓ |
| Environment looks and layer controls | | ✓ |

Pro includes everything in Free. Both editions run locally without an account or
license-key service.

## Animate your objects

Open the **Afterglow** tab in the 3D Viewport sidebar (`N`).

1. Select meshes, curves or text and press **Use Selected**. Missing emission is
   added automatically while original materials are preserved.
2. Load your music and choose an object **Look**.
3. Press **Build Reaction**. Use **Material Preview** or **Rendered** shading and
   press **Space** to preview.

Use **Update Reaction** after changing the music or object look. Afterglow handles
calibration and smooth playback. Existing emissive objects can use their collection
directly. This build leaves generated environment effects alone.

## Generate environment effects — Pro

In Pro, open the **Afterglow FX** tab and its **Afterglow Pro · Environment** panel.

1. Choose the collection that defines the environment bounds. Ordinary non-emissive
   geometry works; leaving Bounds empty uses the main Objects collection.
2. Choose an environment **Look**, such as **Laser Show**.
3. Press **Build Environment**, or **Update Environment** to refresh it.

This creates the effect lights and graphics without preparing materials or building
object reactions. Both tabs share music and Start Frame; their looks and build
controls are independent.

Advanced environment controls contain the individual layers, venue and live mix.
**Data Strikes** and **Gun Bursts** each keep their beam visibility, glow and style
inside their own effect settings. Laser rays work without fog.

## Render with Blender

Arrange your scene and camera in Blender. Set the resolution, frame range, format
and destination in **Output Properties**, then use **Render → Render Animation**.
Afterglow keeps Blender's render settings and timeline range under your control.

## Install

1. Download [Afterglow Free 0.27.0](dist/afterglow-free-0.27.0.zip), or use your Pro ZIP.
2. In Blender 5.2+, open **Edit → Preferences → Get Extensions → Install from Disk**
   and choose the ZIP.
3. Open the 3D Viewport sidebar (`N`) and select **Afterglow**. Pro also includes
   the **Afterglow FX** tab.

Install one edition at a time. To upgrade, save your project, disable and remove
Free, then install Pro. Your object materials and reaction settings remain in the
project. [Edition packages and paid distribution](docs/editions.md).

[Advanced guide and development commands](docs/advanced.md) · [License](LICENSE)
