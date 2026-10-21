# Afterglow 0.24

**Turn music into lighting.**

Audio-reactive lighting for Blender 5.2+. Use your own emissive objects and music,
choose a look, and let Afterglow handle calibration, smooth playback, lighting and glow.

## Start in three steps

1. **Choose your objects:** select their collection, or select emissive objects and
   press **Use Selected**.
2. **Load your music and choose a look.**
3. Press **Create Visual**. Use **Material Preview** or **Rendered** viewport shading,
   then press **Space** to preview.

Change the music or look and press **Update Visual**. It refreshes the whole show.
There are no separate calibration, baking, lighting-build or glow setup steps.

Try **Laser Show** for sweeping green laser fans and violet washes. Rays appear
on the beat without fog or extra setup; choose another look to replace the effect.

The lighting fits your chosen collection. Arrange your scene and camera in Blender,
then press **Update Visual** after changing the collection or look.

## Render with Blender

Set your resolution, frame range, file format and destination in Blender's
**Output Properties**, then use **Render → Render Animation**. Afterglow builds
the music reactions; Blender handles rendering and video export.

## Install

1. Download [afterglow-0.24.0.zip](dist/afterglow-0.24.0.zip).
2. In Blender 5.2+, open **Edit → Preferences → Get Extensions → Install from Disk**
   and choose the ZIP.
3. Open the 3D Viewport sidebar (`N`) and select **Afterglow**.

The add-on runs locally. No account or network service is required.

## Need more control?

Turn on **Advanced Controls**. Individual effects, custom look libraries, object
settings and manual build tools are kept there.

[Advanced guide and development commands](docs/advanced.md) · [License](LICENSE)
