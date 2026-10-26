"""Native spline direction survives bends, cyclic seams, and saved playback."""
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import core, materials, nodes


POINTS = ((-2, -1, 0), (2, -1, 0), (2, 1, 0), (-2, 1, 0))


def curve(kind='POLY', bevel=.12, cyclic=False, collection=None):
    data = bpy.data.curves.new('User curve', 'CURVE')
    data.dimensions, data.bevel_depth, data.resolution_u = '3D', bevel, 12
    spline = data.splines.new(kind)
    points = spline.bezier_points if kind == 'BEZIER' else spline.points
    points.add(3)
    for point, co in zip(points, POINTS):
        point.co = co if kind == 'BEZIER' else (*co, 1.0)
        if kind == 'BEZIER':
            point.handle_left_type = point.handle_right_type = 'AUTO'
    spline.use_cyclic_u = cyclic
    obj = bpy.data.objects.new('User curve', data)
    (collection or bpy.context.scene.collection).objects.link(obj)
    data.materials.append(helpers.emission_material('Original neon', 1.0, (1, 1, 1, 1)))
    return obj


def setup(obj):
    scene = bpy.context.scene
    collection = bpy.data.collections.new('Reaction')
    scene.collection.children.link(collection)
    collection.objects.link(obj)
    s = scene.afterglow
    s.collection, s.sound = collection, helpers.sound()
    s['ripple'], s['through'], s['fast_playback'] = True, True, True
    core.apply(scene)
    mod = core.reaction(obj)
    for name, value in (('Use Baked', True), ('Through', True), ('Use Colors', False),
                        ('Glow', 0.0), ('Ripple Gain', 1.0), ('Ripple Threshold', 0.0)):
        nodes.set_input(mod, name, value)
    for k in range(nodes.TAPS):
        nodes.set_input(mod, f'Baked Tap {k}', 1.0 if k < 3 else 0.0)
    return scene, mod


def evaluated(obj):
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    return ev, ev.to_mesh(preserve_all_data_layers=True, depsgraph=dg)


def test_bare_curve_follows_arc_length_and_reverses_without_editing_splines():
    obj = curve(bevel=0)
    before = tuple(tuple(p.co) for p in obj.data.splines[0].points)
    scene, mod = setup(obj)
    assert nodes.get_input(mod, 'Wave Curve')
    assert (nodes.get_input(mod, 'Tap Min'), nodes.get_input(mod, 'Tap Max')) == (0, 1)
    for reverse, expected in ((False, (0, .4, .6, 1)), (True, (1, .6, .4, 0))):
        scene.afterglow['ripple_reverse'] = reverse
        core.replace(scene)
        ev, mesh = evaluated(obj)
        try:
            values = [p.vector.x for p in mesh.attributes['ar_wave'].data]
            assert all(abs(a-b) < 1e-5 for a, b in zip(values, expected)), values
        finally:
            ev.to_mesh_clear()
    assert tuple(tuple(p.co) for p in obj.data.splines[0].points) == before


def test_beveled_spline_types_keep_geometry_and_cyclic_seams():
    for kind in ('POLY', 'BEZIER', 'NURBS'):
        for cyclic in (False, True):
            obj = curve(kind, cyclic=cyclic)
            ev, mesh = evaluated(obj)
            before = (len(mesh.vertices), len(mesh.polygons))
            ev.to_mesh_clear()
            scene, mod = setup(obj)
            ev, mesh = evaluated(obj)
            try:
                assert (len(mesh.vertices), len(mesh.polygons)) == before
                attr = mesh.attributes['ar_wave']
                assert attr.domain == 'CORNER'
                values = [p.vector.x for p in attr.data]
                assert min(values) < .001 and max(values) > .999, (kind, cyclic, min(values), max(values))
                uv = mesh.uv_layers['UVMap']
                assert all(abs(value - p.uv.x) < 1e-5 for value, p in zip(values, uv.data))
                if cyclic:
                    per_vertex = {}
                    for loop, value in zip(mesh.loops, values):
                        per_vertex.setdefault(loop.vertex_index, []).append(value)
                    assert any(min(v) < .001 and max(v) > .999 for v in per_vertex.values())
            finally:
                ev.to_mesh_clear()


def camera(scene):
    cam = bpy.data.objects.new('Test camera', bpy.data.cameras.new('Test camera'))
    cam.location = (0, 0, 8)
    cam.data.type, cam.data.ortho_scale = 'ORTHO', 5.0
    scene.collection.objects.link(cam)
    scene.camera = cam
    scene.render.resolution_x, scene.render.resolution_y = 96, 64
    scene.render.resolution_percentage = 100
    scene.world = bpy.data.worlds.new('Test black')
    scene.world.color = (0, 0, 0)
    scene.view_settings.view_transform = 'Standard'
    scene.render.image_settings.file_format = 'PNG'


def sampled_arms(scene):
    path = str(Path(tempfile.gettempdir()) / 'noctune-curve-ripple.png')
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = image.size
        # Ortho width 5, image height 10/3: sample x=0 on each arm.
        def sample(y):
            row = round(h / 2 + y * w / 5)
            i = (row * w + w // 2) * 4
            return sum(image.pixels[i:i+3]) / 3
        return sample(-1), sample(1)
    finally:
        bpy.data.images.remove(image)


def test_bent_curve_renders_in_spline_order_in_eevee_and_cycles_and_after_reopen():
    obj = curve()
    scene, mod = setup(obj)
    camera(scene)
    for engine in ('BLENDER_EEVEE', 'CYCLES'):
        scene.render.engine = engine
        if engine == 'CYCLES':
            scene.cycles.samples, scene.cycles.device = 4, 'CPU'
        for reverse in (False, True):
            scene.afterglow['ripple_reverse'] = reverse
            core.replace(scene)
            bottom, top = sampled_arms(scene)
            bright, dark = (top, bottom) if reverse else (bottom, top)
            assert bright > .7 and dark < .12, (engine, reverse, bottom, top)
    path = str(Path(tempfile.gettempdir()) / 'noctune-curve-ripple.blend')
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path, use_scripts=False)
    bottom, top = sampled_arms(bpy.context.scene)
    assert top > .7 and bottom < .12, (bottom, top)


def test_mesh_and_light_keep_spatial_wave_and_material_restore():
    obj = curve()
    original = helpers.snapshot(obj.data.materials[0])
    scene, mod = setup(obj)
    mesh = helpers.plane('Ordinary mesh', scene.afterglow.collection,
                         helpers.emission_material('Mesh material'))
    light = bpy.data.objects.new('Native light', bpy.data.lights.new('Native light', 'POINT'))
    scene.afterglow.collection.objects.link(light)
    core.apply(scene)
    assert nodes.get_input(core.reaction(obj), 'Wave Curve')
    assert not nodes.get_input(core.reaction(mesh), 'Wave Curve')
    assert not nodes.get_input(core.reaction(light), 'Wave Curve')
    core.remove(scene)
    assert helpers.snapshot(obj.data.materials[0]) == original
    assert len(obj.data.splines) == 1 and not obj.modifiers



def test_multiple_splines_follow_their_own_start_points_after_transform():
    obj = curve(bevel=.08)
    spline = obj.data.splines.new('POLY')
    spline.points.add(3)
    for point, co in zip(spline.points, reversed(POINTS)):
        point.co = (co[0], co[1], co[2] + 2.0, 1.0)
    obj.rotation_euler = (.2, -.5, .8)
    obj.scale = (2, .5, 1.5)
    scene, mod = setup(obj)
    ev, mesh = evaluated(obj)
    try:
        values = mesh.attributes['ar_wave'].data
        starts, ends = [], []
        for loop, value in zip(mesh.loops, values):
            z = mesh.vertices[loop.vertex_index].co.z
            (starts if value.vector.x < .001 else ends if value.vector.x > .999 else []).append(z)
        # Both disjoint splines independently span 0..1, even when their point
        # order is opposite. Object rotation and nonuniform scale do not reset it.
        assert min(starts) < .1 and max(starts) > 1.9
        assert min(ends) < .1 and max(ends) > 1.9
    finally:
        ev.to_mesh_clear()


def test_v8_geometry_and_v3_wave_patch_migrate_preserving_keys_and_authored_emission():
    obj = curve()
    mat = obj.data.materials[0]
    original = helpers.snapshot(mat)
    scene, mod = setup(obj)
    nodes.set_input(mod, 'Intensity', 2.3)
    socket = nodes._socket(mod, 'Baked Tap 0')
    socket.value = .8
    socket.keyframe_insert('value', frame=1)
    path = socket.path_from_id('value')
    old = mod.node_group
    old.interface.remove(old.interface.items_tree['Wave Curve'])
    old[nodes.TAG] = 8
    for node in mat.node_tree.nodes:
        if node.get(materials.TAG):
            node[materials.TAG] = 3
    core.sync(scene)
    core.replace(scene)
    assert mod.node_group != old and mod.node_group[nodes.TAG] == nodes.VERSION
    assert abs(nodes.get_input(mod, 'Intensity') - 2.3) < 1e-5
    assert nodes.get_input(mod, 'Wave Curve')
    assert any(fc.data_path == path for fc in helpers.fcurves(obj))
    assert {n[materials.TAG] for n in mat.node_tree.nodes if n.get(materials.TAG)} == {materials.LAYOUT_WAVE}
    materials.unpatch_material(mat)
    assert helpers.snapshot(mat) == original


helpers.run(globals())
