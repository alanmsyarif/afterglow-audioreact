"""The default experience stays focused on choosing a look and creating the visual."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import bake, core, effects, ui


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
    assert not ui.AFTERGLOW_PT_effects.poll(bpy.context)


def test_advanced_opt_in_exposes_manual_tools_and_lighting():
    scene = bpy.context.scene
    scene.afterglow.show_advanced = True
    log = draw(bpy.context)
    tools = {item[1] for item in log if item[0] == "op"}
    assert {"afterglow.calibrate", "afterglow.bake", "afterglow.remove", "afterglow.use_selected"} <= tools
    assert ui.AFTERGLOW_PT_effects.poll(bpy.context)


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
    assert all(obj.get(effects.TAG) for obj in set(scene.objects) - original_objects)
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
    props = set(bpy.context.scene.afterglow.bl_rna.properties.keys())
    assert not props.intersection({'delivery_format', 'output_format', 'output_path',
                                   'output_include_tail', 'preview_offset', 'preview_duration',
                                   'stage_layout', 'easy_source'})


def test_stage_creation_and_preview_operators_are_not_registered():
    for name in ('AFTERGLOW_OT_create_starter', 'AFTERGLOW_OT_preview_stage'):
        assert bpy.types.Operator.bl_rna_get_subclass_py(name) is None


helpers.run(globals())
