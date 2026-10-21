import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import bake, core, easy, effects, finish, starter


def test_empty_scene_becomes_a_complete_ready_visual_in_one_action():
    scene = bpy.context.scene
    original_range = scene.frame_start, scene.frame_end
    result = easy.create_visual(bpy.context)
    assert result['created_stage'] and result['applied'] == 12
    assert result['calibrated'] == result['baked'] == 12
    assert result['effects'] and sum(result['effects'].values()) > 0
    assert scene.afterglow.sound.packed_file
    assert finish.enabled(scene) and scene.afterglow.fast_playback
    assert scene.camera == starter.camera_for(scene.afterglow.collection)
    assert (scene.frame_start, scene.frame_end) == original_range
    assert not bake.outdated(scene) and not effects.outdated(scene)


def test_repeated_creation_reuses_the_stage_and_replaces_only_its_effect_build():
    scene = bpy.context.scene
    easy.create_visual(bpy.context)
    collection = scene.afterglow.collection
    stages = {col.name for col in bpy.data.collections if col.get(starter.TAG)}
    objects = len(scene.objects)
    result = easy.create_visual(bpy.context)
    assert not result['created_stage'] and scene.afterglow.collection == collection
    assert {col.name for col in bpy.data.collections if col.get(starter.TAG)} == stages
    assert len(scene.objects) == objects and len(effects._collections(scene)) == 1
    assert not bake.outdated(scene) and not effects.outdated(scene)


def test_auto_preserves_user_collection_audio_camera_and_unrelated_objects():
    scene = bpy.context.scene
    collection = bpy.data.collections.new('User reaction')
    scene.collection.children.link(collection)
    helpers.plane('User object', collection, helpers.emission_material('User material'))
    untouched = helpers.plane('User floor')
    camera = bpy.data.objects.new('User Camera', bpy.data.cameras.new('User Camera'))
    scene.collection.objects.link(camera)
    camera.location = (9, -7, 6)
    scene.camera = camera
    bpy.context.view_layer.update()
    matrix = camera.matrix_world.copy()
    sound = helpers.sound()
    scene.afterglow.collection, scene.afterglow.sound = collection, sound
    result = easy.create_visual(bpy.context)
    assert not result['created_stage'] and result['applied'] == 1
    assert scene.afterglow.collection == collection and scene.afterglow.sound == sound
    assert scene.camera == camera and camera.matrix_world == matrix
    assert not core.find_modifier(untouched)
    assert not any(col.get(starter.TAG) for col in bpy.data.collections)


def test_explicit_template_switch_is_additive_and_new_stage_selects_its_camera():
    scene = bpy.context.scene
    scene.afterglow.easy_source = 'STAGE'
    scene.afterglow.stage_layout = 'EQUALIZER'
    easy.create_visual(bpy.context)
    original = set(scene.afterglow.collection.objects)
    original_camera = scene.camera
    original_camera_matrix = original_camera.matrix_world.copy()
    scene.afterglow.stage_layout = 'TUNNEL'
    result = easy.create_visual(bpy.context)
    assert result['created_stage'] and result['applied'] == 10
    assert original <= set(scene.objects)
    assert scene.camera != original_camera and original_camera.matrix_world == original_camera_matrix
    assert scene.camera == starter.camera_for(scene.afterglow.collection)
    assert not easy.create_visual(bpy.context)['created_stage']


def test_soft_glow_switch_removes_only_owned_glow():
    scene = bpy.context.scene
    easy.create_visual(bpy.context)
    tree = scene.compositing_node_group
    user_nodes = {node.name for node in tree.nodes if not node.get(finish.TAG)}
    scene.afterglow.easy_glow = False
    easy.create_visual(bpy.context)
    assert not finish.enabled(scene)
    assert {node.name for node in tree.nodes} == user_nodes


def test_bad_audio_or_custom_source_fail_without_creating_scene_data():
    scene = bpy.context.scene
    scene.afterglow.easy_source = 'CUSTOM'
    try:
        easy.create_visual(bpy.context)
    except ValueError as error:
        assert 'collection' in str(error)
    else:
        raise AssertionError('Missing custom collection must fail')
    scene.afterglow.easy_source = 'AUTO'
    sound = helpers.sound()
    sound.filepath = '/tmp/afterglow-easy-audio-does-not-exist.wav'
    scene.afterglow.sound = sound
    scene.afterglow.effects.master_brightness = 0.0
    scene.afterglow.effects.solo_layer = 'strikes'
    before = len(scene.objects), len(bpy.data.collections), scene.compositing_node_group
    try:
        easy.create_visual(bpy.context)
    except ValueError:
        pass
    else:
        raise AssertionError('Unreadable track must fail')
    assert (len(scene.objects), len(bpy.data.collections), scene.compositing_node_group) == before
    assert scene.afterglow.effects.master_brightness == 0.0 and scene.afterglow.effects.solo_layer == 'strikes'


def test_bad_glow_output_fails_before_stage_creation():
    scene = bpy.context.scene
    tree = bpy.data.node_groups.new('User unfinished compositor', 'CompositorNodeTree')
    tree.interface.new_socket(name='Image', in_out='OUTPUT', socket_type='NodeSocketColor')
    tree.nodes.new('NodeGroupOutput')
    scene.compositing_node_group = tree
    try:
        easy.create_visual(bpy.context)
    except ValueError as error:
        assert 'Connect' in str(error)
    else:
        raise AssertionError('Unconnected user graph must not be replaced')
    assert not scene.objects and scene.compositing_node_group == tree

def test_new_stage_selects_its_camera_without_modifying_existing_camera_objects():
    scene = bpy.context.scene
    camera = bpy.data.objects.new('Existing camera', bpy.data.cameras.new('Existing camera'))
    scene.collection.objects.link(camera)
    camera.location = (10, 2, 3)
    scene.camera = camera
    bpy.context.view_layer.update()
    matrix = camera.matrix_world.copy()
    easy.create_visual(bpy.context)
    assert scene.camera == starter.camera_for(scene.afterglow.collection)
    assert scene.camera != camera and camera.matrix_world == matrix
    assert camera.name in scene.objects


def test_simple_creation_restores_full_mix_and_advanced_creation_retains_it():
    scene = bpy.context.scene
    fx = scene.afterglow.effects
    fx.master_brightness, fx.solo_layer = 0.0, 'signals'
    easy.create_visual(bpy.context)
    assert fx.master_brightness == 1.0 and fx.solo_layer == 'ALL'
    scene.afterglow.show_advanced = True
    fx.master_brightness, fx.solo_layer = 0.0, 'strikes'
    easy.create_visual(bpy.context)
    assert fx.master_brightness == 0.0 and fx.solo_layer == 'strikes'
    assert not effects.outdated(scene) and not bake.outdated(scene)


def test_laser_show_works_with_the_simple_creation_action():
    scene = bpy.context.scene
    scene.afterglow.creative_look = 'LASER_SHOW'
    result = easy.create_visual(bpy.context)
    assert not scene.afterglow.show_advanced
    assert result['effects']['lasers'] > 0
    assert set(result['effects']) == {'lasers', 'blooms'}
    graphics = [obj for col in effects._collections(scene) for obj in col.objects
                if obj.type == 'CURVE' and obj.name.startswith('AFX_lasers_')]
    assert graphics and not scene.afterglow.effects.beams
    assert not bake.outdated(scene) and not effects.outdated(scene)
    collection = scene.afterglow.collection
    scene.afterglow.creative_look = 'NEON_CLUB'
    easy.create_visual(bpy.context)
    assert scene.afterglow.collection == collection
    assert not any(obj.name.startswith('AFX_lasers_') for col in effects._collections(scene)
                   for obj in col.objects)


def native_settings(scene):
    render = scene.render
    return (
        tuple(getattr(render, name) for name in (
            'engine', 'filepath', 'resolution_x', 'resolution_y', 'resolution_percentage',
            'pixel_aspect_x', 'pixel_aspect_y', 'fps', 'fps_base', 'film_transparent',
            'use_compositing', 'use_sequencer', 'use_file_extension', 'use_overwrite')),
        tuple(getattr(render.image_settings, name) for name in (
            'media_type', 'file_format', 'color_mode', 'color_depth')),
        tuple(getattr(render.ffmpeg, name) for name in (
            'format', 'codec', 'audio_codec', 'constant_rate_factor', 'ffmpeg_preset')),
        (scene.frame_start, scene.frame_end, scene.use_preview_range,
         scene.frame_preview_start, scene.frame_preview_end),
    )


def test_create_and_update_visual_preserve_native_output_and_timeline_settings():
    scene = bpy.context.scene
    render = scene.render
    render.engine = 'CYCLES'
    render.filepath = '//native-output/frame_'
    render.resolution_x, render.resolution_y, render.resolution_percentage = 1133, 1731, 71
    render.pixel_aspect_x, render.pixel_aspect_y = 1.2, 0.9
    render.fps, render.fps_base = 30, 1.001
    render.film_transparent, render.use_overwrite = True, False
    render.use_sequencer = False
    render.image_settings.file_format = 'OPEN_EXR'
    render.image_settings.color_depth = '32'
    scene.frame_start, scene.frame_end = 7, 22
    scene.use_preview_range = True
    scene.frame_preview_start, scene.frame_preview_end = 9, 19
    before = native_settings(scene)
    for look in ('LASER_SHOW', 'AURORA_FLOW', 'NEON_CLUB'):
        scene.afterglow.creative_look = look
        result = easy.create_visual(bpy.context)
        assert result['effects'] and scene.afterglow.sound.packed_file
        assert native_settings(scene) == before
        assert not bake.outdated(scene) and not effects.outdated(scene)


def test_reopened_visual_keeps_native_settings_and_can_update_without_export_tools():
    scene = bpy.context.scene
    easy.create_visual(bpy.context)
    scene.render.filepath = '//my-existing-render/'
    scene.frame_start, scene.frame_end = 11, 40
    before = native_settings(scene)
    with tempfile.TemporaryDirectory() as folder:
        path = str(Path(folder) / 'portable.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        scene = bpy.context.scene
        assert not bake.outdated(scene) and not effects.outdated(scene)
        easy.create_visual(bpy.context)
        assert native_settings(scene) == before
        assert not bake.outdated(scene) and not effects.outdated(scene)


def test_negative_music_offset_is_independent_of_blenders_render_range():
    scene = bpy.context.scene
    scene.afterglow.start_frame = -10
    before = native_settings(scene)
    result = easy.create_visual(bpy.context)
    assert result['effects'] and scene.frame_current == -10
    assert native_settings(scene) == before
    assert scene.sequence_editor.strips[core.STRIP_NAME].frame_start == -10


def test_disabled_native_compositing_does_not_block_scene_creation():
    scene = bpy.context.scene
    scene.render.use_compositing = False
    tree = bpy.data.node_groups.new('User disabled compositor', 'CompositorNodeTree')
    tree.interface.new_socket(name='Image', in_out='OUTPUT', socket_type='NodeSocketColor')
    output = tree.nodes.new('NodeGroupOutput')
    scene.compositing_node_group = tree
    before = native_settings(scene)
    result = easy.create_visual(bpy.context)
    assert result['effects'] and not result['glow']
    assert native_settings(scene) == before
    assert scene.compositing_node_group == tree and set(tree.nodes) == {output}


def test_disabled_native_compositing_keeps_existing_glow_nodes_during_update():
    scene = bpy.context.scene
    easy.create_visual(bpy.context)
    tree = scene.compositing_node_group
    owned = {node for node in tree.nodes if node.get(finish.TAG)}
    assert owned
    scene.render.use_compositing = False
    before = native_settings(scene)
    result = easy.create_visual(bpy.context)
    assert not result['glow'] and native_settings(scene) == before
    assert scene.compositing_node_group == tree
    assert {node for node in tree.nodes if node.get(finish.TAG)} == owned
    assert finish.enabled(scene)


helpers.run(globals())
