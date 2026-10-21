"""The default experience stays focused on choosing a look and creating the visual."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import bake, effects, starter, ui


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
    assert {item[1] for item in log if item[0] == "op"} == {"afterglow.load_sound", "afterglow.create_visual"}
    assert {item[1] for item in log if item[0] == "prop"} == {"stage_layout", "sound", "creative_look", "show_advanced"}
    assert not ui.AFTERGLOW_PT_effects.poll(bpy.context)


def test_advanced_opt_in_exposes_manual_tools_and_lighting():
    scene = bpy.context.scene
    scene.afterglow.show_advanced = True
    log = draw(bpy.context)
    tools = {item[1] for item in log if item[0] == "op"}
    assert {"afterglow.calibrate", "afterglow.bake", "afterglow.remove", "afterglow.use_selected"} <= tools
    assert ui.AFTERGLOW_PT_effects.poll(bpy.context)


def test_default_creation_is_ready_without_manual_stage_calibrate_bake_or_glow_actions():
    scene = bpy.context.scene
    original = bpy.data.objects.new("My camera", bpy.data.cameras.new("My camera"))
    scene.collection.objects.link(original)
    scene.camera = original
    assert bpy.ops.afterglow.create_visual() == {"FINISHED"}
    collection = scene.afterglow.collection
    assert len(collection.objects) == 12 and scene.afterglow.sound.packed_file
    assert scene.camera == starter.camera_for(collection) and original.name in scene.objects
    assert scene.afterglow.fast_playback and not bake.outdated(scene) and not effects.outdated(scene)
    starter_count = len([obj for obj in scene.objects if obj.get(starter.TAG)])
    assert bpy.ops.afterglow.create_visual() == {"FINISHED"}
    assert scene.afterglow.collection == collection
    assert len([obj for obj in scene.objects if obj.get(starter.TAG)]) == starter_count


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
                                   'output_include_tail', 'preview_offset', 'preview_duration'})


helpers.run(globals())
