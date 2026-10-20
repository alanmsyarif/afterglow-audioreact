"""Beat-triggered laser fans with native, fog-free rays and bounded ownership.

Each pool slot owns two narrow spotlights and two additive curve fans. The fans are
visible in EEVEE and Cycles without changing a venue's materials, world or volume.
"""
import math
import random

import bpy
from mathutils import Quaternion, Vector

from . import effects

ENERGY = 450.0
DURATION = 0.85
RAY_KIND = "afterglow_laser_fan"


def _material(layer, glow):
    material = bpy.data.materials.new("AFX_lasers_glow")
    material[effects.TAG] = effects.VERSION
    material[effects.GAIN] = 40.0
    material.surface_render_method = "BLENDED"
    tree = material.node_tree
    tree.nodes.clear()
    attribute = tree.nodes.new("ShaderNodeAttribute")
    attribute.attribute_type, attribute.attribute_name = "OBJECT", effects.GLOW
    gain = tree.nodes.new("ShaderNodeMath")
    gain.name, gain.operation = "Glow", "MULTIPLY"
    gain.inputs[1].default_value = material[effects.GAIN] * glow
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (*layer.color, 1.0)
    transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
    add = tree.nodes.new("ShaderNodeAddShader")
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(attribute.outputs["Fac"], gain.inputs[0])
    tree.links.new(gain.outputs[0], emission.inputs["Strength"])
    tree.links.new(transparent.outputs[0], add.inputs[0])
    tree.links.new(emission.outputs[0], add.inputs[1])
    tree.links.new(add.outputs[0], output.inputs["Surface"])
    return material


def _fan(coll, light, count, spread, length, material):
    name = f"{light.name}_rays"
    curve = bpy.data.curves.new(name, "CURVE")
    curve[effects.TAG] = effects.VERSION
    curve.dimensions, curve.resolution_u = "3D", 1
    curve.bevel_depth, curve.bevel_resolution = 0.007, 2
    # Spotlights point down local -Z. The central ray shares that exact direction;
    # the remaining rays spread symmetrically across its local XZ plane.
    for index in range(count):
        angle = spread * (index / (count - 1) - 0.5)
        spline = curve.splines.new("POLY")
        spline.points.add(1)
        spline.points[0].co = (0.0, 0.0, 0.0, 1.0)
        spline.points[1].co = (length * math.sin(angle), 0.0, -length * math.cos(angle), 1.0)
    fan = bpy.data.objects.new(name, curve)
    fan[effects.TAG], fan[RAY_KIND], fan[effects.GLOW] = effects.VERSION, 1, 0.0
    fan.parent, fan.visible_shadow = light, False
    coll.objects.link(fan)
    curve.materials.append(material)
    return fan


def _mounts(venue):
    """A symmetric pair at the back of the shared venue, in its own rotated frame."""
    mounts = []
    for u in (0.125, 0.375):
        position = venue.path_point(u)
        position.z = venue.z0 + min(0.4, venue.span_z * 0.12)
        target = Vector((venue.center.x, venue.center.y, venue.z0 + venue.span_z * 0.53))
        direction = target - position
        distance = direction.length
        mounts.append((position, direction.to_track_quat("-Z", "Y"), max(distance * 1.55, 0.2)))
    return mounts


def build_layer(coll, scene, venue, events, fps, start, end):
    fx = scene.afterglow.effects
    layer = fx.lasers
    units = [effects._make_unit(coll, "lasers", slot, tuple(layer.color), n=2, size=0.02)
             for slot in range(effects.POOL["lasers"])]
    keys = effects._Keys()
    free = [-1e9] * len(units)
    mounts = _mounts(venue)
    # Independent stream: adding a laser layer never changes another layer's show.
    rng = random.Random(f"{fx.seed}:lasers:motion")
    material = _material(layer, fx.laser_glow) if events else None
    for lights in units:
        for light, (position, rotation, length) in zip(lights, mounts):
            light.location, light.rotation_quaternion = position, rotation
            if material:
                keys.shafts[light.name] = _fan(coll, light, fx.laser_count, fx.laser_spread, length, material)
    for index, (t0, strength) in enumerate(events):
        frame = start + t0 * fps
        timing = effects.sample_prop(scene, "afterglow.effects.lasers.timing", layer.timing, frame)
        intensity = effects.sample_prop(scene, "afterglow.effects.lasers.intensity", 1.0, frame)
        duration = DURATION * max(timing, 0.05)
        slot, busy = effects._assign(free, t0, duration)
        if busy:
            keys.cut(units[slot], frame, effects.CUT_FADE * fps)
        bright = effects._bright(strength, intensity)
        envelope = effects._envelope(t0, duration, min(0.04, duration * 0.15), 0.55)
        phase = rng.uniform(-0.12, 0.12) * fx.laser_sweep
        direction = -1 if index % 2 else 1
        for side, (light, (_, mount, _)) in enumerate(zip(units[slot], mounts)):
            fan = keys.shafts[light.name]
            for t, value in envelope:
                at = start + t * fps
                keys.add(light.data, light.data, "energy", -1, at, ENERGY * bright * value, "BEZIER")
                keys.add(fan, fan, effects.GLOW_PATH, -1, at, bright * value, "BEZIER")
            for fraction, swing in ((0.0, -1.0), (0.5, 0.0), (1.0, 1.0)):
                # Opposing pans cross over the venue; a smaller tilt lifts the fan
                # through the beat without the distracting spin of a full rotation.
                pan = (phase + direction * swing * 0.35 * fx.laser_sweep) * (-1 if side else 1)
                tilt = direction * swing * 0.10 * fx.laser_sweep
                rotation = Quaternion((0, 0, 1), pan) @ mount @ Quaternion((1, 0, 0), tilt)
                keys.add_vec(light, light, "rotation_quaternion", start + (t0 + fraction * duration) * fps,
                             rotation, "LINEAR")
    first = min((t for t, _ in events), default=0.0)
    bounds = (start + min(first, 0.0) * fps - 1, max(end, start + max(free) * fps) + 1)
    for lights in units:
        for light in lights:
            for frame in bounds:
                keys.add(light.data, light.data, "energy", -1, frame, 0.0, "BEZIER")
                if light.name in keys.shafts:
                    fan = keys.shafts[light.name]
                    keys.add(fan, fan, effects.GLOW_PATH, -1, frame, 0.0, "BEZIER")
    keys.write()
    for lights in units:
        for light in lights:
            effects._tag_action(light)
            effects._tag_action(light.data)
    for fan in keys.shafts.values():
        effects._tag_action(fan)
    return len(events)
