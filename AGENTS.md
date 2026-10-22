# Noctune Free repository

This repository contains Noctune Free: object materials and music reactions.
The paid environment engine is maintained separately in
https://github.com/alanmsyarif/noctune-pro.

- Keep environment generation modules and Pro ZIPs out of this repository.
- The source defaults to Free and public releases upload the Free ZIP only.
- Keep the legacy `afterglow` extension ID, Python package, RNA paths, ownership
  tags and look formats compatible with existing Blender projects.
- Preserve the GPL-3.0-or-later license.
- Build with `python tools/build_editions.py --edition free --output-dir dist`.
- Run `python tools/test_build_editions.py` and each `tests/test_*.py` suite using
  Python 3.13 with `bpy==5.2.2`. Rendering tests need software OpenGL on Linux.

Historical commits and old full-feature ZIPs predate the repository split.
Do not rewrite or delete published history as part of routine changes.
