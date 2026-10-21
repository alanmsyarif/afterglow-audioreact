import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import bake, creator, effects, finish, lookbook, ui


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
    def __init__(self, log, enabled=True):
        self.log, self.enabled = log, enabled

    def child(self, *a, **k):
        return Layout(self.log, self.enabled)

    row = column = box = child

    def panel(self, *a, **k):
        return self.child(), self.child()

    def label(self, **kwargs):
        pass

    def prop(self, data, name, **kwargs):
        assert name in data.bl_rna.properties

    def operator(self, name, **kwargs):
        self.log.append((name, self.enabled))

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


helpers.run(globals())
