# Noctune Free 0.28

**Choose music. Make it glow.**

Audio-reactive object materials for Blender 5.2+. Choose music and a look, then
build. This public repository contains Noctune Free.

| | Noctune Free | Noctune Pro |
|---|:---:|:---:|
| Automatic emissive materials and music reactions | ✓ | ✓ |
| Object looks, custom looks, ripple motion and glow | ✓ | ✓ |
| Environment lights, beams, lasers and signal effects | | ✓ |
| Environment looks and layer controls | | ✓ |

Pro includes everything in Free and is maintained in a separate private
repository for paid delivery. Both editions run locally without an account or
license-key service. [Edition details](docs/editions.md).

## Animate your objects

Open the **Noctune** tab in the 3D Viewport sidebar (`N`).

1. Select meshes, curves or text and press **Use Selected**. Missing emission is
   added automatically while original materials are preserved.
2. Load your music and choose an object **Look**.
3. Press **Build Reaction**. Use **Material Preview** or **Rendered** shading and
   press **Space** to preview.

Use **Update Reaction** after changing the music or object look. Noctune handles
calibration and smooth playback. Existing emissive objects can use their
collection directly. Existing generated environment data stays in the scene.

## Render with Blender

Arrange your scene and camera in Blender. Set the resolution, frame range, format
and destination in **Output Properties**, then use **Render → Render Animation**.
Noctune keeps Blender's render settings and timeline range under your control.

## Install Free

1. Download [Noctune Free 0.28.0](dist/noctune-free-0.28.0.zip).
2. In Blender 5.2+, open **Edit → Preferences → Get Extensions → Install from Disk**
   and choose the ZIP.
3. Open the 3D Viewport sidebar (`N`) and select **Noctune**.

Install one edition at a time. To upgrade to Pro, save your project, disable and
remove Free, then install your Pro ZIP. Your object materials and reaction
settings remain in the project.

[Advanced guide and development commands](docs/advanced.md) · [License](LICENSE)

Renamed from Afterglow. Existing projects and look files remain compatible.
