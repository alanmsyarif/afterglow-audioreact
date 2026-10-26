"""Music reactions on user-authored native lights, with reversible data ownership.

Controls and the baked envelope belong to the object. Native light drivers read
those controls, so playback and rendering need no Python frame-change handler.
Each object receives an independent Light copy; the original data and animation
remain untouched for outside users and for Remove Reaction.
"""
import math

import bpy
from bpy.props import (BoolProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty)

from . import effect_schema, nodes

TAG = "afterglow_light_reaction"
OWNED = "afterglow_reaction_light"
VERSION = 1
FIELDS = {name: name.lower().replace(" ", "_") for name, _, _ in nodes.INPUTS}
FIELDS.update({"Base Power": "base_power", "Original Color": "base_color"})
_PREFIX = "_afterglow_light_"
_setting = 0
_REBAKE = {"Low", "High", "Decay", "Delay"}
_OUTPUT_PATHS = {"energy", "color"}


def _key(field):
    return _PREFIX + field


def _accessors(field, default):
    def get(settings):
        return settings.id_data.get(_key(field), default)

    def set(settings, value):
        settings.id_data[_key(field)] = tuple(value) if hasattr(value, "__len__") else value
        settings.id_data.update_tag()

    return get, set


def _changed(settings, context):
    if _setting or not managed(settings.id_data):
        return
    from . import props
    scene = getattr(context, "scene", None)
    if scene and settings.id_data.name in scene.objects:
        props._rebake(scene)


def _properties():
    fields = {}
    for name, kind, default in nodes.INPUTS:
        # Canonical animation is on the flat ID properties. RNA aliases are useful
        # to scripts, but cannot be animated: their setter writes would not create
        # a native dependency from the alias curve to the light's driver.
        args = {"name": name, "options": set()}
        if name in _REBAKE:
            args["update"] = _changed
        if kind == "NodeSocketSound":
            prop = PointerProperty(type=bpy.types.Sound, **args)
        else:
            getter, setter = _accessors(FIELDS[name], default)
            args.update(get=getter, set=setter)
            if kind == "NodeSocketBool":
                prop = BoolProperty(default=default, **args)
            elif kind == "NodeSocketInt":
                prop = IntProperty(default=default, **args)
            elif kind == "NodeSocketColor":
                prop = FloatVectorProperty(default=default, size=4, subtype="COLOR",
                                           min=0.0, max=1.0, **args)
            elif kind == "NodeSocketVector":
                prop = FloatVectorProperty(default=default, size=3, **args)
            else:
                if name not in {"Wave Lo", "Pulse Phase"}:
                    args["min"] = 0.0
                if name in {"Ripple Position", "Tap Min", "Tap Max"}:
                    args["max"] = 1.0
                prop = FloatProperty(default=default, **args)
        fields[FIELDS[name]] = prop
    fields["base_power"] = FloatProperty(
        name="Base Power", min=0.0, default=10.0,
        get=_accessors("base_power", 10.0)[0], set=_accessors("base_power", 10.0)[1], options=set(),
        description="Original light power, multiplied by the music reaction")
    fields["base_color"] = FloatVectorProperty(
        name="Original Color", size=3, subtype="COLOR", min=0.0, max=1.0,
        get=_accessors("base_color", (1.0, 1.0, 1.0))[0],
        set=_accessors("base_color", (1.0, 1.0, 1.0))[1], options=set(),
        default=(1.0, 1.0, 1.0))
    return fields


class AfterglowLightSettings(bpy.types.PropertyGroup):
    __annotations__ = _properties()


class _Socket:
    """The small socket API shared by object look, calibration and bake helpers."""

    def __init__(self, obj, name):
        self.id_data = obj
        self.field = FIELDS[name]

    @property
    def value(self):
        return getattr(self.id_data.afterglow_light, self.field)

    @value.setter
    def value(self, value):
        global _setting
        _setting += 1
        try:
            setattr(self.id_data.afterglow_light, self.field, value)
            self.id_data.update_tag()
        finally:
            _setting -= 1

    def path_from_id(self, property="value"):
        if property != "value":
            raise ValueError("Light reaction sockets expose only value")
        if self.field == "sound":
            return self.id_data.afterglow_light.path_from_id(self.field)
        return f'["{_key(self.field)}"]'

    def keyframe_insert(self, data_path="value", **kwargs):
        if data_path != "value":
            raise ValueError("Light reaction sockets expose only value")
        return self.id_data.keyframe_insert(self.path_from_id(), **kwargs)


class LightReaction:
    def __init__(self, obj):
        self.id_data = obj

    def socket(self, name):
        return _Socket(self.id_data, name)

    def draw_input(self, layout, name, text=None):
        socket = self.socket(name)
        layout.prop(self.id_data, socket.path_from_id(), text=text or name)


def _ensure_properties(obj):
    """Flat custom-property targets avoid Light -> Object -> Light RNA cycles."""
    for name, kind, default in nodes.INPUTS:
        if kind == "NodeSocketSound":
            continue
        key = _key(FIELDS[name])
        if key not in obj:
            obj[key] = default
        metadata = {"default": default, "description": name}
        if kind in {"NodeSocketFloat", "NodeSocketInt"} and name not in {"Wave Lo", "Pulse Phase"}:
            metadata["min"] = 0
        if name in {"Ripple Position", "Tap Min", "Tap Max"}:
            metadata["max"] = 1
        if kind == "NodeSocketColor":
            metadata.update(subtype="COLOR", min=0.0, max=1.0)
        obj.id_properties_ui(key).update(**metadata)
    for field, default in (("base_power", 10.0), ("base_color", (1.0, 1.0, 1.0))):
        key = _key(field)
        if key not in obj:
            obj[key] = default
        metadata = {"default": default, "min": 0.0}
        if field == "base_color":
            metadata.update(subtype="COLOR", max=1.0)
        obj.id_properties_ui(key).update(**metadata)


def managed(obj):
    return bool(obj and obj.type == "LIGHT" and TAG in obj)


def reaction(obj):
    if managed(obj) and obj[TAG].get("owned") == obj.data:
        return LightReaction(obj)
    return None


def preflight(objects):
    """Validate every native light before selection or building changes any data."""
    for obj in objects:
        if obj.type != "LIGHT":
            continue
        if obj.get(effect_schema.TAG) or obj.get(effect_schema.EMITTER_TAG):
            raise ValueError("Choose your own lights; generated environment lights have their own music reaction")
        if not obj.is_editable or obj.is_property_readonly("data"):
            raise ValueError(f"Make {obj.name} local before creating a light reaction")
        if obj.data is None:
            raise ValueError(f"Choose a native light with data for {obj.name}")
        if not math.isfinite(obj.data.energy) or obj.data.energy < 0:
            raise ValueError(f"Choose a finite nonnegative light power for {obj.name}")
        if not all(math.isfinite(value) for value in obj.data.color):
            raise ValueError(f"Choose a finite light color for {obj.name}")


def _strip_output_animation(data):
    """Copy actions before removing output channels from this owned light only."""
    ad = data.animation_data
    if not ad:
        return
    copies = {}

    def isolate(owner):
        action = getattr(owner, "action", None)
        if not action:
            return
        if action not in copies:
            cloned = action.copy()
            cloned[OWNED] = VERSION
            for layer in cloned.layers:
                for strip in layer.strips:
                    for bag in getattr(strip, "channelbags", ()):
                        for curve in list(bag.fcurves):
                            if curve.data_path in _OUTPUT_PATHS:
                                bag.fcurves.remove(curve)
            copies[action] = cloned
        slot = getattr(owner, "action_slot", None)
        identifier = slot.identifier if slot else None
        owner.action = copies[action]
        if identifier:
            matched = next((slot for slot in owner.action.slots if slot.identifier == identifier), None)
            if matched:
                owner.action_slot = matched

    def visit(strips):
        for strip in strips:
            isolate(strip)
            visit(getattr(strip, "strips", ()))

    isolate(ad)
    for track in ad.nla_tracks:
        visit(track.strips)
    for curve in list(ad.drivers):
        if curve.data_path in _OUTPUT_PATHS:
            ad.drivers.remove(curve)


def _driver(data, path, obj, expression, variables, index=None):
    curve = data.driver_add(path) if index is None else data.driver_add(path, index)
    driver = curve.driver
    driver.type = "SCRIPTED"
    driver.expression = expression
    for old in list(driver.variables):
        driver.variables.remove(old)
    for name, field in variables.items():
        variable = driver.variables.new()
        variable.name, variable.type = name, "SINGLE_PROP"
        target = variable.targets[0]
        target.id_type, target.id = "OBJECT", obj
        base, _, suffix = field.partition("[")
        target.data_path = f'["{_key(base)}"]' + ("[" + suffix if suffix else "")


def _drivers(obj, data):
    response = {"peak": "baked_peak", "gain": "gain", "threshold": "threshold",
                "ripple": "ripple", "rgain": "ripple_gain", "rthreshold": "ripple_threshold"}
    level = "max(peak-(rthreshold if ripple else threshold),0)*(rgain if ripple else gain)"
    _driver(data, "energy", obj, f"base*max({level},glow)*intensity",
            {**response, "base": "base_power", "glow": "glow", "intensity": "intensity"})
    for index in range(3):
        expression = f"a+(b-a)*abs((trail and style>0 and ripple)-min({level},1)) if colors or (trail and style>0 and ripple) else original"
        _driver(data, "color", obj, expression,
                {**response, "colors": "use_colors", "trail": "pulse_color", "style": "pulse_style", "a": f"color_a[{index}]",
                 "b": f"color_b[{index}]", "original": f"base_color[{index}]"}, index)


def _discard(data):
    if not data or data.users or not data.get(OWNED):
        return
    actions = set()
    ad = data.animation_data
    if ad:
        if ad.action:
            actions.add(ad.action)

        def visit(strips):
            for strip in strips:
                if getattr(strip, "action", None):
                    actions.add(strip.action)
                visit(getattr(strip, "strips", ()))

        for track in ad.nla_tracks:
            visit(track.strips)
    bpy.data.lights.remove(data)
    for action in actions:
        if action.users == 0 and action.get(OWNED):
            bpy.data.actions.remove(action)


def apply(obj):
    """Create or refresh one independently owned light and native response drivers."""
    preflight((obj,))
    if obj.type != "LIGHT":
        raise ValueError("Choose a native light for a light reaction")
    _ensure_properties(obj)
    record = obj.get(TAG)
    current = record.get("owned") if record else None
    original = record.get("original") if record else None
    reuse = bool(current and obj.data == current and current.get(OWNED))
    # The record itself is an ID user, hence the expected two users: object + record.
    # A duplicate may share both the data and copied ownership record; isolate it.
    other_objects = any(other != obj and other.data == obj.data for other in bpy.data.objects
                        if other.type == "LIGHT")
    if reuse and not other_objects:
        _drivers(obj, current)
        return LightReaction(obj)
    source = obj.data
    original = original if reuse else source
    owned = source.copy()
    owned.name = f"Noctune {obj.name}"
    owned.use_fake_user = False
    owned[OWNED] = VERSION
    try:
        _strip_output_animation(owned)
        if not reuse:
            settings = obj.afterglow_light
            settings.base_power = source.energy
            settings.base_color = source.color
        _drivers(obj, owned)
        obj.data = owned
        obj[TAG] = {"version": VERSION, "original": original, "owned": owned}
    except Exception:
        if obj.data == owned:
            obj.data = source
        _discard(owned)
        raise
    if current and current != owned:
        _discard(current)
    obj.update_tag()
    return LightReaction(obj)


def remove(obj):
    """Restore the original ID unless the user has assigned another light manually."""
    if not managed(obj):
        return
    # A manual data swap makes reaction() return None. Its object-owned envelope
    # still belongs to Noctune and must not survive removing the reaction.
    from . import bake
    bake.unkey(obj, LightReaction(obj))
    record = obj[TAG]
    original, owned = record.get("original"), record.get("owned")
    if obj.data == owned and original:
        obj.data = original
    del obj[TAG]
    _discard(owned)
    obj.update_tag()


def register():
    bpy.utils.register_class(AfterglowLightSettings)
    bpy.types.Object.afterglow_light = PointerProperty(type=AfterglowLightSettings)


def unregister():
    del bpy.types.Object.afterglow_light
    bpy.utils.unregister_class(AfterglowLightSettings)
