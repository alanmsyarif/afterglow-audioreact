"""Geometry and event-local appearance for the opt-in pooled beam graphics."""
import math
import random

from . import effects

ATTRS = {"phase": 0.0, "dash": 1.0, "gaps": 0.0, "flow": 1.0, "fade": 1.0}


def init(shaft):
    for name, default in ATTRS.items():
        shaft[f"afterglow_look_{name}"] = default


def key_event(keys, shaft, fx, name, event_time, first, last, frame_of):
    """Hold one look for the complete event, refreshing even when the same slot is reused."""
    amount = fx.strike_variation if name == "strikes" else fx.gun_variation
    rng = random.Random(f"{fx.seed}:{name}:look:{event_time:.6f}")
    width = 1.0 + amount * rng.uniform(-0.65, 0.65)
    length = 1.0 + amount * rng.uniform(-0.45, 0.45) if name == "gun" else 1.0
    roll = amount * rng.uniform(-math.pi, math.pi) if name == "strikes" else 0.0
    values = {"phase": amount * rng.uniform(0.0, 100.0),
              "dash": 1.0 + amount * rng.uniform(-0.45, 0.45),
              "gaps": amount * rng.uniform(-0.12, 0.12),
              "flow": 1.0 + amount * rng.uniform(-0.4, 0.4),
              "fade": 1.0 + amount * rng.uniform(-0.4, 0.4)}
    for t in (first, last):
        f = frame_of(t)
        keys.add_vec(shaft, shaft, "scale", f, (width, width, length), "CONSTANT")
        keys.add(shaft, shaft, "rotation_euler", 2, f, roll, "CONSTANT")
        for prop, value in values.items():
            keys.add(shaft, shaft, f'["afterglow_look_{prop}"]', -1, f, value, "CONSTANT")
    return 1.0 + amount * rng.uniform(-0.25, 0.25)


def tubes(name, venue, rng, fx):
    if name == "gun":
        base = effects.GUN_TRACER * venue.radius
        length = base * fx.gun_length
        radius = base * math.tan(effects.BEAM_SPOT_SIZE[name] / 2) * fx.gun_width
        if fx.gun_style == "DART":
            rings = ((0.0, 0.02 * radius), (0.15 * length, 0.65 * radius),
                     (length, 0.015 * radius * fx.gun_taper))
        elif fx.gun_style == "STREAK":
            rings = ((0.0, 0.12 * radius), (0.8 * length, 0.12 * radius * fx.gun_taper),
                     (length, 0.01 * radius))
        else:
            rings = ((0.0, 0.02 * radius), (length, radius * fx.gun_taper))
        return [effects._tube(rings, 0)]
    parts = effects._beam_tubes(name, venue, rng)
    core, sleeve, _ = parts
    stems = [([(fx.strike_width * x, fx.strike_width * y, z) for x, y, z in verts], faces, slot)
             for verts, faces, slot in (core, sleeve)]
    companions = []
    for i in range(fx.strike_companions):
        verts, faces, slot = parts[2] if i == 0 else effects._beam_tubes(name, venue, rng)[2]
        cx = sum(v[0] for v in verts) / len(verts)
        cy = sum(v[1] for v in verts) / len(verts)
        spread = fx.strike_spread / effects.COMPANION_RING
        companions.append(([(cx * spread + (x - cx) * fx.strike_width,
                             cy * spread + (y - cy) * fx.strike_width, z) for x, y, z in verts], faces, slot))
    if fx.strike_style == "TWIN":
        twins = []
        for offset in (-0.12, 0.12):
            for verts, faces, slot in stems:
                twins.append(([(x + offset, y, z) for x, y, z in verts], faces, slot))
        return twins + companions
    if fx.strike_style == "RIBBON":
        return [([(4.0 * x, 0.18 * y, z) for x, y, z in verts], faces, slot)
                for verts, faces, slot in stems] + companions
    return stems + companions
