"""Connected audio-triggered signals; real lights by default, optional thin graphics.

Anchor coordinates are sampled in world space at build time. Generated data belongs
only to the owning effects collection; venue objects and materials are never edited.
"""
import bpy
from mathutils import Vector

from . import effects

ENERGY = 350.0
FADE = 0.4
MAX_ANCHORS = 16
HEAD = tuple(f"afterglow_signal_head_{axis}" for axis in "xyz")
PATH_KIND = "afterglow_signal_path"


def anchors(scene, venue):
    """Full 3D anchor positions in name order, or four shared venue waypoints."""
    coll = scene.afterglow.effects.signal_anchors
    if coll is None:
        return [venue.path_point(i / 4) for i in range(4)]
    bpy.context.view_layer.update()
    return [ob.matrix_world.translation.copy() for ob in sorted(coll.all_objects, key=lambda o: o.name)
            if not ob.get(effects.TAG)]


def add_anchor(scene, location):
    fx = scene.afterglow.effects
    coll = fx.signal_anchors
    if coll is None:
        coll = bpy.data.collections.new("Afterglow Signal Anchors")
        scene.collection.children.link(coll)
        fx.signal_anchors = coll
    if len(coll.all_objects) >= MAX_ANCHORS:
        raise ValueError(f"Signal Network supports up to {MAX_ANCHORS} anchors")
    ob = bpy.data.objects.new("Signal Anchor", None)
    ob["afterglow_signal_anchor"] = 1  # controls survive Remove Effects
    ob.empty_display_type, ob.empty_display_size = "SPHERE", 0.16
    ob.show_in_front = ob.hide_render = True
    ob.location = location
    coll.objects.link(ob)
    fx.signals.enabled = True
    return ob


def route(points):
    """An orthogonal polyline with no zero-length legs, preserving every anchor."""
    out = [Vector(points[0])]
    for point in points[1:]:
        p = Vector(point)
        for axis in range(3):
            q = out[-1].copy()
            q[axis] = p[axis]
            if (q - out[-1]).length > 1e-6:
                out.append(q)
    return out


def validate(scene, venue):
    points = anchors(scene, venue)
    if len(points) < 2 or not any((p - points[0]).length > 1e-6 for p in points[1:]):
        raise ValueError("Signal Network needs at least two distinct anchors")
    if len(points) > MAX_ANCHORS:
        raise ValueError(f"Signal Network supports up to {MAX_ANCHORS} anchors")
    return points


def distances(points):
    out = [0.0]
    for a, b in zip(points, points[1:]):
        out.append(out[-1] + (b - a).length)
    return out


def surface_hit(scene, position):
    """First renderable, non-Afterglow surface below a destination; None on a miss.

    Ray casts use evaluated scene geometry at the build frame, not a fabricated Z plane.
    Helpers and previous builds are looked through, never treated as venue surfaces.
    """
    direction = Vector((0, 0, -1))
    origin = Vector(position) + Vector((0, 0, 1e-4))
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for _ in range(64):
        hit, point, normal, _, ob, _ = scene.ray_cast(depsgraph, origin, direction)
        if not hit:
            return None
        if not (ob.get(effects.TAG) or ob.hide_render or ob.get(effects.EMITTER_TAG)):
            normal = normal.normalized()
            if normal.dot(direction) > 0:
                normal.negate()
            return point + 0.06 * normal, normal
        origin = point + 1e-3 * direction
    return None


def plans(scene, venue):
    """Forward/reverse routes, with a final surface descent when a real surface exists."""
    fx = scene.afterglow.effects
    nodes = validate(scene, venue)
    branches = [nodes] if fx.signal_route == "CHAIN" else [[nodes[0], p] for p in nodes[1:]]
    out = []
    for branch in branches:
        base = route(branch)
        if len(base) < 2:
            continue  # coincident spoke destinations do not create zero-duration pulses
        pair = []
        for points in (list(base), list(reversed(base))):
            normal = Vector((0, -1, 0))  # floating frames stand upright in world XZ
            landing = surface_hit(scene, points[-1]) if fx.signal_surface else None
            if landing:
                point, normal = landing
                points += route((points[-1], point))[1:]
            pair.append((points, normal))
        out.append(pair)
    return out


def signature(scene):
    fx = scene.afterglow.effects
    return (fx.signal_route, fx.signal_direction, fx.signal_surface, fx.signal_graphics,
            *(round(getattr(fx, name), 4) for name in
              ("signal_speed", "signal_width", "signal_tail", "signal_frame_size")),
            tuple(tuple(round(v, 4) for v in p) for p in anchors(scene, None))
            if fx.signal_anchors is not None else None)


def _material(layer, glow, tail=0.75):
    mat = bpy.data.materials.new("AFX_signals_glow")
    mat[effects.TAG] = effects.VERSION
    mat[effects.GAIN] = 30.0
    mat.surface_render_method = "BLENDED"  # additive emission needs forward transparency in EEVEE
    nt = mat.node_tree
    nt.nodes.clear()
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_type = "OBJECT"
    attr.attribute_name = effects.GLOW
    gain = nt.nodes.new("ShaderNodeMath")
    gain.name, gain.operation = "Glow", "MULTIPLY"
    gain.inputs[1].default_value = mat[effects.GAIN] * glow
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (*layer.color, 1.0)
    transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
    add = nt.nodes.new("ShaderNodeAddShader")
    output = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(attr.outputs["Fac"], gain.inputs[0])
    def attribute(name):
        node = nt.nodes.new("ShaderNodeAttribute")
        node.attribute_type, node.attribute_name = "OBJECT", name
        return node.outputs["Factor"]

    def math_node(operation, a, b, clamp=False):
        node = nt.nodes.new("ShaderNodeMath")
        node.operation, node.use_clamp = operation, clamp
        for socket, value in zip(node.inputs, (a, b)):
            if isinstance(value, bpy.types.NodeSocket):
                nt.links.new(value, socket)
            else:
                socket.default_value = value
        return node.outputs[0]

    head = nt.nodes.new("ShaderNodeCombineXYZ")
    for socket, name in zip(head.inputs, HEAD):
        nt.links.new(attribute(name), socket)
    geometry = nt.nodes.new("ShaderNodeNewGeometry")
    distance = nt.nodes.new("ShaderNodeVectorMath")
    distance.operation = "DISTANCE"
    nt.links.new(geometry.outputs["Position"], distance.inputs[0])
    nt.links.new(head.outputs[0], distance.inputs[1])
    fade = math_node("SUBTRACT", 1.0, math_node("DIVIDE", distance.outputs["Value"], max(tail, 0.01)), True)
    # Euclidean head distance softens the tail through corners; bevel factors still bound
    # its exact arc length. Frames bypass this falloff and keep their arrival envelope.
    weight = math_node("SUBTRACT", 1.0, math_node("MULTIPLY", attribute(PATH_KIND),
                       math_node("SUBTRACT", 1.0, math_node("MULTIPLY", fade, fade))))
    nt.links.new(math_node("MULTIPLY", gain.outputs[0], weight), emission.inputs["Strength"])
    nt.links.new(transparent.outputs[0], add.inputs[0])
    nt.links.new(emission.outputs[0], add.inputs[1])
    nt.links.new(add.outputs[0], output.inputs["Surface"])
    return mat


def _curve(coll, light, kind, label, lines, width, material):
    name = f"{light.name}_{kind}_{label}"
    data = bpy.data.curves.new(name, "CURVE")
    data[effects.TAG] = effects.VERSION
    data.dimensions = "3D"
    data.bevel_depth = width / 2
    data.bevel_resolution = 1
    data.bevel_factor_mapping_start = data.bevel_factor_mapping_end = "SPLINE"
    for line in lines:
        spline = data.splines.new("POLY")
        spline.points.add(len(line) - 1)
        for point, co in zip(spline.points, line):
            point.co = (*co, 1.0)
    ob = bpy.data.objects.new(name, data)
    ob[effects.TAG] = effects.VERSION
    ob["afterglow_signal_kind"] = kind
    ob[effects.GLOW] = 0.0
    ob[PATH_KIND] = 1.0 if kind == "path" else 0.0
    for name in HEAD:
        ob[name] = 0.0
    ob.parent = light
    ob.visible_shadow = False
    ob.rotation_mode = "QUATERNION"
    coll.objects.link(ob)
    data.materials.append(material)
    return ob


def _frame_lines(size):
    lines = []
    for half in (0.5, 0.33):
        arm = half * 0.55
        for x, y in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
            lines.append([(x * (half - arm) * size, y * half * size, 0),
                          (x * half * size, y * half * size, 0),
                          (x * half * size, y * (half - arm) * size, 0)])
    return lines


def _graphic_keys(keys, path, frame, light, points, cum, normal, t0, arrival, speed, bright, fx, fps, start):
    def add(struct, prop, t, value):
        keys.add(struct, struct, prop, -1, start + t * fps, value, "LINEAR")

    tail = fx.signal_tail / speed
    for p, d in zip(points, cum):
        # Curves are parented for ownership/live retuning, but their route stays in world space.
        keys.add_vec(path, path, "location", start + (t0 + d / speed) * fps, -p, "LINEAR")
        for name, value in zip(HEAD, p):
            add(path, f'["{name}"]', t0 + d / speed, value)
    keys.add_vec(path, path, "location", start + (arrival + max(FADE, tail)) * fps, -points[-1], "LINEAR")
    for t, v in ((t0, 0), (arrival, 1), (arrival + tail, 1)):
        add(path.data, "bevel_factor_end", t, v)
    for t, v in ((t0, 0), (t0 + tail, 0), (arrival + tail, 1)):
        add(path.data, "bevel_factor_start", t, v)
    ramp = min(0.03, 0.2 * (arrival - t0), 0.2 * tail)
    for t, v in ((t0, 0), (t0 + ramp, bright), (arrival + tail - ramp, bright), (arrival + tail, 0)):
        add(path, effects.GLOW_PATH, t, v)
    q = Vector((0, 0, 1)).rotation_difference(normal)
    keys.add_vec(frame, frame, "rotation_quaternion", start + t0 * fps, q, "LINEAR")
    for t, v in ((arrival, 0), (arrival + 0.06, bright), (arrival + FADE, 0)):
        add(frame, effects.GLOW_PATH, t, v)
    for t, v in ((arrival, 0), (arrival + 0.08, 1), (arrival + FADE, 1)):
        add(frame.data, "bevel_factor_end", t, v)
    for t, scale in ((arrival, 0.6), (arrival + FADE, 1.3)):
        keys.add_vec(frame, frame, "scale", start + t * fps, (scale,) * 3, "LINEAR")


def build_layer(coll, scene, venue, events, fps, start, end):
    fx = scene.afterglow.effects
    layer = fx.signals
    branches = plans(scene, venue)
    units = [effects._make_unit(coll, "signals", slot, tuple(layer.color))
             for slot in range(effects.POOL["signals"])]
    keys = effects._Keys()
    free = [-1e9] * len(units)
    material = _material(layer, fx.signal_glow, fx.signal_tail) if fx.signal_graphics and events else None
    paths, frames = {}, {}
    for i, (t0, strength) in enumerate(events):
        reverse = fx.signal_direction == "REVERSE" or (fx.signal_direction == "ALTERNATE" and i % 2 == 1)
        points, normal = branches[i % len(branches)][int(reverse)]
        cum = distances(points)
        f0 = start + t0 * fps
        timing = effects.sample_prop(scene, "afterglow.effects.signals.timing", layer.timing, f0)
        intensity = effects.sample_prop(scene, "afterglow.effects.signals.intensity", 1.0, f0)
        speed = fx.signal_speed / max(timing, 0.05)
        duration = cum[-1] / speed
        arrival = t0 + duration
        tail = fx.signal_tail / speed if material else 0.0
        slot, busy = effects._assign(free, t0, duration + max(FADE, tail))
        light = units[slot][0]
        if busy:
            keys.cut(units[slot], f0, effects.CUT_FADE * fps)
        for p, d in zip(points, cum):
            keys.add_vec(light, light, "location", start + (t0 + d / speed) * fps, p, "LINEAR")
        keys.add_vec(light, light, "location", start + (arrival + FADE) * fps, points[-1], "LINEAR")
        bright = strength * max(intensity, 0.0)
        env = ((t0, 0.0), (t0 + min(0.03, 0.2 * duration), 1.0),
               (arrival, 1.0), (arrival + 0.04, 2.0), (arrival + FADE, 0.0))
        for t, e in env:
            keys.add(light.data, light.data, "energy", -1, start + t * fps, ENERGY * bright * e, "BEZIER")
        if material:
            if light.name not in frames:
                frame = _curve(coll, light, "frame", slot, _frame_lines(fx.signal_frame_size), fx.signal_width, material)
                frames[light.name] = keys.shafts[light.name] = frame
                keys.extras.setdefault(light.name, []).append(frame.data)
            route_id = (slot, i % len(branches), int(reverse))
            if route_id not in paths:
                path = _curve(coll, light, "path", len(paths), [points], fx.signal_width, material)
                paths[route_id] = path
                keys.extras.setdefault(light.name, []).extend((path, path.data))
            _graphic_keys(keys, paths[route_id], frames[light.name], light, points, cum, normal,
                          t0, arrival, speed, bright, fx, fps, start)
    first = min((t for t, _ in events), default=0.0)
    for (light,) in units:
        for f in (start + min(first, 0.0) * fps - 1, max(end, start + max(free) * fps) + 1):
            keys.add(light.data, light.data, "energy", -1, f, 0.0, "BEZIER")
    for ob in (*paths.values(), *frames.values()):
        for f in (start + min(first, 0.0) * fps - 1, max(end, start + max(free) * fps) + 1):
            keys.add(ob, ob, effects.GLOW_PATH, -1, f, 0.0, "LINEAR")
    keys.write()
    for (light,) in units:
        effects._tag_action(light)
        effects._tag_action(light.data)
    for ob in (*paths.values(), *frames.values()):
        effects._tag_action(ob)
        effects._tag_action(ob.data)
    return len(events)
