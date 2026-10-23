"""Calibration preserves authored animation and rejects conflicts before scene edits."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import core, easy, edition, light_reaction, nodes, workflow

if edition.IS_PRO:
    from afterglow import effects


def stage():
    scene, collection, objects = helpers.visual_scene()
    data = bpy.data.lights.new('My light data', 'POINT')
    data.energy = 800.0
    light = bpy.data.objects.new('My light', data)
    collection.objects.link(light)
    core.apply(scene)
    return scene, collection, objects, light


def plain(value):
    return tuple(value) if hasattr(value, '__len__') and not isinstance(value, str) else value


def curves(owner):
    ad = owner.animation_data
    if ad is None:
        return ()
    result = []
    actions = {ad.action} if ad.action else set()
    for track in ad.nla_tracks:
        for strip in track.strips:
            if getattr(strip, 'action', None):
                actions.add(strip.action)
    for action in actions:
        for layer in action.layers:
            for strip in layer.strips:
                for bag in getattr(strip, 'channelbags', ()):
                    for curve in bag.fcurves:
                        result.append((action.name, bag.slot_handle, curve.data_path,
                                       curve.array_index, tuple(tuple(k.co) for k in curve.keyframe_points)))
    result.extend(('driver', curve.data_path, curve.array_index, curve.driver.expression)
                  for curve in ad.drivers)
    return tuple(sorted(result))


def snapshot(scene):
    objects = tuple(sorted(scene.objects, key=lambda obj: obj.name))
    setup = tuple((obj, obj.data, tuple(obj.modifiers), curves(obj),
                   tuple((name, plain(nodes.get_input(core.reaction(obj), name)))
                         for name in ('Gain', 'Threshold', 'Ripple Gain', 'Ripple Threshold',
                                      'Use Colors', 'Color A', 'Color B', 'Glow', 'Intensity', 'Decay'))
                   if core.reaction(obj) else ()) for obj in objects)
    mats = tuple((mat, helpers.snapshot(mat)) for mat in bpy.data.materials)
    ids = tuple(set(getattr(bpy.data, name)) for name in
                ('objects', 'lights', 'materials', 'node_groups', 'actions', 'collections'))
    native = (scene.frame_current, scene.afterglow.fast_playback,
              tuple(scene.sequence_editor.strips) if scene.sequence_editor else ())
    mix = ((scene.afterglow.effects.master_brightness, scene.afterglow.effects.solo_layer,
            tuple((name, getattr(scene.afterglow.effects, name).enabled,
                   getattr(scene.afterglow.effects, name).intensity) for name in effects.LAYERS))
           if edition.IS_PRO else ())
    return setup, mats, ids, native, mix


def protected(scene, obj, control, action):
    before = snapshot(scene)
    try:
        action()
    except ValueError as error:
        assert 'Calibration' in str(error) and obj.name in str(error) and control in str(error), str(error)
    else:
        raise AssertionError('Animated calibration controls must reject before any build edits')
    assert snapshot(scene) == before


def key(obj, name, value=0.0):
    socket = nodes._socket(core.reaction(obj), name)
    socket.value = value
    socket.keyframe_insert('value', frame=1)
    return socket


def test_build_protects_animated_gain_on_mesh_and_light_before_applying_a_look():
    scene, _, meshes, light = stage()
    for obj in (meshes[0], light):
        key(obj, 'Gain')
        protected(scene, obj, 'Gain', lambda: workflow.create_reaction(
            scene, look_identifier='NEON_CLUB', style_scope='OBJECT'))
        obj.animation_data_clear()


def test_manual_calibrate_protects_animated_threshold_on_all_targets():
    scene, _, meshes, light = stage()
    for obj in (meshes[0], light):
        key(obj, 'Threshold', 0.2)
        protected(scene, obj, 'Threshold', lambda: core.calibrate(scene))
        obj.animation_data_clear()


def test_muted_nla_calibration_animation_is_guarded_and_preserved():
    scene, _, meshes, _ = stage()
    obj = meshes[0]
    key(obj, 'Gain')
    ad = obj.animation_data
    action, slot = ad.action, ad.action_slot
    track = ad.nla_tracks.new()
    strip = track.strips.new('My authored response', 1, action)
    strip.action_slot = slot
    track.mute = True
    ad.action = None
    protected(scene, obj, 'Gain', lambda: workflow.create_reaction(scene))
    assert track.mute and strip.action == action and strip.action_slot == slot


def test_driven_calibration_controls_are_guarded_even_when_muted():
    scene, _, meshes, light = stage()
    for obj in (meshes[0], light):
        path = nodes._socket(core.reaction(obj), 'Threshold').path_from_id('value')
        driver = obj.driver_add(path)
        driver.driver.expression = '0.0'
        driver.mute = True
        protected(scene, obj, 'Threshold', lambda: workflow.create_reaction(scene))
        obj.driver_remove(path)


def test_outside_collection_reactions_are_guarded_before_any_selected_object_changes():
    scene, collection, meshes, light = stage()
    for obj in (meshes[0], light):
        collection.objects.unlink(obj)
        scene.collection.objects.link(obj)
        key(obj, 'Gain')
        protected(scene, obj, 'Gain', lambda: workflow.create_reaction(scene))
        obj.animation_data_clear()


def test_first_light_build_guards_animated_inputs_before_copying_light_data():
    scene, collection, meshes = helpers.visual_scene()
    data = bpy.data.lights.new('Unbuilt native light data', 'POINT')
    data.energy = 800.0
    obj = bpy.data.objects.new('Unbuilt light', data)
    collection.objects.link(obj)
    socket = light_reaction.LightReaction(obj).socket('Gain')
    socket.value = 0.0
    socket.keyframe_insert('value', frame=1)
    protected(scene, obj, 'Gain', lambda: easy.create_visual(bpy.context))
    assert obj.data == data and light_reaction.TAG not in obj
    assert all(core.find_modifier(mesh) is None for mesh in meshes)


def test_ripple_guards_the_active_pair_and_leaves_inactive_animation_authored():
    scene, _, meshes, light = stage()
    scene.afterglow.ripple = True
    key(light, 'Gain')
    # Gain is unused while Ripple is enabled, so it does not block its separate pair.
    workflow.calibration_preflight(scene)
    calibrated = core.calibrate(scene)
    assert light in calibrated and nodes.get_input(core.reaction(light), 'Gain') == 0.0
    for name in ('Ripple Gain', 'Ripple Threshold'):
        key(meshes[0], name)
        protected(scene, meshes[0], name, lambda: workflow.create_reaction(scene))
        meshes[0].animation_data_clear()


def test_unrelated_animation_and_inactive_ripple_controls_do_not_block_build():
    scene, _, meshes, light = stage()
    meshes[0].location.x = 7.0
    meshes[0].keyframe_insert('location', frame=1)
    key(light, 'Ripple Gain')
    animation = curves(meshes[0]), curves(light)
    result = workflow.create_reaction(scene)
    assert result['calibrated'] == len(meshes) + 1
    assert all(entry in curves(meshes[0]) for entry in animation[0])
    assert all(entry in curves(light) for entry in animation[1])


def test_legacy_geometry_group_without_ripple_pair_can_migrate_during_build():
    scene, collection, meshes = helpers.visual_scene()
    core.apply(scene)
    helpers.v1_group()
    # Simulate opening an older project without invoking the RNA sync callback.
    scene.afterglow['ripple'] = True
    workflow.calibration_preflight(scene)
    result = workflow.create_reaction(scene)
    assert result['calibrated'] == len(meshes)
    assert all(nodes.get_input(core.reaction(obj), 'Ripple Gain') > 0 for obj in meshes)


def test_calibration_conflict_preserves_existing_environment_build_and_mix():
    if not edition.IS_PRO:
        return
    scene, _, meshes, _ = stage()
    fx = scene.afterglow.effects
    fx.blooms.enabled = True
    effects.build(scene)
    fx.master_brightness, fx.solo_layer = 0.43, 'blooms'
    original = {obj: curves(obj) for collection in effects._collections(scene)
                for obj in collection.all_objects}
    key(meshes[0], 'Gain')
    protected(scene, meshes[0], 'Gain', lambda: workflow.create_show(scene, 'LASER_SHOW'))
    assert original == {obj: curves(obj) for collection in effects._collections(scene)
                        for obj in collection.all_objects}


helpers.run(globals())
