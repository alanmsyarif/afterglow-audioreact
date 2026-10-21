# Afterglow 0.23

**Turn music into lighting.**

Audio-reactive lighting for Blender 5.2+. Pick a scene and a look, then let Afterglow
handle calibration, smooth playback, lighting, glow and camera setup.

![Neon Tunnel with audio-reactive lighting](docs/images/creator-tunnel-stage.jpg)

## Start in three steps

1. **Choose a scene:** Orbit Stage, Equalizer Wall or Neon Tunnel.
2. **Choose music and a look.** Leave music empty to try the included demo beat.
3. Press **Create Visual**, then **Space** to preview.

Change the music or look and press **Update Visual**. It refreshes the whole show.
There are no separate calibration, baking, lighting-build or glow setup steps.

Try **Laser Show** for sweeping green laser fans and violet washes. Rays appear
on the beat without fog or extra setup; choose another look to replace the effect.
[See the rendered Laser Show preview](docs/images/creator-laser-stage.jpg).

Afterglow reuses the selected collection. Advanced Controls lets you use your own
emissive objects or explicitly add another stage. New stages select their generated
camera; existing custom-scene cameras remain under your control.

## Render with Blender

Set your resolution, frame range, file format and destination in Blender's
**Output Properties**, then use **Render → Render Animation**. Afterglow creates
the reactive scene; Blender handles rendering and video export.

## Install

1. Download [afterglow-0.23.0.zip](dist/afterglow-0.23.0.zip).
2. In Blender 5.2+, open **Edit → Preferences → Get Extensions → Install from Disk**
   and choose the ZIP.
3. Open the 3D Viewport sidebar (`N`) and select **Afterglow**.

The add-on runs locally. No account or network service is required. The demo beat
is original synthesized audio packed into the project.

## Need more control?

Turn on **Advanced Controls**. Individual effects, custom look libraries, object
settings and manual build tools are kept there.

[Advanced guide and development commands](docs/advanced.md) · [License](LICENSE)
