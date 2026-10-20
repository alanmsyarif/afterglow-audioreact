"""An additive starter stage and an original, self-contained preview track."""
import math
import os
import tempfile
import wave

import bmesh
import bpy
import numpy as np
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view


TAG = "afterglow_starter"
DURATION = 4.0
RATE = 44100
LAYOUT_KEY = "afterglow_layout"
LAYOUT_ITEMS = (
    ("RING", "Orbit Stage", "Twelve emissive columns around a circular graphite stage"),
    ("EQUALIZER", "Equalizer Wall", "Sixteen reactive meter bars on a graphite backboard"),
    ("TUNNEL", "Neon Tunnel", "Ten nested emissive portals for music-driven depth"),
)


def camera_for(collection):
    """Find the nearest starter stage's camera, even after nesting its lights collection.

    Name suffixes are deliberately ignored: Blender gives every repeated starter its
    own camera, and an explicitly requested view must select the matching one.
    """
    if collection is None:
        return None
    candidates = []
    for parent in bpy.data.collections:
        if not parent.get(TAG):
            continue
        cameras = [obj for obj in parent.objects if obj.type == "CAMERA" and obj.get(TAG)]
        if not cameras:
            continue
        pending, seen = [(parent, 0)], set()
        while pending:
            current, distance = pending.pop(0)
            if current in seen:
                continue
            seen.add(current)
            if current == collection:
                candidates.append((distance, parent.name, cameras[0]))
                break
            pending.extend((child, distance + 1) for child in current.children)
    return min(candidates, key=lambda item: (item[0], item[1]))[2] if candidates else None


def _preview_sound():
    """Synthesize and pack a short 120-BPM beat; no downloads or external assets."""
    samples = np.zeros(int(DURATION * RATE), dtype=np.float32)

    def add(start, duration, frequency, amplitude, decay):
        offset = int(start * RATE)
        t = np.arange(int(duration * RATE), dtype=np.float32) / RATE
        # A short attack avoids clicks while preserving a clear onset for every beat.
        envelope = np.minimum(t / 0.003, 1.0) * np.exp(-t / decay)
        segment = amplitude * envelope * np.sin(2 * np.pi * frequency * t)
        n = min(len(segment), len(samples) - offset)
        samples[offset:offset + n] += segment[:n]

    for beat in range(8):
        start = 0.125 + beat * 0.5
        add(start, 0.38, 65, 0.8, 0.085)
        add(start + 0.25, 0.16, 440 if beat % 2 else 660, 0.28, 0.045)
        add(start, 0.08, 6500, 0.16, 0.018)
        add(start + 0.25, 0.08, 8800, 0.12, 0.018)
    pcm = (np.clip(samples, -1.0, 1.0) * 30000).astype("<i2")
    fd, path = tempfile.mkstemp(prefix="afterglow-preview-", suffix=".wav")
    os.close(fd)
    sound = None
    try:
        with wave.open(path, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(RATE)
            output.writeframes(pcm.tobytes())
        sound = bpy.data.sounds.load(path, check_existing=False)
        sound.pack()
        sound.name = "Afterglow Preview • 120 BPM"
        sound[TAG] = True
        return sound
    except Exception:
        if sound is not None:
            bpy.data.sounds.remove(sound)
        raise
    finally:
        os.remove(path)


def _cylinder(collection, name, radius, depth, location, material, segments=32):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    try:
        bmesh.ops.create_cone(bm, cap_ends=True, segments=segments,
                             radius1=radius, radius2=radius, depth=depth)
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    obj[TAG] = True
    collection.objects.link(obj)
    bevel = obj.modifiers.new("Soft Edges", "BEVEL")
    bevel.width = min(radius * 0.12, depth * 0.1)
    bevel.segments = 3
    for polygon in mesh.polygons:
        polygon.use_smooth = len(polygon.vertices) == 4
    return obj


def _mesh_object(collection, name, bm, material, location, bevel=0.025):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    obj[TAG] = True
    collection.objects.link(obj)
    modifier = obj.modifiers.new("Soft Edges", "BEVEL")
    modifier.width, modifier.segments = bevel, 3
    return obj


def _box_vertices(bm, size, center=(0, 0, 0)):
    vertices = bmesh.ops.create_cube(bm, size=2)["verts"]
    for vertex in vertices:
        vertex.co = Vector(tuple(vertex.co[i] * size[i] / 2 + center[i] for i in range(3)))


def _box(collection, name, size, location, material, bevel=0.025):
    bm = bmesh.new()
    try:
        _box_vertices(bm, size)
        return _mesh_object(collection, name, bm, material, location, bevel)
    finally:
        bm.free()


def _portal(collection, name, width, height, location, material):
    bm = bmesh.new()
    thickness = 0.075
    try:
        for x in (-width / 2, width / 2):
            _box_vertices(bm, (thickness, thickness, height), (x, 0, height / 2))
        for z in (thickness / 2, height - thickness / 2):
            _box_vertices(bm, (width - thickness, thickness, thickness), (0, 0, z))
        return _mesh_object(collection, name, bm, material, location, bevel=0.015)
    finally:
        bm.free()


def _palette(index, count):
    mix = index / max(1, count - 1)
    return (0.06 + 0.8 * mix, 0.72 - 0.64 * mix, 1.0)


def _emission_material(name, color):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.diffuse_color = (*color, 1.0)
    tree = material.node_tree
    tree.nodes.clear()
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (*color, 1.0)
    emission.inputs["Strength"].default_value = 4.0
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(emission.outputs[0], output.inputs["Surface"])
    return material


def _aim(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


def _frame_stage(scene, camera, center):
    """Leave breathing room above the columns for the default environment beams."""
    ceiling = 2.25 + scene.afterglow.effects.height
    target = center + Vector((0, 0, ceiling * 0.36))
    offset = Vector((11, -14, 10)) - (target - center)
    effect_radius = 3.26 * scene.afterglow.effects.margin
    bounds = [center + Vector((radius * math.cos(2 * math.pi * step / 32),
                               radius * math.sin(2 * math.pi * step / 32), z))
              for radius, z in ((4.2, -0.18), (effect_radius, ceiling)) for step in range(32)]
    # Respect the user's render aspect ratio without changing it. Moving the new
    # camera backwards fits tall beams in landscape and the stage in portrait.
    for _ in range(24):
        camera.location = target + offset
        _aim(camera, target)
        camera.update_tag()
        bpy.context.view_layer.update()
        coords = [world_to_camera_view(scene, camera, point) for point in bounds]
        if all(0.06 <= co.x <= 0.94 and 0.06 <= co.y <= 0.94 and co.z > 0 for co in coords):
            break
        offset *= 1.12


def _points(objects):
    result = []
    for obj in objects:
        if obj.type == "MESH" and len(obj.data.vertices) <= 4096:
            result.extend(obj.matrix_world @ vertex.co for vertex in obj.data.vertices)
        else:
            result.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    return result


def _camera_stage(collection):
    camera = camera_for(collection)
    if camera is None:
        raise ValueError("Choose a starter-stage collection before fitting its camera")
    stage = next((c for c in camera.users_collection if c.get(TAG)), None)
    if stage is None:
        raise ValueError("The starter camera has no stage collection")
    return stage, camera


def _fit_collection(scene, camera, stage, collection):
    """Fit current source geometry and the configured effects volume in world space."""
    from . import core, effects
    bpy.context.view_layer.update()
    sources = core.targets(collection)
    if not sources:
        raise ValueError("The starter collection has no mesh, curve or text objects")
    source_points = [obj.matrix_world @ Vector(corner) for obj in sources for corner in obj.bound_box]
    lo = Vector(tuple(min(p[i] for p in source_points) for i in range(3)))
    hi = Vector(tuple(max(p[i] for p in source_points) for i in range(3)))
    fx = scene.afterglow.effects
    if fx.emitter:
        venue = effects.make_venue(scene)
    else:
        venue = effects.Venue(fx.venue_shape, (lo.x + hi.x) / 2, (lo.y + hi.y) / 2,
                              max((hi.x - lo.x) / 2, 0.25) * fx.margin,
                              max((hi.y - lo.y) / 2, 0.25) * fx.margin,
                              lo.z, hi.z + fx.height)
    objects = set(sources) | {obj for obj in stage.all_objects if obj.get(TAG) and obj.type == "MESH"}
    points = _points(objects)
    for index in range(64):
        point = venue.path_point(index / 64)
        points.extend((Vector((point.x, point.y, venue.z0)), Vector((point.x, point.y, venue.z1))))
    if not points or not all(math.isfinite(c) for point in points for c in point):
        raise ValueError("Stage geometry needs finite bounds before fitting a camera")
    bottom = min(p.z for p in points)
    top = max(p.z for p in points)
    center = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, bottom + (top - bottom) * 0.4))
    layout = stage.get(LAYOUT_KEY, "RING")
    direction = Vector({"RING": (11, -14, 6.3), "EQUALIZER": (2.2, -17, 3.5),
                        "TUNNEL": (1.5, -19, 2.3)}.get(layout, (11, -14, 6.3))).normalized()
    span = max((max(p[i] for p in points) - min(p[i] for p in points)) for i in range(3))
    offset = direction * max(3.0, span * 0.7)
    # Camera projection observes matrix_world, so update only this explicit generated
    # camera, and restore it on failure. Existing user cameras are never touched.
    original = camera.matrix_world.copy()
    try:
        for _ in range(64):
            location = center + offset
            matrix = (center - location).to_track_quat("-Z", "Y").to_matrix().to_4x4()
            matrix.translation = location
            camera.matrix_world = matrix
            bpy.context.view_layer.update()
            coords = [world_to_camera_view(scene, camera, point) for point in points]
            if all(0.055 <= co.x <= 0.945 and 0.055 <= co.y <= 0.945 and co.z > 0 for co in coords):
                return camera
            offset *= 1.12
        raise ValueError("Camera cannot frame these bounds; check its lens and camera settings")
    except Exception:
        camera.matrix_world = original
        bpy.context.view_layer.update()
        raise


def fit_camera(scene, collection):
    """Explicitly refit this starter's generated camera for the current render aspect.

    The active scene camera is not switched. Animated/driven transforms and active
    constraints are protected; callers can choose the fitted camera after success.
    """
    from . import presets
    stage, camera = _camera_stage(collection)
    if not camera.is_editable:
        raise ValueError("Make the starter camera local before fitting it")
    transform_paths = {"location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale",
                       "delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale"}
    current = camera
    while current:
        if presets._animation_paths(current) & transform_paths:
            raise ValueError("Remove camera or parent transform animation and drivers before fitting the stage camera")
        if any(not constraint.mute for constraint in current.constraints):
            raise ValueError("Mute camera or parent constraints before fitting the stage camera")
        current = current.parent
    return _fit_collection(scene, camera, stage, collection)


def create(context, layout="RING"):
    """Add a selected preview stage without replacing scene content or chosen audio.

    The target collection changes to the new stage; the active camera and chosen audio
    remain in place. Unknown templates are rejected before any data is created.
    """
    if layout not in {item[0] for item in LAYOUT_ITEMS}:
        raise ValueError(f"Unknown starter layout: {layout}")
    scene = context.scene
    sound = _preview_sound() if scene.afterglow.sound is None else None
    center = scene.cursor.location.copy()
    label = dict((item[0], item[1]) for item in LAYOUT_ITEMS)[layout]
    stage = bpy.data.collections.new("Afterglow Starter Stage" if layout == "RING" else "Afterglow " + label)
    stage[TAG], stage[LAYOUT_KEY] = True, layout
    scene.collection.children.link(stage)
    venue = bpy.data.collections.new("Afterglow Starter Lights" if layout == "RING" else "Afterglow " + label + " Lights")
    venue[TAG], venue[LAYOUT_KEY] = True, layout
    stage.children.link(venue)

    if layout == "RING":
        for index in range(12):
            angle = 2 * math.pi * index / 12
            height = 1.7 + 0.55 * (0.5 + 0.5 * math.cos(2 * angle))
            mix = (math.sin(angle) + 1.0) * 0.5
            color = (0.12 + 0.65 * mix, 0.65 - 0.5 * mix, 1.0)
            name = f"Afterglow Column {index + 1:02d}"
            material = _emission_material(name, color)
            _cylinder(venue, name, 0.16, height,
                      center + Vector((3.1 * math.cos(angle), 3.1 * math.sin(angle), height / 2)), material)
    elif layout == "EQUALIZER":
        for index in range(16):
            height = 2.4 + 0.8 * math.sin(math.pi * index / 15)
            name = f"Afterglow Meter {index + 1:02d}"
            material = _emission_material(name, _palette(index, 16))
            bar = _box(venue, name, (0.27, 0.16, height),
                       center + Vector(((index - 7.5) * 0.46, -0.15, height / 2 + 0.12)), material)
            bar.afterglow.meter = True
    else:
        for index in range(10):
            name = f"Afterglow Portal {index + 1:02d}"
            material = _emission_material(name, _palette(index, 10))
            _portal(venue, name, 5.4, 4.3, center + Vector((0, -4.5 + index, 0.08)), material)

    floor_material = bpy.data.materials.new("Afterglow Stage • Graphite")
    floor_material.use_nodes = True
    bsdf = floor_material.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (0.025, 0.03, 0.045, 1.0)
    bsdf.inputs["Metallic"].default_value = 0.45
    bsdf.inputs["Roughness"].default_value = 0.32
    if layout == "RING":
        _cylinder(stage, "Afterglow Stage", 4.2, 0.18, center + Vector((0, 0, -0.09)), floor_material, segments=96)
    elif layout == "EQUALIZER":
        _box(stage, "Afterglow Meter Plinth", (8.4, 2.4, 0.18), center + Vector((0, 0.15, -0.09)), floor_material)
        _box(stage, "Afterglow Meter Backboard", (8.2, 0.15, 3.8), center + Vector((0, 0.12, 1.8)), floor_material)
    else:
        _box(stage, "Afterglow Tunnel Plinth", (6.6, 11.2, 0.18), center + Vector((0, 0, -0.09)), floor_material)

    for name, position, color, energy in (
        ("Afterglow Key", (0, -1, 6), (0.55, 0.75, 1.0), 900),
        ("Afterglow Rim", (0, 3, 4), (0.8, 0.2, 1.0), 700),
    ):
        light_data = bpy.data.lights.new(name, "AREA")
        light_data.energy, light_data.size, light_data.color = energy, 5.0, color
        light = bpy.data.objects.new(name, light_data)
        stage.objects.link(light)
        light.location = center + Vector(position)
        light[TAG] = True
        _aim(light, center)

    camera_data = bpy.data.cameras.new("Afterglow Stage Camera")
    camera_data.lens = 35
    camera = bpy.data.objects.new("Afterglow Stage Camera", camera_data)
    stage.objects.link(camera)
    camera[TAG] = True
    if layout == "RING":
        _frame_stage(scene, camera, center)
    else:
        _fit_collection(scene, camera, stage, venue)
    if scene.camera is None:
        scene.camera = camera

    scene.afterglow.collection = venue
    if sound is not None:
        scene.afterglow.sound = sound
    context.view_layer.update()
    return venue
