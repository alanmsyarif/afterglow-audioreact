import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import bake, core, easy, effects, finish, nodes, starter


def render_state(scene):
    return (scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage,
            scene.render.filepath, scene.render.image_settings.media_type,
            scene.render.image_settings.file_format, scene.frame_start, scene.frame_end,
            scene.camera.matrix_world.copy() if scene.camera else None)


def test_empty_scene_becomes_a_complete_ready_visual_in_one_action():
    scene = bpy.context.scene
    result = easy.create_visual(bpy.context)
    assert result['created_stage'] and result['applied'] == 12
    assert result['calibrated'] == result['baked'] == 12
    assert result['effects'] and sum(result['effects'].values()) > 0
    assert scene.afterglow.sound.packed_file
    assert finish.enabled(scene) and scene.afterglow.fast_playback
    assert scene.camera == starter.camera_for(scene.afterglow.collection)
    assert (scene.frame_start, scene.frame_end) == (1, 144)
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


def test_bad_glow_output_and_invalid_render_origin_fail_before_stage_creation():
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
    scene.afterglow.easy_glow = False
    scene.afterglow.start_frame = -100
    try:
        easy.create_visual(bpy.context)
    except ValueError as error:
        assert 'frames' in str(error)
    else:
        raise AssertionError('Unsupported render range must fail')
    assert not scene.objects


def test_export_combines_mp4_format_song_range_and_generated_camera_fit():
    scene = bpy.context.scene
    easy.create_visual(bpy.context)
    scene.afterglow.delivery_format = 'PORTRAIT'
    with tempfile.TemporaryDirectory() as folder:
        scene.afterglow.output_path = folder + '/'
        target = easy.prepare_export(scene)
        assert target == str(Path(folder) / 'afterglow.mp4')
        assert not Path(target).exists()
    assert (scene.render.resolution_x, scene.render.resolution_y) == (1080, 1920)
    assert scene.render.image_settings.media_type == 'VIDEO'
    assert scene.render.image_settings.file_format == 'FFMPEG'
    assert scene.afterglow.output_format == 'PNG_SEQUENCE'
    assert (scene.frame_start, scene.frame_end) == (1, 144)
    assert not bake.outdated(scene) and not effects.outdated(scene)


def test_export_rejects_existing_movie_and_missing_camera_before_setting_changes():
    scene = bpy.context.scene
    easy.create_visual(bpy.context)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'existing.mp4'
        path.write_bytes(b'Existing video')
        scene.afterglow.output_path = str(path)
        before = render_state(scene)
        try:
            easy.prepare_export(scene)
        except ValueError as error:
            assert 'exists' in str(error)
        else:
            raise AssertionError('Existing movie must be protected')
        assert render_state(scene) == before and path.read_bytes() == b'Existing video'
        scene.camera = None
        before = render_state(scene)
        try:
            easy.prepare_export(scene)
        except ValueError as error:
            assert 'camera' in str(error)
        else:
            raise AssertionError('Missing camera must fail')
        assert render_state(scene) == before


def test_export_preserves_custom_camera_and_rejects_stale_visual():
    scene = bpy.context.scene
    easy.create_visual(bpy.context)
    camera = bpy.data.objects.new('Custom shot', bpy.data.cameras.new('Custom shot'))
    scene.collection.objects.link(camera)
    camera.location = (3, -4, 5)
    scene.camera = camera
    bpy.context.view_layer.update()
    matrix = camera.matrix_world.copy()
    with tempfile.TemporaryDirectory() as folder:
        scene.afterglow.output_path = folder + '/'
        easy.prepare_export(scene)
        assert scene.camera == camera and camera.matrix_world == matrix
        obj = next(iter(scene.afterglow.collection.objects))
        nodes.set_input(core.find_modifier(obj), 'Decay', 0.77)
        before = render_state(scene)
        try:
            easy.prepare_export(scene)
        except ValueError as error:
            assert 'Update Visual' in str(error)
        else:
            raise AssertionError('Stale playback must be refreshed first')
        assert render_state(scene) == before


def test_export_refits_camera_directed_effects_without_leaving_a_stale_show():
    scene = bpy.context.scene
    scene.afterglow.creative_look = 'AURORA_FLOW'
    scene.afterglow.effects.cascade_direction = 'TO_CAMERA'
    easy.create_visual(bpy.context)
    scene.afterglow.delivery_format = 'PORTRAIT'
    with tempfile.TemporaryDirectory() as folder:
        scene.afterglow.output_path = folder + '/'
        easy.prepare_export(scene)
    assert not effects.outdated(scene) and not bake.outdated(scene)
    assert 'downward' in effects.built_counts(scene)


def test_export_requires_an_actual_built_visual():
    scene = bpy.context.scene
    try:
        easy.prepare_export(scene)
    except ValueError as error:
        assert 'Create Visual' in str(error)
    else:
        raise AssertionError('Empty scene cannot be exported as a finished visual')


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


def test_export_after_save_as_uses_relative_path_without_a_false_stale_prompt():
    scene = bpy.context.scene
    easy.create_visual(bpy.context)
    with tempfile.TemporaryDirectory() as folder:
        path = str(Path(folder) / 'portable.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        scene = bpy.context.scene
        scene.afterglow.output_path = '//video/'
        target = easy.prepare_export(scene)
        assert Path(target) == Path(folder) / 'video' / 'afterglow.mp4'
        assert not Path(target).exists()
        assert not bake.outdated(scene) and not effects.outdated(scene)


def test_export_rolls_back_format_when_an_animated_stage_camera_cannot_be_fitted():
    scene = bpy.context.scene
    easy.create_visual(bpy.context)
    scene.camera.keyframe_insert(data_path='location', frame=1)
    scene.afterglow.delivery_format = 'PORTRAIT'
    with tempfile.TemporaryDirectory() as folder:
        scene.afterglow.output_path = folder + '/'
        before = render_state(scene)
        try:
            easy.prepare_export(scene)
        except ValueError as error:
            assert 'animation' in str(error)
        else:
            raise AssertionError('An animated stage camera must not be overwritten')
        assert render_state(scene) == before
        assert not Path(folder, 'afterglow.mp4').exists()


def test_repeated_folder_exports_choose_a_new_filename_without_overwriting():
    scene = bpy.context.scene
    easy.create_visual(bpy.context)
    with tempfile.TemporaryDirectory() as folder:
        first = Path(folder) / 'afterglow.mp4'
        second = Path(folder) / 'afterglow-001.mp4'
        first.write_bytes(b'First finished movie')
        second.write_bytes(b'Second finished movie')
        scene.afterglow.output_path = folder + '/'
        target = easy.prepare_export(scene)
        assert Path(target) == Path(folder) / 'afterglow-002.mp4'
        assert first.read_bytes() == b'First finished movie'
        assert second.read_bytes() == b'Second finished movie'
        assert not Path(target).exists()


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


def test_simple_export_rejects_a_hidden_solo_or_mute_without_changing_it():
    scene = bpy.context.scene
    easy.create_visual(bpy.context)
    fx = scene.afterglow.effects
    fx.master_brightness, fx.solo_layer = 0.0, 'strikes'
    with tempfile.TemporaryDirectory() as folder:
        scene.afterglow.output_path = folder + '/'
        before = render_state(scene)
        try:
            easy.prepare_export(scene)
        except ValueError as error:
            assert 'restore full lighting' in str(error)
        else:
            raise AssertionError('A hidden preview mix must not silently become the movie')
        assert render_state(scene) == before
        assert fx.master_brightness == 0.0 and fx.solo_layer == 'strikes'
        scene.afterglow.show_advanced = True
        easy.prepare_export(scene)
        assert fx.master_brightness == 0.0 and fx.solo_layer == 'strikes'


helpers.run(globals())
