"""New visible names, with legacy project ownership and animation preserved."""
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import bake, core, effect_schema, finish, lookbook, materials, nodes, prepare, workflow


def stage():
    scene, collection, objects = helpers.visual_scene()
    core.apply(scene)
    return scene, collection, objects


def test_new_generated_data_uses_noctune_names():
    scene, collection, objects = stage()
    modifier = core.find_modifier(objects[0])
    assert modifier.name == "Noctune" and modifier.node_group.name == "Noctune AudioReact"
    assert core.sound_strip(scene).name == "Noctune Audio"
    assert materials.level_group().name == "Noctune Level"
    assert materials.wave_group().name == "Noctune Wave"
    assert all(node.label == "Noctune" for node in objects[0].active_material.node_tree.nodes
               if node.get(materials.TAG))
    plain = helpers.plane("Plain")
    prepare.selected([plain])
    assert plain.active_material.name.startswith("Noctune ")
    glow = finish.enable(scene)
    assert glow.name == "Noctune Render Glow" and glow.label == "Noctune • Render Glow"
    assert scene.compositing_node_group.name.startswith("Noctune • ")


def test_legacy_named_modifiers_and_shader_groups_are_reused():
    scene, collection, objects = stage()
    modifier = core.find_modifier(objects[0])
    modifier.name = "Afterglow"
    group = modifier.node_group
    group.name = "Afterglow AudioReact"
    level, wave = materials.level_group(), materials.wave_group()
    level.name, wave.name = "Afterglow Level", "My customized wave"
    labels = [node for node in objects[0].active_material.node_tree.nodes if node.get(materials.TAG)]
    for node in labels:
        node.label = "Afterglow"
    nodes.set_input(modifier, "Glow", 0.33)
    before = len(bpy.data.node_groups), len(objects[0].modifiers)
    core.apply(scene)
    assert core.find_modifier(objects[0]) == modifier and modifier.name == "Afterglow"
    assert nodes.get_group() == group and group.name == "Afterglow AudioReact"
    assert materials.level_group() == level and materials.wave_group() == wave
    assert wave.name == "My customized wave" and all(node.label == "Afterglow" for node in labels)
    assert abs(nodes.get_input(modifier, "Glow") - 0.33) < 1e-6
    assert before == (len(bpy.data.node_groups), len(objects[0].modifiers))


def test_legacy_audio_sync_and_cleanup_preserve_unrelated_music():
    scene, collection, objects = stage()
    strip = core.sound_strip(scene)
    strip.name = "Afterglow Audio"
    del strip[core.AUDIO_TAG]  # Earlier versions had no ownership tag.
    unrelated = scene.sequence_editor.strips.new_sound("My separate music", helpers.tone_wav(),
                                                       channel=2, frame_start=7)
    assert core.sound_strip(scene) == strip
    scene.afterglow.start_frame = 29  # Property callback uses the legacy lookup.
    assert core.sound_strip(scene) == strip and strip.frame_start == 29
    assert strip.name == "Afterglow Audio" and strip.get(core.AUDIO_TAG)
    assert len(scene.sequence_editor.strips) == 2
    core.apply(scene)
    assert len(scene.sequence_editor.strips) == 2
    core.remove(scene)
    assert core.sound_strip(scene) is None
    assert list(scene.sequence_editor.strips) == [unrelated]


def test_owned_audio_survives_name_collisions_and_manual_rename():
    scene = bpy.context.scene
    scene.afterglow.sound = helpers.sound()
    editor = scene.sequence_editor_create()
    color = editor.strips.new_effect("Noctune Audio", type="COLOR", channel=1,
                                     frame_start=1, length=48)
    core.setup_playback(scene)
    strip = core.sound_strip(scene)
    assert strip.name != core.STRIP_NAME and strip.get(core.AUDIO_TAG)
    core.setup_playback(scene)
    assert core.sound_strip(scene) == strip and len(editor.strips) == 2
    strip.name = "My song timing"
    scene.afterglow.start_frame = 17
    assert core.sound_strip(scene) == strip and strip.frame_start == 17
    assert strip.name == "My song timing" and len(editor.strips) == 2
    core.remove(scene)
    assert list(editor.strips) == [color]


def test_legacy_audio_is_kept_while_environment_owns_it():
    scene, collection, objects = stage()
    strip = core.sound_strip(scene)
    strip.name = "Afterglow Audio"
    del strip[core.AUDIO_TAG]
    environment = bpy.data.collections.new("Afterglow Effects")
    environment[effect_schema.TAG] = 1
    scene.collection.children.link(environment)
    core.remove(scene)
    assert core.sound_strip(scene) == strip and strip.name == "Afterglow Audio"
    assert not any(core.find_modifier(obj) for obj in objects)


def test_legacy_compositor_glow_is_reused_without_renaming_user_data():
    scene = bpy.context.scene
    node = finish.enable(scene)
    tree = scene.compositing_node_group
    node.name, node.label = "Afterglow Render Glow", "My glow"
    tree.name = "Afterglow • Scene Compositor"
    assert finish.enable(scene) == node
    assert node.name == "Afterglow Render Glow" and node.label == "My glow"
    assert scene.compositing_node_group == tree and tree.name == "Afterglow • Scene Compositor"
    assert finish.disable(scene) and len(tree.nodes) == 2


def test_legacy_project_reopens_with_bakes_looks_and_names_intact():
    scene, collection, objects = helpers.visual_scene()
    workflow.create_reaction(scene, "NEON_CLUB", style_scope="OBJECT")
    modifier = core.find_modifier(objects[0])
    modifier.name, modifier.node_group.name = "Afterglow", "Afterglow AudioReact"
    strip = core.sound_strip(scene)
    strip.name = "Afterglow Audio"
    del strip[core.AUDIO_TAG]
    snapshot = lookbook.dumps(scene)
    assert lookbook.loads(snapshot)["format"] == "afterglow.object-look"
    assert not bake.outdated(scene)
    with tempfile.TemporaryDirectory() as directory:
        project = Path(directory) / "legacy.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(project))
        bpy.ops.wm.open_mainfile(filepath=str(project))
        scene = bpy.context.scene
        assert not bake.outdated(scene) and lookbook.dumps(scene) == snapshot
        assert nodes.get_group().name == "Afterglow AudioReact"
        assert core.sound_strip(scene).name == "Afterglow Audio"
        scene.afterglow.start_frame = 13
        core.sync(scene)
        assert core.sound_strip(scene).frame_start == 13
        assert len(scene.sequence_editor.strips) == 1
        core.remove(scene)
        assert core.sound_strip(scene) is None


helpers.run(globals())
