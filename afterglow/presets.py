"""Curated starting looks that leave a user's scene and routing intact.

Applying a look edits controls only. Build Effects and, when Decay changes,
Re-bake are still explicit operations. No geometry or materials are created here.
"""

from . import core, edition, effect_schema, nodes, prepare


DESCRIPTIONS = {
    "NEON_CLUB": "Electric cyan and pink, punchy strikes and fast club accents",
    "CINEMATIC_PULSE": "Warm gold and cool blue, restrained blooms and deliberate impacts",
    "AURORA_FLOW": "Mint and violet, soft blooms and flowing trails",
    "SIGNAL_GRID": "Icy cyan and amber, connected signals and crisp digital strikes",
    "LASER_SHOW": "Neon green laser fans sweep on the beat over violet washes",
}
ITEMS = tuple((identifier, name, DESCRIPTIONS[identifier]) for identifier, name in (
    ("NEON_CLUB", "Neon Club"),
    ("CINEMATIC_PULSE", "Cinematic Pulse"),
    ("AURORA_FLOW", "Aurora Flow"),
    ("SIGNAL_GRID", "Signal Grid"),
    ("LASER_SHOW", "Laser Show"),
))

# Object looks share stable IDs with their environment companions, while their
# labels describe the object result even when no environment engine is installed.
OBJECT_ITEMS = (
    ("NEON_CLUB", "Neon Club", "Electric cyan and pink with a punchy response"),
    ("CINEMATIC_PULSE", "Cinematic Pulse", "Cool blue and warm gold with a slower fade"),
    ("AURORA_FLOW", "Aurora Flow", "Mint and violet with a soft, flowing response"),
    ("SIGNAL_GRID", "Signal Grid", "Icy cyan and amber with a crisp response"),
    ("LASER_SHOW", "Electric Violet", "Violet and neon green with a quick pulse"),
)

# Colors are linear RGB, as expected by Blender's Color properties.
_LOOKS = {
    "NEON_CLUB": {
        "colors": ((0.01, 0.65, 1.0), (1.0, 0.025, 0.35)),
        "layers": {"strikes": (0.85, "LOW", 0.65, 1.2),
                   "blooms": (0.35, "MID", 0.8, 0.65),
                   "gun": (0.5, "HIGH", 0.6, 0.9)},
        "fx": {"beams": True, "strike_style": "TWIN", "gun_style": "TRACER",
               "strike_companions": 1, "gun_length": 1.25},
        "object": (0.0, 1.2, 0.12),
    },
    "CINEMATIC_PULSE": {
        "colors": ((0.08, 0.22, 0.6), (1.0, 0.48, 0.09)),
        "layers": {"strikes": (0.35, "LOW", 1.5, 0.75),
                   "blooms": (0.65, "MID", 1.8, 0.8)},
        "fx": {"beams": True, "strike_style": "FILAMENT", "gun_style": "DART",
               "strike_companions": 0, "gun_length": 1.0},
        "object": (0.0, 0.9, 0.35),
    },
    "AURORA_FLOW": {
        "colors": ((0.035, 0.8, 0.45), (0.45, 0.1, 1.0)),
        "layers": {"blooms": (0.7, "MID", 2.2, 0.7),
                   "trails": (0.65, "LOW", 1.8, 0.85),
                   "downward": (0.3, "HIGH", 1.5, 0.5)},
        "fx": {"beams": False, "strike_style": "RIBBON", "gun_style": "STREAK",
               "strike_companions": 0, "gun_length": 1.5},
        "object": (0.0, 0.85, 0.5),
    },
    "SIGNAL_GRID": {
        "colors": ((0.02, 0.7, 1.0), (1.0, 0.3, 0.025)),
        "layers": {"signals": (0.8, "MID", 0.8, 1.0),
                   "strikes": (0.45, "LOW", 0.6, 0.8)},
        "fx": {"beams": True, "strike_style": "FILAMENT", "gun_style": "STREAK",
               "strike_companions": 0, "gun_length": 0.8},
        "object": (0.0, 1.1, 0.09),
    },
    "LASER_SHOW": {
        "colors": ((0.42, 0.025, 1.0), (0.08, 1.0, 0.2)),
        "layer_colors": {"blooms": (0.42, 0.025, 1.0)},
        "layers": {"lasers": (1.0, "LOW", 1.5, 1.0),
                   "blooms": (0.4, "MID", 1.8, 0.45)},
        "fx": {"beams": False, "strike_style": "FILAMENT", "gun_style": "TRACER",
               "strike_companions": 0, "gun_length": 1.0},
        "object": (0.0, 0.85, 0.18),
    },
}


def _action_paths(action, slot):
    if not action:
        return set()
    paths = set()
    for layer in action.layers:
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", ()):
                if slot is None or bag.slot_handle == slot.handle:
                    paths.update(fc.data_path for fc in bag.fcurves)
    return paths


def _animation_paths(idblock):
    """Include active actions, NLA actions and drivers, even when currently muted."""
    ad = idblock.animation_data
    if not ad:
        return set()
    paths = {fc.data_path for fc in ad.drivers}
    paths.update(_action_paths(ad.action, ad.action_slot))

    def visit(strips):
        for strip in strips:
            paths.update(_action_paths(getattr(strip, "action", None),
                                       getattr(strip, "action_slot", None)))
            visit(getattr(strip, "strips", ()))

    for track in ad.nla_tracks:
        visit(track.strips)
    return paths


def _guard(idblock, paths):
    conflicts = sorted(_animation_paths(idblock).intersection(paths))
    if conflicts:
        raise ValueError(f"Look would replace animated controls on {idblock.name}: "
                         f"{conflicts[0]}. Remove their keyframes, NLA animation or drivers "
                         "first, or apply the look in a separate scene")


SCOPES = ("ALL", "OBJECT", "EFFECTS")


def _scope(scope):
    if not isinstance(scope, str) or scope not in SCOPES:
        raise ValueError("Choose ALL, OBJECT or EFFECTS style scope")
    return edition.style_scope(scope)


def _plan(scene, identifier, scope="ALL"):
    """Guard only the controls this style scope will change, before any edits."""
    scope = _scope(scope)
    if not isinstance(identifier, str) or identifier not in _LOOKS:
        raise ValueError(f"Unknown Noctune look: {identifier}")
    look = _LOOKS[identifier]
    color_a, color_b = look["colors"]
    fx, layer_values, fx_values = None, {}, {}
    if scope in ("ALL", "EFFECTS"):
        fx = scene.afterglow.effects
        for index, name in enumerate(effect_schema.LAYERS):
            enabled = name in look["layers"]
            weight, band, timing, intensity = look["layers"].get(name, (0.5, "LOW", 1.0, 1.0))
            layer_values[name] = {"enabled": enabled, "weight": weight, "trigger_band": band,
                                  "timing": timing, "intensity": intensity,
                                  "color": look.get("layer_colors", {}).get(
                                      name, color_a if index % 2 == 0 else color_b)}
        for name in ("strikes", "gun"):
            layer_values[name].update({"visible_beam": look["fx"]["beams"], "beam_glow": 1.0})
        fx_values = {"beam_glow": 1.0, "signal_graphics": identifier == "SIGNAL_GRID",
                     "signal_glow": 1.0, "laser_count": 7, "laser_spread": 1.0,
                     "laser_sweep": 0.7, "laser_glow": 1.0, **look["fx"]}
        scene_paths = {f"afterglow.effects.{name}.{prop}"
                       for name, values in layer_values.items() for prop in values}
        scene_paths.update(f"afterglow.effects.{name}" for name in fx_values)
        _guard(scene, scene_paths)

    object_values, objects = {}, []
    if scope in ("ALL", "OBJECT"):
        glow, intensity, decay = look["object"]
        object_values = {"Use Colors": True, "Color A": (*color_a, 1.0),
                         "Color B": (*color_b, 1.0), "Glow": glow,
                         "Intensity": intensity, "Decay": decay}
        collection = scene.afterglow.collection
        for ob in core.targets(collection) if collection else ():
            mod = core.reaction(ob)
            if mod:
                if ob.library and not ob.override_library:
                    raise ValueError(f"Make {ob.name} local before applying a look")
                _guard(ob, {nodes._socket(mod, name).path_from_id("value")
                            for name in object_values})
                objects.append((ob, mod))

    return fx, layer_values, fx_values, object_values, objects


def preflight(scene, identifier, *, scope="ALL"):
    """Reject unavailable looks or animation conflicts within the requested scope."""
    _plan(scene, identifier, scope)


def enabled_layers(identifier):
    """The layers a built-in look will enable, without consulting or mutating a scene."""
    if not isinstance(identifier, str) or identifier not in _LOOKS:
        raise ValueError(f"Unknown Noctune look: {identifier}")
    return tuple(_LOOKS[identifier]["layers"])


def apply(scene, identifier, *, scope="ALL"):
    """Apply styling only in scope; return object count for OBJECT, else layer count."""
    scope = _scope(scope)
    fx, layer_values, fx_values, object_values, objects = _plan(scene, identifier, scope)
    for name, values in layer_values.items():
        layer = getattr(fx, name)
        for prop, value in values.items():
            setattr(layer, prop, value)
    for name, value in fx_values.items():
        setattr(fx, name, value)
    for ob, mod in objects:
        for name, value in object_values.items():
            if name == "Use Colors" and ob.type != "LIGHT" and prepare.has_authored_emission(ob):
                value = False
            nodes.set_input(mod, name, value)
    return len(objects) if scope == "OBJECT" else sum(values["enabled"] for values in layer_values.values())
