# Noctune editions

Noctune Free includes object materials, automatic material preparation, music
analysis and reactions, object looks and custom object looks. Noctune Pro
includes everything in Free plus environment generation: lights, visible beams,
lasers and environment look controls.

This public repository contains the Free source. Pro is maintained separately in
[the private noctune-pro repository](https://github.com/alanmsyarif/noctune-pro).
Access to that source repository requires permission; buyers install the Pro ZIP
provided through paid delivery.

Noctune is the new product name for Afterglow. Both editions retain the Blender
extension ID `afterglow`, the source folder of the same name, and existing
project property and look-format identifiers for compatibility. Existing
Afterglow projects and look files can be opened without manual migration.

Install one edition at a time. To upgrade, save your project, disable and remove
Free, then install Pro from its ZIP. Material and reaction data stays in the
project. Pro adds its environment tools to that workflow. Free has no locked
environment panel or disabled paid controls.

Existing generated Pro lights and graphics remain ordinary Blender scene data
and can still render when a project is opened in Free. Free cannot generate or
edit their environment controls. Saving through Free preserves those settings
for reopening in Pro. Free object-look files also work in Pro without changing
the environment; Free imports the object styling from older complete look files.

## Build Free

Use Python 3.11 or newer from this repository's root:

```sh
python tools/build_editions.py --edition free --output-dir dist
```

The output name is `noctune-free-VERSION.zip`, using the version in
`afterglow/blender_manifest.toml`. The archive is a complete Blender extension
with its manifest at the archive root. The script sets edition metadata in the
archive and leaves the source tree unchanged. The public source omits the
environment generator modules; it cannot build a working Pro edition.

Run packaging checks with:

```sh
python tools/test_build_editions.py
```

With Blender's `bpy` package installed, validate the package using its extension
tool:

```sh
python -I PATH_TO_BLENDER_SCRIPTS/addons_core/bl_pkg/cli/blender_ext.py validate dist/noctune-free-VERSION.zip
```

## Build Pro in the private repository

Maintainers run this command from a complete private `noctune-pro` checkout:

```sh
python tools/build_editions.py --edition pro --output-dir dist
```

That checkout contains the environment generator and produces
`dist/noctune-pro-VERSION.zip` in the private repository. The public Free builder
rejects Pro builds and keeps Pro ZIPs out of its public `dist` directory. Deliver
the paid ZIP from the private repository through your chosen store or another
private channel.

## Releases and licensing

The public GitHub tag release workflow builds and publishes **Free only**. It
validates the Free ZIP and passes that exact file to `gh release create`; it
does not use a wildcard that could upload a paid package. Pro development and
delivery stay in the separate private repository. No checkout, payment account,
license-key service or DRM is added to the add-on.

Both editions retain GPL-3.0-or-later. Selling Pro is permitted under that
license; recipients retain GPL rights, including redistribution. Previously
published source and historical full-feature ZIPs remain public. Moving current
Pro development to a private repository does not make that earlier code private.
