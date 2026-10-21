"""Portable, declarative style snapshots; never project wiring or executable code.

Schema version 3 captures independent strike and gun beam visibility and glow.
Versions 1 and 2 retain their shared beam appearance; version 1 disables lasers.
A common object style comes from the first reacting object by name in the
chosen collection. FX-only scenes use object=None. Applying a snapshot styles existing
reacting objects, but does not create modifiers, build effects, or refresh a bake.
"""
import json
import math

from . import core, effects, nodes, presets

FORMAT = "afterglow.look"
VERSION = 3
MAX_BYTES = 65536

# Public, stable field names: omit project-specific placement/routing/mixer controls.
LAYER_FIELDS = ("enabled", "weight", "trigger_band", "timing", "intensity", "color")
BEAM_FIELDS = ("visible_beam", "beam_glow")
BEAM_LAYERS = ("strikes", "gun")
OBJECT_FIELDS = ("Use Colors", "Color A", "Color B", "Glow", "Intensity", "Decay")

# (type, lower/enum choices, upper). Numeric upper bounds also limit pathological
# external payloads while leaving substantially more range than the UI's soft limits.
_LIMIT = 1_000_000.0
_V1_EFFECT_SPECS = {
    "beams": ("bool",),
    "beam_glow": ("float", 0.0, _LIMIT),
    "strike_style": ("enum", ("FILAMENT", "TWIN", "RIBBON")),
    "strike_width": ("float", 0.1, _LIMIT),
    "strike_variation": ("float", 0.0, 1.0),
    "strike_companions": ("int", 0, 4),
    "strike_spread": ("float", 0.0, _LIMIT),
    "gun_style": ("enum", ("TRACER", "DART", "STREAK")),
    "gun_length": ("float", 0.05, 4.0),
    "gun_width": ("float", 0.1, _LIMIT),
    "gun_variation": ("float", 0.0, 1.0),
    "gun_taper": ("float", 0.05, 3.0),
    "gun_fade": ("float", 0.25, 6.0),
    "beam_direction": ("enum", ("UP", "DOWN")),
    "beam_gaps": ("float", 0.0, 1.0),
    "beam_dash": ("float", 0.05, _LIMIT),
    "beam_walk": ("enum", ("UP", "DOWN", "OFF")),
    "beam_walk_time": ("float", 0.02, 10.0),
    "beam_flow": ("float", 0.0, _LIMIT),
    "cascade_count": ("int", 1, 8),
    "cascade_spacing": ("float", 0.0, 0.8),
    "cascade_size": ("float", 0.0, _LIMIT),
    "signal_graphics": ("bool",),
    "signal_width": ("float", 0.001, _LIMIT),
    "signal_tail": ("float", 0.01, _LIMIT),
    "signal_frame_size": ("float", 0.05, _LIMIT),
    "signal_glow": ("float", 0.0, _LIMIT),
}
_V1_LAYERS = ("strikes", "blooms", "flashes", "trails", "downward", "gun", "signals")
_LASER_DEFAULTS = {
    "laser_count": 7,
    "laser_spread": 1.0,
    "laser_sweep": 0.7,
    "laser_glow": 1.0,
}
EFFECT_SPECS = {
    **_V1_EFFECT_SPECS,
    "laser_count": ("int", 3, 12),
    "laser_spread": ("float", 0.1, 2.6),
    "laser_sweep": ("float", 0.0, 1.6),
    "laser_glow": ("float", 0.0, _LIMIT),
}
EFFECT_FIELDS = tuple(EFFECT_SPECS)
_LAYER_SPECS = {
    "enabled": ("bool",),
    "weight": ("float", 0.0, 1.0),
    "trigger_band": ("enum", tuple(item[0] for item in effects.BAND_ITEMS)),
    "timing": ("float", 0.25, 4.0),
    "intensity": ("float", 0.0, 4.0),
    "color": ("color", 3),
}
_BEAM_SPECS = {"visible_beam": ("bool",), "beam_glow": ("float", 0.0, _LIMIT)}
_OBJECT_SPECS = {
    "Use Colors": ("bool",),
    "Color A": ("color", 4),
    "Color B": ("color", 4),
    "Glow": ("float", 0.0, _LIMIT),
    "Intensity": ("float", 0.0, _LIMIT),
    "Decay": ("float", 0.0, _LIMIT),
}


def layer_fields(name):
    """Exact current fields for a layer; only strikes and gun bursts have beams."""
    return LAYER_FIELDS + (BEAM_FIELDS if name in BEAM_LAYERS else ())


def _layer_specs(name, version):
    return {**_LAYER_SPECS, **_BEAM_SPECS} if version >= 3 and name in BEAM_LAYERS else _LAYER_SPECS


def _keys(value, keys, location):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"Invalid {location}: fields must be {', '.join(keys)}")


def _number(value, lo, hi, location, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid {location}: expected a {'whole' if integer else 'finite'} number")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"Invalid {location}: number is out of range") from None
    if not math.isfinite(number):
        raise ValueError(f"Invalid {location}: number must be finite")
    if integer and (not isinstance(value, int) or number != int(number)):
        raise ValueError(f"Invalid {location}: expected a whole number")
    # RNA stores float32, so a captured value at a legal boundary can differ by a
    # few ulps. Normalize only that precision-sized boundary discrepancy.
    if number < lo and math.isclose(number, lo, rel_tol=1e-7, abs_tol=1e-9):
        number = lo
    if number > hi and math.isclose(number, hi, rel_tol=1e-7, abs_tol=1e-9):
        number = hi
    if not lo <= number <= hi:
        raise ValueError(f"Invalid {location}: expected {lo:g} to {hi:g}")
    return int(number) if integer else number


def _value(value, spec, location):
    kind = spec[0]
    if kind == "bool":
        if type(value) is not bool:
            raise ValueError(f"Invalid {location}: expected true or false")
        return value
    if kind == "enum":
        if not isinstance(value, str) or value not in spec[1]:
            raise ValueError(f"Invalid {location}: unsupported choice")
        return value
    if kind == "color":
        if not isinstance(value, (list, tuple)) or len(value) != spec[1]:
            raise ValueError(f"Invalid {location}: expected {spec[1]} color components")
        return [_number(component, 0.0, 1.0, location) for component in value]
    return _number(value, spec[1], spec[2], location, integer=kind == "int")


def _record(value, specs, location):
    _keys(value, specs, location)
    return {name: _value(value[name], spec, f"{location}.{name}")
            for name, spec in specs.items()}


def validate(data):
    """Return a normalized independent snapshot, or reject it before any mutations."""
    _keys(data, ("format", "version", "layers", "effects", "object"), "look")
    if data["format"] != FORMAT or type(data["version"]) is not int or data["version"] not in (1, 2, VERSION):
        raise ValueError("Unsupported look format or version; use an Afterglow look version 1, 2 or 3")
    version = data["version"]
    legacy = version == 1
    layer_names = _V1_LAYERS if legacy else effects.LAYERS
    _keys(data["layers"], layer_names, "layers")
    layers = {name: _record(data["layers"][name], _layer_specs(name, version), f"layers.{name}")
              for name in layer_names}
    appearance = _record(data["effects"], _V1_EFFECT_SPECS if legacy else EFFECT_SPECS, "effects")
    if legacy:
        layers["lasers"] = {"enabled": False, "weight": 0.8, "trigger_band": "LOW",
                            "timing": 1.0, "intensity": 1.0, "color": [0.3, 1.0, 0.55]}
        appearance.update(_LASER_DEFAULTS)
    if version < 3:
        for name in BEAM_LAYERS:
            layers[name].update(visible_beam=appearance["beams"], beam_glow=appearance["beam_glow"])
    obj = None if data["object"] is None else _record(data["object"], _OBJECT_SPECS, "object")
    return {"format": FORMAT, "version": VERSION, "layers": layers,
            "effects": appearance, "object": obj}


def _plain(value):
    if isinstance(value, (bool, str, int, float)):
        return value
    return list(value)


def capture(scene):
    """Capture styling only; the first reacting target by name supplies object style."""
    fx = scene.afterglow.effects
    layers = {name: {prop: _plain(getattr(getattr(fx, name), prop)) for prop in layer_fields(name)}
              for name in effects.LAYERS}
    object_style = None
    collection = scene.afterglow.collection
    for ob in core.targets(collection) if collection else ():
        mod = core.find_modifier(ob)
        if mod:
            object_style = {name: _plain(nodes.get_input(mod, name)) for name in OBJECT_FIELDS}
            break
    return validate({"format": FORMAT, "version": VERSION, "layers": layers,
                     "effects": {name: _plain(getattr(fx, name)) for name in EFFECT_FIELDS},
                     "object": object_style})


def _plan(scene, data):
    """Validate snapshot and animation conflicts without applying any values."""
    data = validate(data)
    scene_paths = {f"afterglow.effects.{name}.{prop}"
                   for name in effects.LAYERS for prop in layer_fields(name)}
    scene_paths.update(f"afterglow.effects.{name}" for name in EFFECT_FIELDS)
    presets._guard(scene, scene_paths)
    objects = []
    if data["object"] is not None:
        collection = scene.afterglow.collection
        for ob in core.targets(collection) if collection else ():
            mod = core.find_modifier(ob)
            if mod:
                if not ob.is_editable:
                    raise ValueError(f"Make {ob.name} local before applying a look")
                presets._guard(ob, {nodes._socket(mod, name).path_from_id("value")
                                    for name in OBJECT_FIELDS})
                objects.append((ob, mod))
    return data, objects


def preflight(scene, data):
    """Return normalized data after validation and animation guards, without mutation."""
    data, _ = _plan(scene, data)
    return data


def apply(scene, data):
    """Apply a portable style to existing reacting objects, preserving project wiring."""
    data, objects = _plan(scene, data)
    fx = scene.afterglow.effects
    for name, values in data["layers"].items():
        layer = getattr(fx, name)
        for prop, value in values.items():
            setattr(layer, prop, value)
    for prop, value in data["effects"].items():
        setattr(fx, prop, value)
    for ob, mod in objects:
        for prop, value in data["object"].items():
            nodes.set_input(mod, prop, value)
    return sum(values["enabled"] for values in data["layers"].values())


def dumps(scene):
    """Serialize a captured look; no process state, paths or credentials are included."""
    text = json.dumps(capture(scene), indent=2, sort_keys=True, allow_nan=False)
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Look exceeds the 64 KiB size limit")
    return text


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Duplicate look field: {key}")
        value[key] = item
    return value


def _invalid_constant(value):
    raise ValueError(f"Invalid look number: {value}")


def loads(text):
    """Parse strict versioned JSON without executing code or interpreting any paths."""
    if not isinstance(text, str):
        raise ValueError("Look data must be JSON text")
    try:
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise ValueError("Look exceeds the 64 KiB size limit")
        data = json.loads(text, object_pairs_hook=_unique_object,
                          parse_constant=_invalid_constant)
    except (json.JSONDecodeError, RecursionError, UnicodeError) as error:
        raise ValueError("Invalid look JSON") from error
    return validate(data)
