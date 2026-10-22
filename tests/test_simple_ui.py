"""The default experience stays focused on choosing a look and creating the visual."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import bake, core, effects, materials, ui


class Layout:
    def __init__(self, log, enabled=True):
        self.log, self.enabled = log, enabled

    def child(self, *args, **kwargs):
        return Layout(self.log, self.enabled)

    row = column = box = child

    def panel(self, *args, **kwargs):
        return self.child(), self.child()

    def prop(self, data, name, **kwargs):
        assert name in data.bl_rna.properties
        self.log.append(("prop", name))

    def operator(self, name, **kwargs):
        self.log.append(("op", name, self.enabled))
        return SimpleNamespace()

    def label(self, **kwargs):
        pass

    def separator(self, **kwargs):
        pass


def draw(context):
    log = []
    ui.AFTERGLOW_PT_panel.draw(SimpleNamespace(layout=Layout(log)), context)
    return log


def test_default_surface_has_one_creation_action_and_no_tuning_controls():
    scene = bpy.context.scene
    assert not scene.afterglow.show_advanced
    log = draw(bpy.context)
    assert {item[1] for item in log if item[0] == "op"} == {"afterglow.use_selected", "afterglow.load_sound", "afterglow.create_visual"}
    assert {item[1] for item in log if item[0] == "prop"} == {"collection", "sound", "creative_look", "show_advanced"}
    assert not scene.afterglow.environment_advanced
    assert ui.AFTERGLOW_PT_effects.bl_category != ui.AFTERGLOW_PT_panel.bl_category


def test_advanced_opt_in_exposes_manual_tools_and_lighting():
    scene = bpy.context.scene
    scene.afterglow.show_advanced = True
    log = draw(bpy.context)
    tools = {item[1] for item in log if item[0] == "op"}
    assert {"afterglow.calibrate", "afterglow.bake", "afterglow.remove", "afterglow.use_selected"} <= tools
    assert not scene.afterglow.environment_advanced


def test_selected_user_objects_become_a_visual_without_templates_or_camera_changes():
    scene, original_collection, objects = helpers.visual_scene()
    scene.afterglow.collection = None
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    original = bpy.data.objects.new("My camera", bpy.data.cameras.new("My camera"))
    scene.collection.objects.link(original)
    original.location = (10, -8, 7)
    scene.camera = original
    world = bpy.data.worlds.new("My world")
    scene.world = world
    bpy.context.view_layer.update()
    matrix = original.matrix_world.copy()
    original_objects = set(scene.objects)
    audio = scene.afterglow.sound
    assert bpy.ops.afterglow.use_selected() == {"FINISHED"}
    collection = scene.afterglow.collection
    assert set(collection.objects) == set(objects)
    assert set(original_collection.objects) == set(objects)
    assert bpy.ops.afterglow.create_visual() == {"FINISHED"}
    assert set(collection.objects) == set(objects) and scene.afterglow.sound == audio
    assert not audio.packed_file
    assert scene.camera == original and original.matrix_world == matrix and scene.world == world
    assert scene.afterglow.fast_playback and not bake.outdated(scene) and not effects.outdated(scene)
    assert all(core.find_modifier(obj) for obj in objects)
    assert set(scene.objects) == original_objects
    assert not effects._collections(scene)
    total = len(scene.objects)
    assert bpy.ops.afterglow.create_visual() == {"FINISHED"}
    assert scene.afterglow.collection == collection and set(collection.objects) == set(objects)
    assert len(scene.objects) == total
    assert scene.camera == original and original.matrix_world == matrix and scene.world == world


def test_creation_disabled_during_playback_without_preflight_or_parameter_scans():
    context = SimpleNamespace(scene=bpy.context.scene, screen=SimpleNamespace(is_animation_playing=True))
    log = draw(context)
    assert ("op", "afterglow.create_visual", False) in log
    assert ("op", "afterglow.load_sound", False) in log


def test_only_scene_and_lighting_panels_are_registered_without_export_settings():
    panels = {panel.__name__ for panel in bpy.types.Panel.__subclasses__()
              if panel.__name__.startswith('AFTERGLOW_PT_')}
    assert panels == {'AFTERGLOW_PT_panel', 'AFTERGLOW_PT_effects'}
    assert ui.AFTERGLOW_PT_panel.bl_category == 'Afterglow'
    assert ui.AFTERGLOW_PT_effects.bl_category == 'Afterglow FX'
    assert not getattr(ui.AFTERGLOW_PT_effects, 'bl_parent_id', '')
    assert ui.AFTERGLOW_PT_effects.bl_label == 'Afterglow Pro · Environment'
    props = set(bpy.context.scene.afterglow.bl_rna.properties.keys())
    assert not props.intersection({'delivery_format', 'output_format', 'output_path',
                                   'output_include_tail', 'preview_offset', 'preview_duration',
                                   'stage_layout', 'easy_source'})


def test_stage_creation_and_preview_operators_are_not_registered():
    for name in ('AFTERGLOW_OT_create_starter', 'AFTERGLOW_OT_preview_stage'):
        assert bpy.types.Operator.bl_rna_get_subclass_py(name) is None


def test_plain_selected_mesh_is_prepared_created_and_restored_through_sidebar_actions():
    scene = bpy.context.scene
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    assert not any(slot.material for slot in obj.material_slots)
    original_links = set(obj.users_collection)
    original_range = scene.frame_start, scene.frame_end
    assert bpy.ops.afterglow.use_selected() == {"FINISHED"}
    assert all(collection in obj.users_collection for collection in original_links)
    assert any(materials.has_emission(slot.material) for slot in obj.material_slots)
    scene.afterglow.sound = helpers.sound()
    assert bpy.ops.afterglow.create_visual() == {"FINISHED"}
    assert core.find_modifier(obj) and not bake.outdated(scene) and not effects.outdated(scene)
    assert (scene.frame_start, scene.frame_end) == original_range
    assert bpy.ops.afterglow.remove() == {"FINISHED"}
    assert not core.find_modifier(obj)
    assert not any(slot.material for slot in obj.material_slots)
    assert all(collection in obj.users_collection for collection in original_links)


def test_environment_has_its_own_visible_default_controls_and_build_action():
    scene = bpy.context.scene
    assert not scene.afterglow.show_advanced and not scene.afterglow.environment_advanced
    log = []
    ui.AFTERGLOW_PT_effects.draw(SimpleNamespace(layout=Layout(log)), bpy.context)
    props = {entry[1] for entry in log if entry[0] == 'prop'}
    operators = {entry[1] for entry in log if entry[0] == 'op'}
    assert {'bounds_collection', 'sound', 'environment_look', 'environment_advanced'} <= props
    assert 'show_advanced' not in props and 'creative_look' not in props
    assert {'afterglow.use_environment_selected', 'afterglow.build_environment',
            'afterglow.load_sound', 'afterglow.remove_effects'} <= operators
    assert not operators.intersection({'afterglow.use_selected', 'afterglow.create_visual',
                                       'afterglow.create_reaction', 'afterglow.remove'})


def test_environment_sidebar_can_build_from_plain_bounds_without_preparing_object_materials():
    scene = bpy.context.scene
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    original_links = set(obj.users_collection)
    original_range = scene.frame_start, scene.frame_end
    assert bpy.ops.afterglow.use_environment_selected() == {'FINISHED'}
    bounds = scene.afterglow.effects.bounds_collection
    assert bounds and obj.name in bounds.objects
    assert scene.afterglow.collection is None
    assert not any(slot.material for slot in obj.material_slots)
    assert 'afterglow_prepared_materials' not in obj
    scene.afterglow.sound = helpers.sound()
    scene.afterglow.environment_look = 'LASER_SHOW'
    assert bpy.ops.afterglow.build_environment() == {'FINISHED'}
    assert effects.built_counts(scene)['lasers'] > 0
    assert not core.find_modifier(obj) and not any(slot.material for slot in obj.material_slots)
    assert scene.afterglow.collection is None and scene.afterglow.effects.bounds_collection == bounds
    assert original_links <= set(obj.users_collection)
    assert (scene.frame_start, scene.frame_end) == original_range
    assert scene.camera is None and scene.world is None
    assert scene.sequence_editor.strips.get(core.STRIP_NAME)


def test_removing_main_reaction_restores_materials_and_keeps_environment_and_music():
    scene = bpy.context.scene
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    assert bpy.ops.afterglow.use_selected() == {'FINISHED'}
    scene.afterglow.sound = helpers.sound()
    scene.afterglow.effects.bounds_collection = scene.afterglow.collection
    scene.afterglow.environment_look = 'LASER_SHOW'
    assert bpy.ops.afterglow.build_environment() == {'FINISHED'}
    generated = set(effects._collections(scene)[0].all_objects)
    counts = effects.built_counts(scene)
    assert not core.find_modifier(obj)
    assert bpy.ops.afterglow.create_visual() == {'FINISHED'}
    assert core.find_modifier(obj) and not bake.outdated(scene)
    assert bpy.ops.afterglow.remove() == {'FINISHED'}
    assert not core.find_modifier(obj) and not any(slot.material for slot in obj.material_slots)
    assert set(effects._collections(scene)[0].all_objects) == generated
    assert effects.built_counts(scene) == counts and not effects.outdated(scene)
    assert scene.sequence_editor.strips.get(core.STRIP_NAME)
    assert bpy.ops.afterglow.remove_effects() == {'FINISHED'}
    assert not effects._collections(scene) and not core.find_modifier(obj)


helpers.run(globals())
