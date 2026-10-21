"""Automatic emission preserves original shading, sharing and later user edits."""
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import core, easy, materials, prepare, workflow


def select(*objects):
    for obj in bpy.context.scene.objects:
        obj.select_set(obj in objects)
    if objects:
        bpy.context.view_layer.objects.active = objects[0]
    return workflow.use_selected(bpy.context)


def plain(name="Plain"):
    material = bpy.data.materials.new(name)
    shader = next(node for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED')
    shader.inputs['Base Color'].default_value = (0.2, 0.4, 0.7, 1)
    return material


def test_materialless_selection_becomes_reactive_and_remove_returns_no_material():
    scene = bpy.context.scene
    obj = helpers.plane('My plain object')
    data = obj.data
    collection = select(obj)
    assert set(collection.objects) == {obj}
    assert materials.has_emission(obj.material_slots[0].material)
    assert obj.data == data
    scene.afterglow.sound = helpers.sound()
    result = easy.create_visual(bpy.context)
    assert result['applied'] == result['baked'] == 1
    core.remove(scene)
    assert not core.find_modifier(obj) and prepare.TAG not in obj
    assert all(slot.material is None for slot in obj.material_slots)
    assert obj.data == data
    assert not any(material.get(prepare.OWNED) for material in bpy.data.materials)


def test_principled_copy_keeps_base_color_texture_and_original_network():
    material = plain()
    tree = material.node_tree
    shader = next(node for node in tree.nodes if node.type == 'BSDF_PRINCIPLED')
    texture = tree.nodes.new('ShaderNodeTexNoise')
    tree.links.new(texture.outputs['Color'], shader.inputs['Base Color'])
    original = helpers.snapshot(material)
    obj = helpers.plane('Textured object', material=material)
    select(obj)
    prepared = obj.material_slots[0].material
    assert prepared != material and helpers.snapshot(material) == original
    shader = next(node for node in prepared.node_tree.nodes if node.type == 'BSDF_PRINCIPLED')
    assert shader.inputs['Base Color'].links[0].from_socket == shader.inputs['Emission Color'].links[0].from_socket
    assert shader.inputs['Emission Strength'].default_value == prepare.STRENGTH
    core.remove(bpy.context.scene)
    assert obj.material_slots[0].link == 'DATA' and obj.material_slots[0].material == material
    assert helpers.snapshot(material) == original


def test_custom_surface_is_retained_beside_added_emission():
    material = bpy.data.materials.new('Custom surface')
    tree = material.node_tree
    tree.nodes.clear()
    diffuse = tree.nodes.new('ShaderNodeBsdfDiffuse')
    output = tree.nodes.new('ShaderNodeOutputMaterial')
    tree.links.new(diffuse.outputs[0], output.inputs['Surface'])
    original = helpers.snapshot(material)
    obj = helpers.plane('Custom object', material=material)
    select(obj)
    prepared = obj.material_slots[0].material
    output = next(node for node in prepared.node_tree.nodes if node.type == 'OUTPUT_MATERIAL')
    add = output.inputs['Surface'].links[0].from_node
    assert add.type == 'ADD_SHADER'
    assert add.inputs[0].links[0].from_node.type == 'BSDF_DIFFUSE'
    assert add.inputs[1].links[0].from_node.type == 'EMISSION'
    assert helpers.snapshot(material) == original


def test_shared_mesh_material_override_does_not_change_unselected_users():
    material = plain()
    obj = helpers.plane('Selected', material=material)
    outside = bpy.data.objects.new('Unselected', obj.data)
    bpy.context.scene.collection.objects.link(outside)
    original = helpers.snapshot(material)
    data = obj.data
    select(obj)
    assert obj.data == outside.data == data
    assert obj.material_slots[0].link == 'OBJECT'
    assert outside.material_slots[0].material == material and data.materials[0] == material
    assert helpers.snapshot(material) == original
    assert obj.material_slots[0].material != material
    assert not workflow.inspect(bpy.context.scene)['shared']
    core.remove(bpy.context.scene)
    assert obj.data == outside.data == data
    assert obj.material_slots[0].material == outside.material_slots[0].material == material


def test_slotless_shared_mesh_is_localized_and_user_geometry_edits_survive_remove():
    obj = helpers.plane('Selected')
    outside = bpy.data.objects.new('Unselected', obj.data)
    bpy.context.scene.collection.objects.link(outside)
    original_data = obj.data
    select(obj)
    assert obj.data != original_data and outside.data == original_data
    assert not outside.material_slots
    prepared_data = obj.data
    obj.data.vertices[0].co.x = -7
    core.remove(bpy.context.scene)
    assert obj.data == prepared_data and obj.data.vertices[0].co.x == -7
    assert original_data.vertices[0].co.x == -1
    assert all(slot.material is None for slot in obj.material_slots)


def test_existing_emission_is_retained_unless_shared_with_an_unselected_object():
    safe = helpers.emission_material('Already emission')
    obj = helpers.plane('Safe', material=safe)
    select(obj)
    assert obj.material_slots[0].material == safe and prepare.TAG not in obj
    outside = helpers.plane('Unselected', material=safe)
    select(obj)
    assert obj.material_slots[0].material != safe
    assert outside.material_slots[0].material == safe
    assert not workflow.inspect(bpy.context.scene)['shared']
    core.remove(bpy.context.scene)
    assert obj.material_slots[0].material == safe


def test_all_slots_are_prepared_and_original_object_link_is_restored():
    data_material, object_material = plain('Data material'), plain('Object material')
    obj = helpers.plane('Many slots', material=data_material)
    obj.data.materials.append(None)
    obj.data.materials.append(data_material)
    obj.material_slots[0].link = 'OBJECT'
    obj.material_slots[0].material = object_material
    obj.data.polygons[0].material_index = 2
    select(obj)
    assert len(obj.material_slots) == 3
    assert all(materials.has_emission(slot.material) for slot in obj.material_slots)
    assert obj.data.polygons[0].material_index == 2
    core.remove(bpy.context.scene)
    assert obj.material_slots[0].link == 'OBJECT' and obj.material_slots[0].material == object_material
    assert obj.material_slots[1].material is None
    assert obj.material_slots[2].material == data_material
    assert obj.data.polygons[0].material_index == 2


def test_curves_and_text_can_be_prepared_without_mesh_conversion():
    curve = bpy.data.curves.new('My curve', 'CURVE')
    curve.dimensions = '3D'
    spline = curve.splines.new('POLY')
    spline.points.add(1)
    spline.points[0].co, spline.points[1].co = (0, 0, 0, 1), (2, 0, 0, 1)
    curve.bevel_depth = 0.1
    curve_obj = bpy.data.objects.new('Curve', curve)
    text = bpy.data.curves.new('My text', 'FONT')
    text.body = 'Music'
    text_obj = bpy.data.objects.new('Text', text)
    for obj in (curve_obj, text_obj):
        bpy.context.scene.collection.objects.link(obj)
    collection = select(curve_obj, text_obj)
    assert set(collection.objects) == {curve_obj, text_obj}
    assert curve_obj.data == curve and text_obj.data == text
    assert all(materials.has_emission(obj.material_slots[0].material) for obj in collection.objects)
    bpy.context.scene.afterglow.sound = helpers.sound()
    assert workflow.create_reaction(bpy.context.scene)['applied'] == 2


def test_repeated_selection_and_build_do_not_duplicate_materials_slots_or_collection():
    obj = helpers.plane('Plain')
    collection = select(obj)
    first = (set(bpy.data.materials), set(bpy.data.collections), set(bpy.data.meshes), len(obj.material_slots))
    assert select(obj) == collection
    assert first == (set(bpy.data.materials), set(bpy.data.collections), set(bpy.data.meshes), len(obj.material_slots))
    bpy.context.scene.afterglow.sound = helpers.sound()
    easy.create_visual(bpy.context)
    ready = (set(bpy.data.materials), set(bpy.data.collections), len(obj.material_slots))
    assert select(obj) == collection
    assert ready == (set(bpy.data.materials), set(bpy.data.collections), len(obj.material_slots))


def test_manual_material_change_and_underlying_data_changes_are_preserved_on_remove():
    first, second = helpers.plane('First', material=plain('Original first')), helpers.plane('Second', material=plain('Original second'))
    select(first, second)
    manual = helpers.emission_material('User replacement')
    first.material_slots[0].material = manual
    second.data.materials[0] = manual
    core.remove(bpy.context.scene)
    assert first.material_slots[0].material == manual
    assert second.material_slots[0].link == 'DATA' and second.material_slots[0].material == manual
    assert prepare.TAG not in first and prepare.TAG not in second


def test_save_and_reopen_retains_original_material_references_for_remove():
    obj = helpers.plane('Portable object', material=plain('Portable original'))
    original = obj.material_slots[0].material
    snapshot = helpers.snapshot(original)
    select(obj)
    bpy.context.scene.afterglow.sound = helpers.sound()
    easy.create_visual(bpy.context)
    assert original.users > 0
    with tempfile.TemporaryDirectory() as folder:
        path = str(Path(folder) / 'automatic-emission.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        obj = bpy.data.objects['Portable object']
        core.remove(bpy.context.scene)
        assert obj.material_slots[0].material == bpy.data.materials['Portable original']
        assert helpers.snapshot(obj.material_slots[0].material) == snapshot
        assert prepare.TAG not in obj and not core.find_modifier(obj)


def test_remove_restores_prepared_selection_after_switching_before_any_build():
    first, second = helpers.plane('First'), helpers.plane('Second')
    select(first)
    select(second)
    core.remove(bpy.context.scene)
    assert all(slot.material is None for obj in (first, second) for slot in obj.material_slots)
    assert all(prepare.TAG not in obj for obj in (first, second))


def test_preparation_failure_rolls_back_new_materials_and_object_assignments():
    first, second = helpers.plane('First'), helpers.plane('Second', material=plain())
    original = second.material_slots[0].material
    before = set(bpy.data.materials), set(bpy.data.collections), set(bpy.data.meshes)
    real = prepare._emission_ready
    calls = []
    def fail_second(material):
        calls.append(material.name)
        if len(calls) == 2:
            raise RuntimeError('Test preparation failure')
        return real(material)
    prepare._emission_ready = fail_second
    try:
        try:
            select(first, second)
        except RuntimeError as error:
            assert 'Test preparation failure' in str(error)
        else:
            raise AssertionError('Expected preparation failure')
    finally:
        prepare._emission_ready = real
    assert before == (set(bpy.data.materials), set(bpy.data.collections), set(bpy.data.meshes))
    assert not first.material_slots and second.material_slots[0].material == original
    assert all(prepare.TAG not in obj for obj in (first, second))


def test_linked_material_is_copied_locally_and_readonly_object_is_skipped():
    original = plain('Library material')
    source = helpers.plane('Library object', material=original)
    with tempfile.TemporaryDirectory() as folder:
        path = str(Path(folder) / 'library.blend')
        bpy.data.libraries.write(path, {source})
        with bpy.data.libraries.load(path, link=True) as (available, linked):
            linked.materials = ['Library material']
            linked.objects = ['Library object']
        linked_material, linked_object = linked.materials[0], linked.objects[0]
        bpy.context.scene.collection.objects.link(linked_object)
        obj = helpers.plane('Local object', material=linked_material)
        collection = select(obj, linked_object)
        assert set(collection.objects) == {obj}
        assert obj.material_slots[0].material.library is None
        assert obj.material_slots[0].material != linked_material
        assert not linked_object.is_editable
        assert linked_object.material_slots[0].material.library is not None


def test_disconnected_emission_node_does_not_hide_an_unlit_surface():
    material = plain('Surface with unused emission')
    material.node_tree.nodes.new('ShaderNodeEmission')
    obj = helpers.plane('Unlit surface', material=material)
    assert materials.has_emission(material)
    select(obj)
    prepared = obj.material_slots[0].material
    assert prepared != material
    shader = next(node for node in prepared.node_tree.nodes if node.type == 'BSDF_PRINCIPLED')
    assert shader.inputs['Emission Strength'].default_value > 0
    assert prepare._has_surface_emission(prepared)


def test_linked_slotless_geometry_is_copied_before_preparation():
    source = helpers.plane('Library geometry')
    with tempfile.TemporaryDirectory() as folder:
        path = str(Path(folder) / 'geometry.blend')
        bpy.data.libraries.write(path, {source.data})
        with bpy.data.libraries.load(path, link=True) as (available, linked):
            linked.meshes = [source.data.name]
        data = linked.meshes[0]
        obj = bpy.data.objects.new('Local object with linked data', data)
        bpy.context.scene.collection.objects.link(obj)
        select(obj)
        assert obj.data != data and obj.data.library is None
        assert not data.materials
        assert materials.has_emission(obj.material_slots[0].material)
        core.remove(bpy.context.scene)
        assert all(slot.material is None for slot in obj.material_slots)
        assert not data.materials


def test_linked_geometry_material_slot_can_use_a_local_object_override():
    source = helpers.plane('Library shaded geometry', material=plain('Library surface'))
    with tempfile.TemporaryDirectory() as folder:
        path = str(Path(folder) / 'shaded-geometry.blend')
        bpy.data.libraries.write(path, {source.data})
        with bpy.data.libraries.load(path, link=True) as (available, linked):
            linked.meshes = [source.data.name]
        data = linked.meshes[0]
        original = data.materials[0]
        snapshot = helpers.snapshot(original)
        obj = bpy.data.objects.new('Local object with shaded linked data', data)
        bpy.context.scene.collection.objects.link(obj)
        assert obj.material_slots[0].link == 'DATA'
        assert obj.material_slots[0].is_property_readonly('material')
        select(obj)
        assert obj.data == data
        assert obj.material_slots[0].link == 'OBJECT'
        assert obj.material_slots[0].material.library is None
        assert data.materials[0] == original and helpers.snapshot(original) == snapshot
        core.remove(bpy.context.scene)
        assert obj.material_slots[0].link == 'DATA'
        assert obj.material_slots[0].material == original
        assert helpers.snapshot(original) == snapshot


helpers.run(globals())
