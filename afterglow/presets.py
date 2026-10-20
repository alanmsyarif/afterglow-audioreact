"""Curated starting looks that leave a user's scene and routing intact.

Applying a look edits controls only. Build Effects and, when Decay changes,
Re-bake are still explicit operations. No geometry or materials are created here.
"""

from . import core, effects, nodes


DESCRIPTIONS = {
    "NEON_CLUB": "Electric cyan and pink, punchy strikes and fast club accents",
    "CINEMATIC_PULSE": "Warm gold and cool blue, restrained blooms and deliberate impacts",
    "AURORA_FLOW": "Mint and violet, soft blooms and flowing trails",
    "SIGNAL_GRID": "Icy cyan and amber, connected signals and crisp digital strikes",
}
ITEMS = tuple((identifier, name, DESCRIPTIONS[identifier]) for identifier, name in (
    ("NEON_CLUB", "Neon Club"),
    ("CINEMATIC_PULSE", "Cinematic Pulse"),
    ("AURORA_FLOW", "Aurora Flow"),
    ("SIGNAL_GRID", "Signal Grid"),
))

# Colors are linear RGB, as expected by Blender's Color properties.
_LOOKS = {
    "NEON_CLUB": {
        "colors": ((0.01, 0.65, 1.0), (1.0, 0.025, 0.35)),
        "layers": {"strikes": (0.85, "LOW", 0.65, 1.2),
                   "blooms": (0.35, "MID", 0.8, 0.65),
                   "gun": (0.5, "HIGH", 0.6, 0.9)},
        "fx": {"beams": True, "strike_style": "TWIN", "gun_style": "TRACER",
               "strike_companions": 1, "gun_length": 1.25},
        "object": (0.025, 1.2, 0.12),
    },
    "CINEMATIC_PULSE": {
        "colors": ((0.08, 0.22, 0.6), (1.0, 0.48, 0.09)),
        "layers": {"strikes": (0.35, "LOW", 1.5, 0.75),
                   "blooms": (0.65, "MID", 1.8, 0.8)},
        "fx": {"beams": True, "strike_style": "FILAMENT", "gun_style": "DART",
               "strike_companions": 0, "gun_length": 1.0},
        "object": (0.01, 0.9, 0.35),
    },
    "AURORA_FLOW": {
        "colors": ((0.035, 0.8, 0.45), (0.45, 0.1, 1.0)),
        "layers": {"blooms": (0.7, "MID", 2.2, 0.7),
                   "trails": (0.65, "LOW", 1.8, 0.85),
                   "downward": (0.3, "HIGH", 1.5, 0.5)},
        "fx": {"beams": False, "strike_style": "RIBBON", "gun_style": "STREAK",
               "strike_companions": 0, "gun_length": 1.5},
        "object": (0.08, 0.85, 0.5),
    },
    "SIGNAL_GRID": {
        "colors": ((0.02, 0.7, 1.0), (1.0, 0.3, 0.025)),
        "layers": {"signals": (0.8, "MID", 0.8, 1.0),
                   "strikes": (0.45, "LOW", 0.6, 0.8)},
        "fx": {"beams": True, "strike_style": "FILAMENT", "gun_style": "STREAK",
               "strike_companions": 0, "gun_length": 0.8},
        "object": (0.015, 1.1, 0.09),
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


def _plan(scene, identifier):
    """Prepare and guard a curated look without changing any scene values.

    All animation checks precede edits, so a conflict leaves every setting intact.
    Object styling targets only existing Afterglow modifiers in the chosen collection.
    Audio, seed, placement, anchors, emitter, route and camera settings are preserved.
    """
    if not isinstance(identifier, str) or identifier not in _LOOKS:
        raise ValueError(f"Unknown Afterglow look: {identifier}")
    look = _LOOKS[identifier]
    fx = scene.afterglow.effects
    color_a, color_b = look["colors"]
    layer_values = {}
    for index, name in enumerate(effects.LAYERS):
        enabled = name in look["layers"]
        weight, band, timing, intensity = look["layers"].get(name, (0.5, "LOW", 1.0, 1.0))
        layer_values[name] = {"enabled": enabled, "weight": weight, "trigger_band": band,
                              "timing": timing, "intensity": intensity,
                              "color": color_a if index % 2 == 0 else color_b}
    fx_values = {"beam_glow": 1.0, "signal_graphics": identifier == "SIGNAL_GRID",
                 "signal_glow": 1.0, **look["fx"]}
    scene_paths = {f"afterglow.effects.{name}.{prop}"
                   for name, values in layer_values.items() for prop in values}
    scene_paths.update(f"afterglow.effects.{name}" for name in fx_values)
    _guard(scene, scene_paths)

    glow, intensity, decay = look["object"]
    object_values = {"Use Colors": True, "Color A": (*color_a, 1.0),
                     "Color B": (*color_b, 1.0), "Glow": glow,
                     "Intensity": intensity, "Decay": decay}
    objects = []
    collection = scene.afterglow.collection
    for ob in core.targets(collection) if collection else ():
        mod = core.find_modifier(ob)
        if mod:
            if ob.library and not ob.override_library:
                raise ValueError(f"Make {ob.name} local before applying a look")
            _guard(ob, {nodes._socket(mod, name).path_from_id("value")
                        for name in object_values})
            objects.append((ob, mod))

    return fx, layer_values, fx_values, object_values, objects


def preflight(scene, identifier):
    """Reject unavailable looks or protected animation before a setup changes objects."""
    _plan(scene, identifier)


def enabled_layers(identifier):
    """The layers a built-in look will enable, without consulting or mutating a scene."""
    if not isinstance(identifier, str) or identifier not in _LOOKS:
        raise ValueError(f"Unknown Afterglow look: {identifier}")
    return tuple(_LOOKS[identifier]["layers"])


def apply(scene, identifier):
    """Apply a built-in look after checking every affected scene and object control."""
    fx, layer_values, fx_values, object_values, objects = _plan(scene, identifier)
    for name, values in layer_values.items():
        layer = getattr(fx, name)
        for prop, value in values.items():
            setattr(layer, prop, value)
    for name, value in fx_values.items():
        setattr(fx, name, value)
    for ob, mod in objects:
        for name, value in object_values.items():
            nodes.set_input(mod, name, value)
    return sum(values["enabled"] for values in layer_values.values())
