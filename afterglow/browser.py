"""Transient sidebar navigation and a cached browser for original Noctune previews.

Choosing a preview changes only the existing preset selection. Applying a look or
building a reaction stays an explicit operator action in the sidebar.
"""
from pathlib import Path

import bpy
from bpy.props import EnumProperty

from . import edition, presets


OBJECT_PAGES = (
    ("SETUP", "Setup", "Choose objects and build or update their reaction", "MODIFIER", 0),
    ("STYLE", "Style", "Tune the appearance of the current reaction", "SHADING_RENDERED", 1),
    ("PRESETS", "Presets", "Browse optional looks and your saved styles", "PRESET", 2),
)
ENVIRONMENT_PAGES = (
    ("SETUP", "Setup", "Choose bounds and build or update the environment", "MODIFIER", 0),
    ("STYLE", "Effects", "Choose and tune individual environment effects", "LIGHT", 1),
    ("PRESETS", "Presets", "Browse optional environment mixes and saved styles", "PRESET", 2),
)
EFFECT_ITEMS = (
    ("strikes", "Data Strikes", "Vertical accents and visible shafts", "LIGHT_SPOT", 0),
    ("blooms", "Data Blooms", "Soft washes and swells", "LIGHT_AREA", 1),
    ("flashes", "Rapid Flashes", "Brief whole-venue accents", "LIGHT_SUN", 2),
    ("trails", "Light Trails", "Moving light around the venue", "CURVE_PATH", 3),
    ("downward", "Cascade Trails", "A line of travelling lights", "SORT_ASC", 4),
    ("gun", "Gun Bursts", "Directed bursts and tracers", "LIGHT_SPOT", 5),
    ("signals", "Signal Network", "Connected travelling pulses", "OUTLINER_DATA_CURVES", 6),
    ("lasers", "Lasers", "Visible rays and scanning patterns", "LIGHT_SPOT", 7),
)
WORKFLOWS = (("OBJECT", "Objects", "Materials and native light reactions", "OBJECT_DATA", 0),)
if edition.IS_PRO:
    WORKFLOWS += (("ENVIRONMENT", "Environment", "Generated lights, beams and patterns", "LIGHT", 1),)


class AfterglowUIState(bpy.types.PropertyGroup):
    workflow: EnumProperty(name="Workflow", items=WORKFLOWS, default="OBJECT", options={"SKIP_SAVE"})
    object_page: EnumProperty(name="Object Workspace", items=OBJECT_PAGES, default="SETUP", options={"SKIP_SAVE"})
    if edition.IS_PRO:
        environment_page: EnumProperty(name="Environment Workspace", items=ENVIRONMENT_PAGES,
                                       default="SETUP", options={"SKIP_SAVE"})
        active_effect: EnumProperty(name="Effect", items=EFFECT_ITEMS, default="strikes", options={"SKIP_SAVE"})


_previews = None
_ICON_ROOT = Path(__file__).resolve().parent / "icons"
# Keep all tuple/string references alive for Blender's dynamic enum callbacks.
_object_items = tuple((ident, label, description, 0, index)
                      for index, (ident, label, description) in enumerate(presets.OBJECT_ITEMS))
_environment_items = tuple((ident, label, description, 0, index)
                           for index, (ident, label, description) in enumerate(presets.ITEMS))


def object_items(_self, _context):
    return _object_items


def environment_items(_self, _context):
    return _environment_items


def _items(source, category):
    return tuple((ident, label, description,
                  _previews[category + "/" + ident].icon_id
                  if _previews is not None and category + "/" + ident in _previews else 0,
                  index) for index, (ident, label, description) in enumerate(source))


def register():
    """Load thumbnail files once; background Blender keeps a text-only browser."""
    global _previews, _object_items, _environment_items
    unregister()
    if not bpy.app.background:
        from bpy.utils import previews as preview_api
        _previews = preview_api.new()
        categories = ("objects", "environment") if edition.IS_PRO else ("objects",)
        for category in categories:
            source = presets.OBJECT_ITEMS if category == "objects" else presets.ITEMS
            for identifier, _name, _description in source:
                path = _ICON_ROOT / category / (identifier.lower() + ".png")
                if path.is_file():
                    try:
                        _previews.load(category + "/" + identifier, str(path), "IMAGE")
                    except (OSError, RuntimeError):
                        pass  # The ordinary picker stays usable if a preview is unavailable.
    _object_items = _items(presets.OBJECT_ITEMS, "objects")
    _environment_items = _items(presets.ITEMS, "environment")


def unregister():
    global _previews, _object_items, _environment_items
    if _previews is not None:
        from bpy.utils import previews as preview_api
        preview_api.remove(_previews)
        _previews = None
    _object_items = _items(presets.OBJECT_ITEMS, "objects")
    _environment_items = _items(presets.ITEMS, "environment")


def draw(layout, settings, property_name):
    """Draw a native thumbnail chooser, with a safe text fallback and no I/O."""
    if property_name not in {"creative_look", "environment_look"}:
        raise ValueError("Choose an object or environment look")
    if property_name == "environment_look" and not edition.IS_PRO:
        raise ValueError("Environment previews require Noctune Pro")
    items = _object_items if property_name == "creative_look" else _environment_items
    if any(item[3] for item in items) and hasattr(layout, "template_icon_view"):
        layout.template_icon_view(settings, property_name, show_labels=True, scale=6.0, scale_popup=4.0)
    else:
        layout.prop(settings, property_name, text="Object Look" if property_name == "creative_look" else "Preset")
