# Offline Free user guide

Build from the repository root with Python 3.11 or newer:

```bash
python tools/build_docs.py --edition free
```

Open `docs/html/noctune-free.html` directly in a browser, or extract
`dist/noctune-free-documentation.zip` and open `index.html`.
The HTML embeds its styles, scripts, images and animations. No web server,
network connection, Blender installation or extra Python package is needed.
Animations start paused and have Play/Pause controls. The documentation ZIP
is separate from the Blender extension ZIP.

Edit `free.json` to update the short workflows and optional advanced controls.
`media.json` supplies image paths, descriptions and captions. `style.css` and
`guide.js` handle responsive layout, local search, image enlargement and printing.
Reading and still images work without JavaScript.

The example scenes are original documentation renders using actual Noctune Free
materials, presets and native-light reactions with controlled prepared response
levels. They are not built-in stages. GIF posters share their animation's basename.
Update the documented version and download URL when the add-on changes.

This public source contains only the Free guide and Free media. Pro documentation
and environment examples are maintained in the separate private repository.
