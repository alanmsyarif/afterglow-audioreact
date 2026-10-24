# Noctune Free 0.31.0

**Choose music. Make it glow.**

Make your own meshes, curves, text and lights react to music in Blender. Select
your objects, load a track and choose a look. Noctune prepares geometry materials,
calibrates the reaction and prepares playback for you.

[**Download Noctune Free 0.31.0**](dist/noctune-free-0.31.0.zip?raw=true)

**Blender 5.2+ · EEVEE and Cycles · Runs locally**

[**Download the illustrated offline guide**](dist/noctune-free-documentation.zip?raw=true).
Extract the ZIP and open **index.html** in a browser. It includes quick steps,
images, optional GIFs and search. [Single HTML file](docs/html/noctune-free.html?raw=true).

## Install

1. Download the ZIP above and keep it zipped.
2. In Blender, open **Edit → Preferences → Get Extensions → Install from Disk**
   and choose the ZIP.
3. In the 3D Viewport, press `N` and open the **Noctune** tab.

## Make your first reaction

Start with your own scene and objects.

1. **Music → Track.** Use the folder icon to load a track.
2. **Objects → Use Selected.** Select your geometry and lights, then use the
   full-width button. Missing emission is prepared while original materials are
   preserved for restoration.
3. **Reaction → Build Reaction.** Press **Space** to preview.
   Use **Material Preview** or **Rendered** shading to see materials react.

After changing music, press **Update Reaction**. For a preset, use **Appearance → Object Look → Apply Look** after building. Point, Spot, Area
and Sun lights work in the same selection as geometry: their power and color
react to the music. Use **Rendered** shading to preview native lights.
Calibration reads the full source track, so playback Delay and Ripple placement
keep the same normalized response.

## Included in Free

- **Automatic material preparation:** use ordinary meshes, curves and text, or
  choose a collection of objects that already have emissive materials.
- **Your own lights:** sync the power and color of Point, Spot, Area and Sun lights
  through the same Build Reaction workflow.
- **Five object looks:** Neon Club, Cinematic Pulse, Aurora Flow, Signal Grid and
  Electric Violet.
- **Custom looks:** save your style in the project and import or export look files
  to reuse it elsewhere.
- **Ripple motion and level meters:** send light across objects and surfaces, or
  fill objects like equalizer bars.
- **Soft glow:** add optional compositor glow to the final image.
- **Playback preparation:** Noctune calibrates and bakes the reaction when you
  build it.

The panel follows **Music → Objects → Reaction → Appearance**. The full-width **Use Selected**
button prepares your selection. Open **Advanced Controls** for the selected object's
appearance, audio response, ripple, glow, saved looks and playback tools. **Start
Frame** appears in Music while those controls are open.

Build and Update calibrate the audio response while keeping your current appearance. After building, choose an optional preset under **Appearance → Apply Look**. Browsing the picker does not apply it.
See the [advanced guide](docs/advanced.md) for those controls.

## Preview, render and cleanup

Use **Material Preview** or **Rendered** shading while playing the timeline.
Soft glow appears in the composited final image and needs Blender's
**Compositing** option enabled.

Set your camera, resolution, frame range, format and destination in Blender,
then use **Render → Render Animation**. Noctune preserves your output settings. The timeline end follows the music when its track, Start Frame or frame rate changes; later manual range edits are kept until that configuration changes again.

To remove the reaction, expand **Cleanup → Remove Object Reaction** below the workflow. This restores automatically prepared material
assignments and original light data, including preexisting light animation.
Materials or light data you reassigned manually stay in place.

## Noctune Free and Pro

| Feature | Free | Pro |
|---|:---:|:---:|
| Object materials, native lights and music reactions | ✓ | ✓ |
| Object looks, custom looks, ripple motion, level meters and glow | ✓ | ✓ |
| Generated environment lights, beams, lasers and signal effects | | ✓ |
| Environment looks and layer controls | | ✓ |

Pro includes everything in Free and is distributed separately. Install one
edition at a time. To upgrade, save your project, disable and remove Free, then
install your Pro ZIP. Your object materials and reaction settings remain in the
project. [More about editions and compatibility](docs/editions.md).

## Documentation and license

[Advanced controls and development](docs/advanced.md) ·
[Build and edition details](docs/editions.md) · [GPL-3.0-or-later license](LICENSE)

Noctune was previously named Afterglow. Existing projects and look files remain
compatible.

Built-in object looks preserve existing emissive material colors and use zero idle glow. Newly prepared materials use the preset palette. To add a constant glow, adjust **Advanced Controls → Selected Object → Idle Glow**; calibration changes audio sensitivity, not this artistic control. Zero emission can still leave a surface visible under scene lighting.

Build and Update leave compositor glow untouched. Add a halo explicitly under **Advanced Controls → Glow → Add Glow**.
