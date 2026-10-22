import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import bake, core, creator, effects, finish, lookbook, nodes, ui


def user_show():
    helpers.visual_scene()
    assert bpy.ops.afterglow.build_show() == {"FINISHED"}
    return bpy.context.scene


def test_complete_show_operator_builds_current_builtin():
    scene = user_show()
    assert set(effects.built_counts(scene)) == {"strikes", "blooms", "gun"}
    assert not effects.outdated(scene) and not bake.outdated(scene)
    assert len([ob for ob in scene.afterglow.collection.all_objects if ob.modifiers]) == 3


def test_library_save_unique_names_remove_and_project_persistence():
    scene = user_show()
    scene.afterglow.new_look_name = "Midnight"
    bpy.ops.afterglow.save_look()
    bpy.ops.afterglow.save_look()
    assert [item.name for item in scene.afterglow.saved_looks] == ["Midnight", "Midnight (2)"]
    bpy.ops.afterglow.remove_saved_look()
    assert len(scene.afterglow.saved_looks) == 1 and scene.afterglow.saved_look_index == 0
    payload = creator.selected_look(scene).payload
    with tempfile.TemporaryDirectory() as folder:
        path = str(Path(folder) / "saved-library.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        scene = bpy.context.scene
        entry = creator.selected_look(scene)
        assert entry.name == "Midnight" and entry.payload == payload
        assert lookbook.loads(entry.payload)["object"] is not None


def test_export_and_import_preserve_saved_snapshot_without_applying():
    scene = user_show()
    bpy.ops.afterglow.save_look()
    saved = lookbook.loads(creator.selected_look(scene).payload)
    scene.afterglow.effects.strikes.color = (0.7, 0.1, 0.05)
    current = tuple(scene.afterglow.effects.strikes.color)
    with tempfile.TemporaryDirectory() as folder:
        path = str(Path(folder) / "Portable Style.json")
        assert bpy.ops.afterglow.export_look(filepath=path) == {"FINISHED"}
        assert lookbook.loads(Path(path).read_text()) == saved
        assert bpy.ops.afterglow.import_look(filepath=path) == {"FINISHED"}
    assert len(scene.afterglow.saved_looks) == 2
    assert creator.selected_look(scene).name == "Portable Style"
    assert tuple(scene.afterglow.effects.strikes.color) == current
    assert bpy.ops.afterglow.build_saved_look() == {"FINISHED"}
    assert tuple(scene.afterglow.effects.strikes.color) != current
    assert not bake.outdated(scene) and not effects.outdated(scene)


def test_bad_import_does_not_add_a_snapshot_or_change_the_scene():
    scene = bpy.context.scene
    before = lookbook.capture(scene)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "bad.json"
        path.write_text('{"not": "an Afterglow look"}')
        try:
            bpy.ops.afterglow.import_look(filepath=str(path))
        except RuntimeError as error:
            assert "Could not import look" in str(error)
        else:
            raise AssertionError("malformed import must fail")
    assert not scene.afterglow.saved_looks and lookbook.capture(scene) == before


def test_glow_operators_are_idempotent_and_reversible():
    scene = bpy.context.scene
    assert bpy.ops.afterglow.add_render_glow() == {"FINISHED"}
    tree = scene.compositing_node_group
    assert finish.enabled(scene)
    bpy.ops.afterglow.add_render_glow()
    assert len([node for node in tree.nodes if node.get(finish.TAG)]) == 1
    assert bpy.ops.afterglow.remove_render_glow() == {"FINISHED"}
    assert not finish.enabled(scene)
    assert not any(node.get(finish.TAG) for node in tree.nodes)


class Layout:
    def __init__(self, log, enabled=True, buttons=None):
        self.log, self.enabled = log, enabled
        self.buttons = buttons if buttons is not None else {}

    def child(self, *a, **k):
        return Layout(self.log, self.enabled, self.buttons)

    row = column = box = child

    def panel(self, *a, **k):
        return self.child(), self.child()

    def label(self, **kwargs):
        self.log.append(("label", kwargs.get("text", "")))

    def prop(self, data, name, **kwargs):
        assert name in data.bl_rna.properties

    def operator(self, name, **kwargs):
        self.log.append((name, self.enabled))
        button = SimpleNamespace()
        self.buttons[name] = button
        return button

    def template_list(self, name, unused, data, prop, active_data, active_prop, **kwargs):
        assert name == "AFTERGLOW_UL_saved_looks"
        assert prop in data.bl_rna.properties and active_prop in active_data.bl_rna.properties
        self.log.append(("library_list", True))


def test_library_actions_and_playback_gating_draw():
    from types import SimpleNamespace
    scene = bpy.context.scene
    creator.store_look(scene, "Stored", lookbook.capture(scene))
    context = SimpleNamespace(scene=scene, screen=SimpleNamespace(is_animation_playing=True))
    log = []
    creator.draw_library(Layout(log), context)
    assert ("library_list", True) in log
    for action in ("save_look", "apply_saved_look", "remove_saved_look", "build_saved_look", "import_look"):
        assert ("afterglow." + action, False) in log, log


def test_register_roundtrip_includes_creator_library_and_operators():
    import afterglow
    afterglow.unregister()
    afterglow.register()
    assert hasattr(bpy.types, "AFTERGLOW_UL_saved_looks")
    assert hasattr(bpy.context.scene.afterglow, "saved_looks")
    assert hasattr(bpy.ops.afterglow, "build_show")


def test_object_saved_look_build_preserves_environment_and_ignores_fx_animation():
    scene = user_show()
    creator.store_look(scene, "Object style", lookbook.capture(scene))
    fx = scene.afterglow.effects
    fx.strikes.weight = 0.23
    fx.strikes.keyframe_insert("weight", frame=1)
    fx.gun.visible_beam = False
    fx.gun.beam_glow = 2.3
    before = lookbook.capture(scene)
    collections = set(effects._collections(scene))
    objects = {obj for collection in collections for obj in collection.objects}
    actions = {obj.name: [(curve.data_path, curve.array_index,
                          tuple(tuple(point.co) for point in curve.keyframe_points))
                         for curve in helpers.fcurves(obj)] for obj in objects}
    assert bpy.ops.afterglow.build_saved_look(scope="OBJECT") == {"FINISHED"}
    after = lookbook.capture(scene)
    assert before["layers"] == after["layers"] and before["effects"] == after["effects"]
    assert set(effects._collections(scene)) == collections
    assert {obj for collection in collections for obj in collection.objects} == objects
    assert actions == {obj.name: [(curve.data_path, curve.array_index,
                                  tuple(tuple(point.co) for point in curve.keyframe_points))
                                 for curve in helpers.fcurves(obj)] for obj in objects}
    assert not bake.outdated(scene)


def test_environment_saved_look_build_preserves_object_style_and_animation():
    scene, collection, objects = helpers.visual_scene()
    core.apply(scene)
    scene.afterglow.effects.strikes.enabled = True
    creator.store_look(scene, "Environment style", lookbook.capture(scene))
    mod = core.find_modifier(objects[0])
    nodes.set_input(mod, "Decay", 0.37)
    nodes._socket(mod, "Color A").keyframe_insert("value", frame=1)
    before = lookbook.capture(scene)["object"]
    action = [(curve.data_path, curve.array_index, tuple(tuple(point.co) for point in curve.keyframe_points))
              for curve in helpers.fcurves(objects[0])]
    native = scene.frame_start, scene.frame_end, scene.afterglow.fast_playback
    assert bpy.ops.afterglow.build_saved_look(scope="EFFECTS") == {"FINISHED"}
    assert effects.built_counts(scene)["strikes"] > 0
    assert lookbook.capture(scene)["object"] == before
    assert action == [(curve.data_path, curve.array_index, tuple(tuple(point.co) for point in curve.keyframe_points))
                      for curve in helpers.fcurves(objects[0])]
    assert (scene.frame_start, scene.frame_end, scene.afterglow.fast_playback) == native
    assert all(core.find_modifier(obj) for obj in objects)


def test_environment_saved_look_build_works_without_main_objects_or_materials():
    scene = bpy.context.scene
    bounds = bpy.data.collections.new("Environment bounds")
    scene.collection.children.link(bounds)
    obj = helpers.plane("Ordinary bounds", bounds)
    scene.afterglow.effects.bounds_collection = bounds
    scene.afterglow.sound = helpers.sound()
    data = lookbook.capture(scene)
    data["layers"]["strikes"].update(enabled=True, trigger_band="FULL", weight=1.0)
    creator.store_look(scene, "Environment only", data)
    assert scene.afterglow.collection is None and not obj.material_slots
    native_range = scene.frame_start, scene.frame_end
    assert bpy.ops.afterglow.build_saved_look(scope="EFFECTS") == {"FINISHED"}
    assert effects.built_counts(scene)["strikes"] > 0
    assert scene.afterglow.collection is None and not core.find_modifier(obj)
    assert not obj.material_slots and (scene.frame_start, scene.frame_end) == native_range
    strip = scene.sequence_editor.strips[core.STRIP_NAME]
    assert strip.sound == scene.afterglow.sound and strip.frame_start == scene.afterglow.start_frame
    assert bpy.ops.afterglow.build_saved_look(scope="EFFECTS") == {"FINISHED"}
    assert len(scene.sequence_editor.strips) == 1 and scene.sequence_editor.strips[core.STRIP_NAME] == strip


def test_environment_saved_look_apply_does_not_refresh_object_bakes():
    scene = user_show()
    creator.store_look(scene, "Environment style", lookbook.capture(scene))
    original = bake.bake_all
    def unexpected_bake(scene):
        raise AssertionError("Environment styling must not rebake object reactions")
    bake.bake_all = unexpected_bake
    try:
        assert bpy.ops.afterglow.apply_saved_look(scope="EFFECTS") == {"FINISHED"}
    finally:
        bake.bake_all = original


def test_library_buttons_target_their_own_workflow():
    scene = bpy.context.scene
    creator.store_look(scene, "Stored", lookbook.capture(scene))
    for scope, label in (("OBJECT", "My Object Looks"), ("EFFECTS", "My Environment Looks")):
        log = []
        layout = Layout(log)
        creator.draw_library(layout, bpy.context, scope=scope)
        assert layout.buttons["afterglow.apply_saved_look"].scope == scope
        assert layout.buttons["afterglow.build_saved_look"].scope == scope
        assert any(kind == "label" and text.startswith(label) for kind, text in log)


helpers.run(globals())
