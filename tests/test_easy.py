import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import bake, core, easy, effects, finish, presets


def unchanged_scene_data(scene):
    return (set(scene.objects), set(bpy.data.collections), set(bpy.data.sounds),
            set(bpy.data.cameras), scene.world, scene.camera, scene.compositing_node_group)


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


def test_empty_scene_requires_user_objects_without_creating_any_scene_data():
    scene = bpy.context.scene
    before = unchanged_scene_data(scene)
    try:
        easy.create_visual(bpy.context)
    except ValueError as error:
        assert 'collection' in str(error)
    else:
        raise AssertionError('An empty setup must ask for user objects')
    assert unchanged_scene_data(scene) == before
    assert scene.afterglow.collection is None and scene.afterglow.sound is None


def test_missing_or_unreadable_music_fails_before_mutating_objects_or_live_mix():
    scene, collection, objects = helpers.visual_scene()
    scene.afterglow.sound = None
    fx = scene.afterglow.effects
    fx.master_brightness, fx.solo_layer = 0.0, 'strikes'
    for sound in (None, helpers.sound()):
        if sound:
            sound.filepath = '/tmp/afterglow-easy-audio-does-not-exist.wav'
        scene.afterglow.sound = sound
        before = unchanged_scene_data(scene)
        try:
            easy.create_visual(bpy.context)
        except ValueError:
            pass
        else:
            raise AssertionError('A user-provided readable track is required')
        assert unchanged_scene_data(scene) == before
        assert not any(core.find_modifier(obj) for obj in objects)
        assert fx.master_brightness == 0.0 and fx.solo_layer == 'strikes'


def test_user_collection_becomes_ready_without_source_geometry_sound_or_camera_generation():
    scene, collection, objects = helpers.visual_scene()
    original_range = scene.frame_start, scene.frame_end
    source_meshes = {obj.data for obj in objects}
    sounds = set(bpy.data.sounds)
    vertices = {obj: tuple(tuple(vertex.co) for vertex in obj.data.vertices) for obj in objects}
    result = easy.create_visual(bpy.context)
    assert result['applied'] == result['calibrated'] == result['baked'] == 3
    assert result['effects'] and sum(result['effects'].values()) > 0
    assert set(collection.objects) == set(objects)
    assert {obj.data for obj in objects} == source_meshes
    assert all(vertices[obj] == tuple(tuple(vertex.co) for vertex in obj.data.vertices) for obj in objects)
    assert set(bpy.data.sounds) == sounds
    assert not bpy.data.cameras and scene.camera is None and scene.world is None
    assert finish.enabled(scene) and scene.afterglow.fast_playback
    assert (scene.frame_start, scene.frame_end) == original_range
    assert not bake.outdated(scene) and not effects.outdated(scene)


def test_repeated_creation_reuses_user_objects_and_replaces_only_owned_effects():
    scene, collection, objects = helpers.visual_scene()
    easy.create_visual(bpy.context)
    count = len(scene.objects)
    source_links = {obj: set(obj.users_collection) for obj in objects}
    easy.create_visual(bpy.context)
    assert scene.afterglow.collection == collection
    assert set(collection.objects) == set(objects) and len(scene.objects) == count
    assert all(set(obj.users_collection) == source_links[obj] for obj in objects)
    assert len(effects._collections(scene)) == 1
    assert not bpy.data.cameras
    assert not bake.outdated(scene) and not effects.outdated(scene)


def test_existing_camera_world_audio_and_unrelated_objects_remain_unchanged():
    scene, collection, objects = helpers.visual_scene()
    untouched = helpers.plane('User floor', material=helpers.emission_material('User floor material'))
    outside_material = helpers.snapshot(untouched.data.materials[0])
    camera = bpy.data.objects.new('User Camera', bpy.data.cameras.new('User Camera'))
    scene.collection.objects.link(camera)
    camera.location = (9, -7, 6)
    scene.camera = camera
    world = bpy.data.worlds.new('User world')
    scene.world = world
    bpy.context.view_layer.update()
    matrix = camera.matrix_world.copy()
    sound = scene.afterglow.sound
    easy.create_visual(bpy.context)
    assert scene.afterglow.collection == collection and scene.afterglow.sound == sound
    assert scene.camera == camera and camera.matrix_world == matrix
    assert scene.world == world and not sound.packed_file
    assert not core.find_modifier(untouched)
    assert helpers.snapshot(untouched.data.materials[0]) == outside_material


def test_old_stage_tags_are_ordinary_user_data_and_never_trigger_camera_changes():
    scene, collection, objects = helpers.visual_scene()
    collection['afterglow_starter'] = 1
    collection['afterglow_layout'] = 'TUNNEL'
    scene.afterglow['easy_source'] = 'STAGE'
    scene.afterglow['stage_layout'] = 'EQUALIZER'
    old_camera = bpy.data.objects.new('Old stage camera', bpy.data.cameras.new('Old stage camera'))
    old_camera['afterglow_starter'] = 1
    collection.objects.link(old_camera)
    active_camera = bpy.data.objects.new('My shot', bpy.data.cameras.new('My shot'))
    scene.collection.objects.link(active_camera)
    active_camera.location = (3, -9, 7)
    scene.camera = active_camera
    bpy.context.view_layer.update()
    matrices = {camera: camera.matrix_world.copy() for camera in (old_camera, active_camera)}
    easy.create_visual(bpy.context)
    assert scene.camera == active_camera and len(bpy.data.cameras) == 2
    assert all(camera.matrix_world == matrix for camera, matrix in matrices.items())
    assert set(collection.objects) == set(objects) | {old_camera}
    assert collection['afterglow_layout'] == 'TUNNEL'


def test_soft_glow_switch_removes_only_owned_glow():
    scene, _, _ = helpers.visual_scene()
    easy.create_visual(bpy.context)
    tree = scene.compositing_node_group
    user_nodes = {node.name for node in tree.nodes if not node.get(finish.TAG)}
    scene.afterglow.easy_glow = False
    easy.create_visual(bpy.context)
    assert not finish.enabled(scene)
    assert {node.name for node in tree.nodes} == user_nodes


def test_bad_glow_output_fails_before_reaction_or_lighting_changes():
    scene, _, objects = helpers.visual_scene()
    tree = bpy.data.node_groups.new('User unfinished compositor', 'CompositorNodeTree')
    tree.interface.new_socket(name='Image', in_out='OUTPUT', socket_type='NodeSocketColor')
    tree.nodes.new('NodeGroupOutput')
    scene.compositing_node_group = tree
    before = unchanged_scene_data(scene)
    try:
        easy.create_visual(bpy.context)
    except ValueError as error:
        assert 'Connect' in str(error)
    else:
        raise AssertionError('Unconnected user graph must not be replaced')
    assert unchanged_scene_data(scene) == before
    assert not any(core.find_modifier(obj) for obj in objects)


def test_simple_creation_restores_full_mix_and_advanced_creation_retains_it():
    scene, _, _ = helpers.visual_scene()
    fx = scene.afterglow.effects
    fx.master_brightness, fx.solo_layer = 0.0, 'signals'
    easy.create_visual(bpy.context)
    assert fx.master_brightness == 1.0 and fx.solo_layer == 'ALL'
    scene.afterglow.show_advanced = True
    fx.master_brightness, fx.solo_layer = 0.0, 'strikes'
    easy.create_visual(bpy.context)
    assert fx.master_brightness == 0.0 and fx.solo_layer == 'strikes'
    assert not effects.outdated(scene) and not bake.outdated(scene)


def test_all_curated_looks_and_lasers_work_on_user_objects_without_a_camera():
    scene, collection, objects = helpers.visual_scene()
    for identifier, _, _ in presets.ITEMS:
        scene.afterglow.creative_look = identifier
        result = easy.create_visual(bpy.context)
        assert result['applied'] == 3 and result['effects']
        assert set(collection.objects) == set(objects) and not bpy.data.cameras
        assert not bake.outdated(scene) and not effects.outdated(scene)
        graphics = [obj for col in effects._collections(scene) for obj in col.objects
                    if obj.type == 'CURVE' and obj.name.startswith('AFX_lasers_')]
        if identifier == 'LASER_SHOW':
            assert result['effects']['lasers'] > 0 and graphics
            assert not scene.afterglow.effects.beams
        else:
            assert not graphics


def test_create_and_update_visual_preserve_native_output_and_timeline_settings():
    scene, _, _ = helpers.visual_scene()
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
        easy.create_visual(bpy.context)
        assert native_settings(scene) == before
        assert not bake.outdated(scene) and not effects.outdated(scene)


def test_reopened_visual_keeps_user_audio_and_native_settings():
    scene, _, _ = helpers.visual_scene()
    scene.afterglow.sound.pack()
    easy.create_visual(bpy.context)
    scene.render.filepath = '//my-existing-render/'
    scene.frame_start, scene.frame_end = 11, 40
    before = native_settings(scene)
    with tempfile.TemporaryDirectory() as folder:
        path = str(Path(folder) / 'portable.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        scene = bpy.context.scene
        assert scene.afterglow.sound.packed_file
        assert not bake.outdated(scene) and not effects.outdated(scene)
        easy.create_visual(bpy.context)
        assert native_settings(scene) == before and not bpy.data.cameras
        assert not bake.outdated(scene) and not effects.outdated(scene)


def test_negative_music_offset_is_independent_of_blenders_render_range():
    scene, _, _ = helpers.visual_scene()
    scene.afterglow.start_frame = -10
    before = native_settings(scene)
    result = easy.create_visual(bpy.context)
    assert result['effects'] and scene.frame_current == -10
    assert native_settings(scene) == before
    assert scene.sequence_editor.strips[core.STRIP_NAME].frame_start == -10


def test_disabled_native_compositing_does_not_block_scene_creation():
    scene, _, _ = helpers.visual_scene()
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
    scene, _, _ = helpers.visual_scene()
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
