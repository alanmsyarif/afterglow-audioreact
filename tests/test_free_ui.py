"""Free exposes the complete object workflow without environment controls."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import bake, core, creator, edition, lookbook, materials, nodes, ui, workflow


class Layout:
    def __init__(self, log, enabled=True, opened=()):
        self.log, self.enabled, self.opened = log, enabled, opened

    def child(self, *args, **kwargs):
        return Layout(self.log, self.enabled, self.opened)

    row = column = box = grid_flow = split = child

    def panel(self, name, default_closed=False):
        self.log.append(("panel", name, default_closed))
        opened = self.opened is None or name in self.opened or not default_closed
        return self.child(), self.child() if opened else None

    def prop(self, data, name, **kwargs):
        assert name in data.bl_rna.properties
        self.log.append(("prop", name))

    def operator(self, name, **kwargs):
        operator = SimpleNamespace()
        self.log.append(("op", name, self.enabled, operator))
        return operator

    def label(self, **kwargs):
        self.log.append(("label", kwargs.get("text", "")))

    def template_list(self, *args, **kwargs):
        self.log.append(("list", args[0]))

    def template_icon_view(self, data, name, **kwargs):
        self.prop(data, name)

    def template_icon(self, *args, **kwargs):
        pass

    def separator(self, **kwargs):
        pass


def draw(context, opened=()):
    log = []
    ui.AFTERGLOW_PT_panel.draw(SimpleNamespace(layout=Layout(log, opened=opened)), context)
    return log


def test_free_registers_object_tools_without_paid_or_export_controls():
    assert not edition.IS_PRO and edition.NAME == "Noctune Free"
    assert ui.AFTERGLOW_PT_panel.bl_label == "Noctune Free"
    assert ui.AFTERGLOW_PT_panel.bl_category == "Noctune"
    assert bpy.types.Panel.bl_rna_get_subclass_py("AFTERGLOW_PT_panel") is not None
    assert bpy.types.Panel.bl_rna_get_subclass_py("AFTERGLOW_PT_effects") is None
    properties = set(bpy.context.scene.afterglow.bl_rna.properties.keys())
    assert {"collection", "sound", "show_advanced", "saved_looks"} <= properties
    assert not properties.intersection({"effects", "environment_look", "environment_advanced",
                                        "output_format", "output_path", "stage_layout"})
    for name in ("AFTERGLOW_OT_build_environment", "AFTERGLOW_OT_build_effects",
                 "AFTERGLOW_OT_remove_effects", "AFTERGLOW_OT_build_show",
                 "AFTERGLOW_OT_create_starter", "AFTERGLOW_OT_preview_stage"):
        assert bpy.types.Operator.bl_rna_get_subclass_py(name) is None


def test_default_sidebar_has_one_creation_action_and_no_parameter_tuning():
    assert not bpy.context.scene.afterglow.show_advanced
    log = draw(bpy.context)
    assert {item[1] for item in log if item[0] == "op"} == {
        "afterglow.use_selected", "afterglow.load_sound", "afterglow.create_visual"}
    props = {item[1] for item in log if item[0] == "prop"}
    assert {"collection", "sound", "start_frame", "object_page"} <= props
    assert not {"creative_look", "bounds_collection", "environment_look"} & props
    assert not any("Noctune FX" in item[1] for item in log if item[0] == "label")


def test_style_tools_and_saved_looks_have_separate_pages():
    state = bpy.context.window_manager.afterglow_ui
    state.object_page = 'SETUP'
    setup = draw(bpy.context, opened=None)
    tools = {item[1] for item in setup if item[0] == 'op'}
    assert {'afterglow.calibrate','afterglow.bake','afterglow.remove'} <= tools
    helpers.visual_scene()
    bpy.ops.afterglow.create_visual()
    state.object_page = 'STYLE'
    style = draw(bpy.context, opened=None)
    style_tools = {item[1] for item in style if item[0] == 'op'}
    assert 'afterglow.add_render_glow' not in style_tools
    assert {'ripple','through'} <= {item[1] for item in style if item[0] == 'prop'}
    state.object_page = 'PRESETS'
    library = draw(bpy.context, opened=None)
    library_tools = {item[1] for item in library if item[0] == 'op'}
    assert {'afterglow.save_look','afterglow.import_look','afterglow.export_look'} <= library_tools
    assert not library_tools.intersection({'afterglow.build_environment','afterglow.build_effects','afterglow.build_show'})

def test_playback_disables_creation_and_music_loading():
    context = SimpleNamespace(scene=bpy.context.scene,
                              screen=SimpleNamespace(is_animation_playing=True))
    log = draw(context)
    for name in ("afterglow.create_visual", "afterglow.load_sound"):
        assert any(item[:3] == ("op", name, False) for item in log)


def test_sidebar_prepares_builds_and_restores_plain_objects_without_scene_templates():
    scene = bpy.context.scene
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    original_links = set(obj.users_collection)
    original_objects = set(scene.objects)
    scene.frame_start, scene.frame_end = 9, 41
    scene.render.filepath = "//my-output/"
    before = scene.camera, scene.world, scene.frame_start, scene.render.filepath
    assert bpy.ops.afterglow.use_selected() == {"FINISHED"}
    assert any(materials.has_emission(slot.material) for slot in obj.material_slots)
    scene.afterglow.sound = helpers.sound()
    assert bpy.ops.afterglow.create_visual() == {"FINISHED"}
    assert scene.frame_end == 72
    assert core.find_modifier(obj) and not bake.outdated(scene)
    assert set(scene.objects) == original_objects
    assert before == (scene.camera, scene.world, scene.frame_start,
                      scene.render.filepath)
    assert bpy.ops.afterglow.remove() == {"FINISHED"}
    assert not core.find_modifier(obj) and not any(slot.material for slot in obj.material_slots)
    assert original_links <= set(obj.users_collection)


def test_saved_look_actions_use_object_scope_and_restore_the_shared_style():
    scene, collection, objects = helpers.visual_scene()
    workflow.create_reaction(scene, "NEON_CLUB")
    scene.afterglow.new_look_name = "My object look"
    assert bpy.ops.afterglow.save_look() == {"FINISHED"}
    snapshot = lookbook.loads(creator.selected_look(scene).payload)
    assert snapshot["format"] == lookbook.OBJECT_FORMAT
    expected = snapshot["object"]["Glow"]
    nodes.set_input(core.find_modifier(objects[0]), "Glow", 0.8)
    assert bpy.ops.afterglow.apply_saved_look() == {"FINISHED"}
    assert all(abs(nodes.get_input(core.find_modifier(obj), "Glow") - expected) < 1e-6
               for obj in objects)
    assert not bake.outdated(scene)
    bpy.context.window_manager.afterglow_ui.object_page = "PRESETS"
    log = draw(bpy.context, opened=None)
    actions = [item for item in log if item[0] == "op" and item[1] in {
        "afterglow.apply_saved_look", "afterglow.build_saved_look"}]
    assert len(actions) == 2 and all(item[3].scope == "OBJECT" for item in actions)
    assert ("list", "AFTERGLOW_UL_saved_looks") in log


helpers.run(globals())
