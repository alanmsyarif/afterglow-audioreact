"""Environment generation is independent of object reactions and material preparation."""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import bake, core, effects, environment, lookbook, nodes, presets, workflow


def venue():
    scene = bpy.context.scene
    bounds = bpy.data.collections.new('My non-emissive bounds')
    scene.collection.children.link(bounds)
    objects = (helpers.plane('left bounds', bounds), helpers.plane('right bounds', bounds))
    objects[0].location.x = -3
    objects[1].location.x = 3
    scene.afterglow.effects.bounds_collection = bounds
    scene.afterglow.sound = helpers.sound()
    bpy.context.view_layer.update()
    return scene, bounds, objects


def native(scene):
    r = scene.render
    return (scene.camera, scene.world, r.engine, r.filepath, r.resolution_x, r.resolution_y,
            r.resolution_percentage, r.fps, r.fps_base, scene.frame_start, scene.frame_end,
            scene.frame_current, scene.use_preview_range, scene.frame_preview_start,
            scene.frame_preview_end, r.image_settings.file_format)


def source(objects):
    return [(obj, obj.data, tuple(tuple(row) for row in obj.matrix_world),
             tuple((slot.link, slot.material) for slot in obj.material_slots),
             tuple((mod.name, mod.type) for mod in obj.modifiers),
             tuple((fc.data_path, fc.array_index, tuple(tuple(kp.co) for kp in fc.keyframe_points))
                   for fc in helpers.fcurves(obj))) for obj in objects]


def test_environment_alone_uses_non_emissive_bounds_without_object_reactions():
    scene, bounds, objects = venue()
    scene.frame_start, scene.frame_end = 13, 44
    scene.render.filepath = '//user-output/'
    scene.render.resolution_x, scene.render.resolution_y = 913, 1519
    before_native, before_source = native(scene), source(objects)
    assert scene.afterglow.collection is None and not scene.sequence_editor.strips
    counts = environment.build(scene, 'LASER_SHOW')
    assert counts['lasers'] > 0 and set(counts) == {'lasers', 'blooms'}
    assert source(objects) == before_source and native(scene) == before_native
    assert scene.afterglow.collection is None and not scene.afterglow.fast_playback
    assert all(not obj.material_slots and not core.find_modifier(obj) for obj in objects)
    assert not any(obj.animation_data for obj in objects)
    assert len(scene.sequence_editor.strips) == 1 and scene.sync_mode == 'AUDIO_SYNC'
    assert scene.sequence_editor.strips[core.STRIP_NAME].sound == scene.afterglow.sound
    assert not effects.outdated(scene)


def test_main_object_reactions_and_animated_styling_stay_unchanged():
    scene, collection, objects = helpers.visual_scene()
    workflow.create_reaction(scene, 'NEON_CLUB', style_scope='OBJECT')
    mod = core.find_modifier(objects[0])
    nodes._socket(mod, 'Color A').keyframe_insert('value', frame=1)
    before = source(objects)
    style = lookbook.capture(scene)['object']
    material_trees = {mat: helpers.snapshot(mat) for obj in objects for mat in core._materials(obj)}
    environment.build(scene, 'LASER_SHOW')
    assert source(objects) == before and lookbook.capture(scene)['object'] == style
    assert {mat: helpers.snapshot(mat) for mat in material_trees} == material_trees
    assert not bake.outdated(scene)
    assert effects.built_counts(scene)['lasers'] > 0


def test_explicit_bounds_and_look_are_independent_from_main_controls():
    scene, bounds, objects = venue()
    environment.build(scene, 'LASER_SHOW')
    assert scene.afterglow.environment_look == 'LASER_SHOW'
    before = effects.settings_sig(scene)
    main = bpy.data.collections.new('Other main objects')
    scene.collection.children.link(main)
    helpers.plane('main object', main).location.x = 50
    scene.afterglow.collection = main
    scene.afterglow.creative_look = 'CINEMATIC_PULSE'
    assert effects.bounds_collection(scene) == bounds
    assert scene.afterglow.environment_look == 'LASER_SHOW'
    assert effects.settings_sig(scene) == before and not effects.outdated(scene)
    scene.afterglow.effects.bounds_collection = main
    assert effects.outdated(scene)


def test_legacy_projects_fall_back_to_main_bounds_and_look_until_explicitly_set():
    scene, main, objects = helpers.visual_scene()
    assert effects.bounds_collection(scene) == main
    scene.afterglow.creative_look = 'AURORA_FLOW'
    assert scene.afterglow.environment_look == 'AURORA_FLOW'
    scene.afterglow.environment_look = 'SIGNAL_GRID'
    scene.afterglow.creative_look = 'NEON_CLUB'
    assert scene.afterglow.environment_look == 'SIGNAL_GRID'
    assert not scene.afterglow.environment_advanced and not scene.afterglow.show_advanced
    scene.afterglow.show_advanced = True
    assert not scene.afterglow.environment_advanced


def test_selected_bounds_do_not_prepare_materials_or_change_main_collection():
    scene, bounds, objects = venue()
    main = bpy.data.collections.new('Main objects')
    scene.collection.children.link(main)
    scene.afterglow.collection = main
    scene.afterglow.effects.bounds_collection = None
    before = source(objects)
    context = SimpleNamespace(scene=scene, selected_objects=list(objects))
    chosen = environment.use_selected(context)
    assert set(chosen.all_objects) == set(objects) and source(objects) == before
    assert scene.afterglow.collection == main
    assert all(obj.name in bounds.objects for obj in objects)
    total = len(bpy.data.collections)
    assert environment.use_selected(context) == chosen and len(bpy.data.collections) == total
    assert not any(obj.material_slots for obj in objects)


def test_missing_audio_bad_routing_or_animated_effects_leave_previous_build_intact():
    scene, bounds, objects = venue()
    environment.build(scene, 'LASER_SHOW')
    previous = set(effects._collections(scene))
    fx = scene.afterglow.effects
    fx.master_brightness, fx.solo_layer = 0.4, 'lasers'
    sound = scene.afterglow.sound
    scene.afterglow.sound = None
    before = lookbook.capture(scene)
    try:
        environment.build(scene, 'NEON_CLUB')
    except ValueError as error:
        assert 'music' in str(error)
    else:
        raise AssertionError('Missing audio must fail before styling')
    assert lookbook.capture(scene) == before and set(effects._collections(scene)) == previous
    scene.afterglow.sound = sound
    fx.strike_placement = 'POINTS'
    before = lookbook.capture(scene)
    try:
        environment.build(scene, 'NEON_CLUB')
    except ValueError as error:
        assert 'strike point' in str(error)
    else:
        raise AssertionError('Future look routing must be checked before styling')
    assert lookbook.capture(scene) == before and set(effects._collections(scene)) == previous
    fx.strike_placement = 'RANDOM'
    fx.gun.keyframe_insert('intensity', frame=1)
    before = lookbook.capture(scene)
    try:
        environment.build(scene, 'NEON_CLUB')
    except ValueError as error:
        assert 'animated controls' in str(error)
    else:
        raise AssertionError('Animated environment controls must remain protected')
    assert lookbook.capture(scene) == before and set(effects._collections(scene)) == previous
    assert abs(fx.master_brightness - 0.4) < 1e-6 and fx.solo_layer == 'lasers'


def test_unlinked_or_generated_bounds_are_rejected_without_replacing_build():
    scene, bounds, objects = venue()
    environment.build(scene, 'NEON_CLUB')
    previous = set(effects._collections(scene))
    detached = bpy.data.collections.new('Detached source')
    helpers.plane('detached object', detached)
    for collection in (detached, next(iter(previous))):
        scene.afterglow.effects.bounds_collection = collection
        try:
            environment.build(scene, 'LASER_SHOW')
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid environment source must fail before replacement')
        assert set(effects._collections(scene)) == previous


def test_emitter_only_environment_needs_no_object_collection():
    scene = bpy.context.scene
    scene.afterglow.sound = helpers.sound()
    cage = bpy.data.objects.new('My explicit emitter', None)
    scene.collection.objects.link(cage)
    cage.location, cage.scale = (2, 3, 0), (4, 2, 8)
    scene.afterglow.effects.emitter = cage
    counts = environment.build(scene, 'LASER_SHOW')
    assert counts['lasers'] > 0 and scene.afterglow.collection is None
    before = effects.settings_sig(scene)
    scene.afterglow.collection = bpy.data.collections.new('Unused main input')
    assert effects.settings_sig(scene) == before and not effects.outdated(scene)


def test_parent_bounds_exclude_owned_effects_and_emitter_cages():
    scene, bounds, objects = venue()
    parent = bpy.data.collections.new('Whole scene source bounds')
    scene.collection.children.link(parent)
    parent.children.link(bounds)
    scene.afterglow.effects.bounds_collection = parent
    expected = effects.make_venue(scene)
    environment.build(scene, 'LASER_SHOW')
    parent.children.link(effects._collections(scene)[0])
    actual = effects.make_venue(scene)
    assert actual.center == expected.center and actual.half() == expected.half()
    cage = effects.fit_cage(scene)
    parent.objects.link(cage)
    scene.afterglow.effects.emitter = None
    cage.scale = (1000, 1000, 1000)
    actual = effects.make_venue(scene)
    assert actual.center == expected.center and actual.half() == expected.half()
    environment.build(scene, 'LASER_SHOW')
    assert not effects.outdated(scene)


def test_saved_custom_environment_look_ignores_object_style_and_reuses_playback():
    scene, bounds, objects = venue()
    data = lookbook.capture(scene)
    data['layers']['lasers']['enabled'] = True
    data['layers']['lasers']['weight'] = 1.0
    counts = environment.build(scene, look_data=data)
    assert counts['lasers'] > 0 and not any(obj.modifiers for obj in objects)
    strip = scene.sequence_editor.strips[core.STRIP_NAME]
    environment.build(scene, look_data=data)
    assert len(scene.sequence_editor.strips) == 1 and scene.sequence_editor.strips[core.STRIP_NAME] == strip
    before = set(effects._collections(scene))
    try:
        environment.build(scene, 'LASER_SHOW', data)
    except ValueError:
        pass
    else:
        raise AssertionError('Ambiguous look sources must be rejected')
    assert set(effects._collections(scene)) == before


def test_environment_settings_persist_through_save_and_reopen():
    scene, bounds, objects = venue()
    scene.afterglow.environment_look = 'LASER_SHOW'
    scene.afterglow.environment_advanced = True
    scene.afterglow.sound.pack()
    environment.build(scene, 'LASER_SHOW')
    bounds_name = bounds.name
    with tempfile.TemporaryDirectory() as folder:
        path = str(Path(folder) / 'environment.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        scene = bpy.context.scene
        assert scene.afterglow.collection is None
        assert scene.afterglow.effects.bounds_collection.name == bounds_name
        assert scene.afterglow.environment_look == 'LASER_SHOW' and scene.afterglow.environment_advanced
        assert not effects.outdated(scene)
        environment.build(scene, scene.afterglow.environment_look)
        assert len(scene.sequence_editor.strips) == 1


def test_simple_environment_build_restores_full_mix_and_advanced_preserves_it():
    scene, bounds, objects = venue()
    s, fx = scene.afterglow, scene.afterglow.effects
    fx.master_brightness, fx.solo_layer = 0.0, 'strikes'
    environment.build(scene, 'LASER_SHOW')
    assert fx.master_brightness == 1.0 and fx.solo_layer == 'ALL'
    s.environment_advanced = True
    fx.master_brightness, fx.solo_layer = 0.4, 'lasers'
    environment.build(scene, 'LASER_SHOW')
    assert abs(fx.master_brightness - 0.4) < 1e-6 and fx.solo_layer == 'lasers'
    s.environment_advanced = False
    environment.build(scene)
    assert abs(fx.master_brightness - 0.4) < 1e-6 and fx.solo_layer == 'lasers'


helpers.run(globals())
