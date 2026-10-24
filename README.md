# Noctune Free 0.32.0

**Choose music. Make it glow.**

Make your own meshes, curves, text and lights react to music in Blender. Select
your objects, load a track and build. Styling is optional afterward. Noctune prepares geometry materials,
calibrates the reaction and prepares playback for you.

[**Download Noctune Free 0.32.0**](dist/noctune-free-0.32.0.zip?raw=true)

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

The panel has three views: **Setup**, **Style** and **Presets**. **Music → Track**
and **Start Frame** remain at the top.

1. Load a track using the folder icon beside **Track**.
2. In **Setup**, select your geometry or lights and press **Use Selected**.
   Missing emission is prepared while original materials are kept for restoration.
3. Press **Build Reaction**. It calibrates and prepares playback, then opens **Style**.
   Use **Material Preview** or **Rendered** shading and press **Space** to preview.

Select a reacting object to see its settings in **Style**. For an optional built-in
look, open **Presets**, choose a thumbnail and press **Apply Look**. Browsing does
not apply a preset. **Setup → Update Reaction** refreshes the reaction while keeping
your current materials and appearance.

Point, Spot, Area and Sun lights work beside geometry. Use **Rendered** shading to
see native lights illuminate surfaces. Calibration reads the full source track;
Start Frame, Delay and Ripple placement do not change that measurement.

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

**Style** shows the selected object's appearance directly. Its **Audio Response**,
**Ripple Motion** and **Glow** sections keep optional controls close to the result.
**Presets** contains both the thumbnail browser and **My Object Looks**.
**Setup → Tools** holds playback refresh and calibration; **Setup → Cleanup**
removes the reaction. You can change views without changing the scene.
See the [advanced guide](docs/advanced.md) for the full controls.

## Preview, render and cleanup

Use **Material Preview** or **Rendered** shading while playing the timeline.
Soft glow appears in the composited final image and needs Blender's
**Compositing** option enabled.

Set your camera, resolution, frame range, format and destination in Blender,
then use **Render → Render Animation**. Noctune preserves your output settings. The timeline end follows the music when its track, Start Frame or frame rate changes; later manual range edits are kept until that configuration changes again.

To remove the reaction, open **Setup → Cleanup → Remove Object Reaction**. This restores automatically prepared material
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

Built-in object looks preserve existing emissive material colors and use zero idle glow. Newly prepared materials use the preset palette. To add a constant glow, adjust **Style → Idle Glow**; calibration changes audio sensitivity, not this artistic control. Zero emission can still leave a surface visible under scene lighting.

Build and Update leave compositor glow untouched. Add a halo explicitly under **Style → Glow → Add Glow**.

## Superhive product-page files

[Download the product-page HTML kit](dist/noctune-free-superhive-listing-kit.zip?raw=true).
Extract it and open **index.html** for standalone previews, editor-ready description
HTML and the image-upload helper. Upload the supplied renders to Superhive, paste
their hosted URLs into the helper, then copy the generated description into the
product editor. Set price and seller details in your dashboard and review its
preview before publishing. The kit does not publish anything.

Rebuild with `python tools/build_storefront.py --edition free`.

[**Download the single-file Superhive description**](dist/noctune-free-superhive.html?raw=true).
Open it, choose an edition, then use **Copy HTML** or **Copy formatted text**.
All preview images are embedded; no ZIP or media folder is needed. If the
marketplace strips embedded images, use the built-in Image links helper to save
them for upload and substitute their hosted URLs.
