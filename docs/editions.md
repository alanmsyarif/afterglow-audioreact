# Afterglow editions

Afterglow Free includes object materials, automatic material preparation, music
analysis and reactions, object looks and custom object looks. Afterglow Pro
includes everything in Free plus environment generation: lights, visible beams,
lasers and the environment look controls.

Both editions use the Blender extension ID `afterglow`. Install one edition at a
time. To upgrade, disable and remove Free, then install Pro from its ZIP. Save
your Blender project before changing editions. The material and reaction data
stays in the project; Pro adds its environment tools to that workflow. Free has
no locked environment panel or disabled paid controls.

Existing generated Pro lights and graphics remain ordinary Blender scene data
and can still render when a project is opened in Free. Free cannot generate or
edit their environment controls. Saving through Free preserves those settings
for reopening in Pro. Free object-look files also work in Pro without changing
the environment; Free imports the object styling from older complete look files.

## Build packages

Use Python 3.11 or newer from the repository root:

```sh
python tools/build_editions.py --edition free --output-dir dist
python tools/build_editions.py --edition pro --output-dir build/private
```

The output names are `afterglow-free-VERSION.zip` and
`afterglow-pro-VERSION.zip`, using the version in
`afterglow/blender_manifest.toml`. Each archive is a complete Blender extension
with its manifest at the archive root. The script sets the edition metadata in
the archive and leaves the source tree unchanged. Pro is the development source
edition. Free packages omit the environment generator modules.

The builder refuses to put a Pro ZIP in the public `dist` directory. The
`build` directory is ignored by Git. Keep Pro ZIPs in your store's delivery
system or another private location when distributing the paid product.

Run packaging checks with:

```sh
python tools/test_build_editions.py
```

With Blender's `bpy` package installed, validate a built package using its
extension tool:

```sh
python -I PATH_TO_BLENDER_SCRIPTS/addons_core/bl_pkg/cli/blender_ext.py validate dist/afterglow-free-VERSION.zip
```

## Public releases and paid delivery

The GitHub tag release workflow builds and publishes **Free only**. It validates
the Free ZIP and passes that exact file to `gh release create`; it does not use
a wildcard that could upload a paid package. Build Pro locally for delivery
through your chosen store. No checkout, payment account, license-key service or
DRM is added to the add-on.

Both editions retain GPL-3.0-or-later. Selling Pro is permitted under that
license; recipients retain the GPL rights, including redistribution. Existing
public source and historical full-feature ZIPs remain public. The edition split
organizes the products and their distribution; it does not make that published
code private.
