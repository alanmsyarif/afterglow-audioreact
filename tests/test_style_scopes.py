"""Object styling and environment styling have independent guards and mutations."""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import bake, core, effects, lookbook, nodes, presets, workflow


def stage():
    scene, collection, objects = helpers.visual_scene()
    core.apply(scene)
    return scene, collection, objects


def plain(value):
    return tuple(value) if hasattr(value, '__len__') and not isinstance(value, str) else value


def object_style(objects):
    return {obj.name: tuple(plain(nodes.get_input(core.find_modifier(obj), field))
                            for field in lookbook.OBJECT_FIELDS) for obj in objects}


def fx_style(scene):
    snapshot = lookbook.capture(scene)
    fx = scene.afterglow.effects
    return snapshot['layers'], snapshot['effects'], fx.master_brightness, fx.solo_layer


def build_state(scene):
    result = {}
    for collection in effects._collections(scene):
        for obj in collection.all_objects:
            for owner in (obj, obj.data):
                if owner is None:
                    continue
                result[(owner.bl_rna.identifier, owner.name)] = tuple(
                    (curve.data_path, curve.array_index,
                     tuple(tuple(point.co) for point in curve.keyframe_points))
                    for curve in helpers.fcurves(owner))
    return result


def rejects(action):
    try:
        action()
    except ValueError:
        return
    raise AssertionError('Expected invalid scope or protected animation to reject the change')


def test_object_preset_ignores_environment_animation_and_preserves_its_build():
    scene, collection, objects = stage()
    presets.apply(scene, 'NEON_CLUB')
    fx = scene.afterglow.effects
    fx.blooms.intensity = 0.43
    fx.blooms.keyframe_insert('intensity', frame=1)
    effects.build(scene)
    before, animation = fx_style(scene), build_state(scene)
    owned = set(effects._collections(scene)[0].all_objects)
    presets.preflight(scene, 'AURORA_FLOW', scope='OBJECT')
    assert presets.apply(scene, 'AURORA_FLOW', scope='OBJECT') == len(objects)
    assert all(abs(nodes.get_input(core.find_modifier(obj), 'Decay') - 0.5) < 1e-5
               for obj in objects)
    assert fx_style(scene) == before and build_state(scene) == animation
    assert set(effects._collections(scene)[0].all_objects) == owned
    assert not effects.outdated(scene)


def test_effect_preset_does_not_access_or_guard_animated_object_style():
    scene, collection, objects = stage()
    nodes._socket(core.find_modifier(objects[0]), 'Color B').keyframe_insert('value', frame=1)
    before = object_style(objects)
    tree = helpers.snapshot(objects[0].active_material)
    real_targets = core.targets
    def unwanted_scan(collection):
        raise AssertionError('Environment styling must not inspect reacting objects')
    core.targets = unwanted_scan
    try:
        presets.preflight(scene, 'LASER_SHOW', scope='EFFECTS')
        assert presets.apply(scene, 'LASER_SHOW', scope='EFFECTS') == 2
    finally:
        core.targets = real_targets
    assert object_style(objects) == before
    assert helpers.snapshot(objects[0].active_material) == tree
    assert scene.afterglow.effects.lasers.enabled and scene.afterglow.effects.blooms.enabled
    assert not scene.afterglow.fast_playback and not effects._collections(scene)


def test_effect_preset_needs_no_object_collection_material_or_sound():
    scene = bpy.context.scene
    before = set(bpy.data.objects), set(bpy.data.materials), set(bpy.data.node_groups)
    presets.apply(scene, 'SIGNAL_GRID', scope='EFFECTS')
    assert scene.afterglow.effects.signals.enabled
    assert scene.afterglow.collection is None and scene.afterglow.sound is None
    assert before == (set(bpy.data.objects), set(bpy.data.materials), set(bpy.data.node_groups))


def test_object_snapshot_ignores_fx_animation_and_keeps_environment_values():
    scene, collection, objects = stage()
    data = lookbook.capture(scene)
    data['object']['Decay'] = 0.72
    data['layers']['lasers']['enabled'] = True
    scene.afterglow.effects.gun.keyframe_insert('timing', frame=1)
    before = fx_style(scene)
    normalized = lookbook.preflight(scene, data, scope='OBJECT')
    assert normalized == data
    assert lookbook.apply(scene, normalized, scope='OBJECT') == len(objects)
    assert fx_style(scene) == before
    assert all(abs(nodes.get_input(core.find_modifier(obj), 'Decay') - 0.72) < 1e-5
               for obj in objects)


def test_effect_snapshot_ignores_object_animation_and_does_not_scan_objects():
    scene, collection, objects = stage()
    data = lookbook.capture(scene)
    data['layers']['lasers']['enabled'] = True
    data['layers']['lasers']['intensity'] = 0.4
    data['object']['Decay'] = 0.9
    nodes._socket(core.find_modifier(objects[0]), 'Decay').keyframe_insert('value', frame=1)
    before = object_style(objects)
    real_targets = core.targets
    def unwanted_scan(collection):
        raise AssertionError('Environment snapshot must not inspect reacting objects')
    core.targets = unwanted_scan
    try:
        lookbook.preflight(scene, data, scope='EFFECTS')
        assert lookbook.apply(scene, data, scope='EFFECTS') == 1
    finally:
        core.targets = real_targets
    assert object_style(objects) == before
    assert scene.afterglow.effects.lasers.enabled
    assert abs(scene.afterglow.effects.lasers.intensity - 0.4) < 1e-5
    assert not scene.afterglow.fast_playback


def test_invalid_scope_rejects_before_preset_snapshot_or_reaction_mutation():
    scene, collection, objects = stage()
    data = lookbook.capture(scene)
    before = fx_style(scene), object_style(objects), helpers.snapshot(objects[0].active_material)
    for scope in ('UNKNOWN', None, ['OBJECT']):
        for action in (
                lambda: presets.preflight(scene, 'NEON_CLUB', scope=scope),
                lambda: presets.apply(scene, 'NEON_CLUB', scope=scope),
                lambda: lookbook.preflight(scene, data, scope=scope),
                lambda: lookbook.apply(scene, data, scope=scope),
                lambda: workflow.create_reaction(scene, 'NEON_CLUB', style_scope=scope)):
            rejects(action)
            assert before == (fx_style(scene), object_style(objects), helpers.snapshot(objects[0].active_material))
            assert not scene.afterglow.fast_playback


def test_each_scope_still_protects_its_own_animation_atomically():
    scene, collection, objects = stage()
    data = lookbook.capture(scene)
    data['object']['Glow'] = 0.3
    mod = core.find_modifier(objects[0])
    nodes._socket(mod, 'Color A').keyframe_insert('value', frame=1)
    before = fx_style(scene), object_style(objects)
    rejects(lambda: presets.apply(scene, 'LASER_SHOW', scope='OBJECT'))
    rejects(lambda: lookbook.apply(scene, data, scope='OBJECT'))
    assert (fx_style(scene), object_style(objects)) == before
    objects[0].animation_data_clear()
    scene.afterglow.effects.strikes.keyframe_insert('weight', frame=1)
    before = fx_style(scene), object_style(objects)
    rejects(lambda: presets.apply(scene, 'LASER_SHOW', scope='EFFECTS'))
    rejects(lambda: lookbook.apply(scene, data, scope='EFFECTS'))
    assert (fx_style(scene), object_style(objects)) == before


def test_scoped_snapshot_still_rejects_malformed_data_on_the_other_side():
    scene, collection, objects = stage()
    data = lookbook.capture(scene)
    before = fx_style(scene), object_style(objects)
    invalid = copy.deepcopy(data)
    invalid['layers']['lasers']['intensity'] = 'not a number'
    rejects(lambda: lookbook.apply(scene, invalid, scope='OBJECT'))
    invalid = copy.deepcopy(data)
    invalid['object']['Decay'] = -1
    rejects(lambda: lookbook.apply(scene, invalid, scope='EFFECTS'))
    assert (fx_style(scene), object_style(objects)) == before


def test_object_reaction_refresh_keeps_existing_fx_build_and_mix_intact():
    scene, collection, objects = stage()
    fx = scene.afterglow.effects
    fx.strikes.enabled, fx.strikes.weight = True, 1.0
    fx.master_brightness, fx.solo_layer = 0.65, 'blooms'
    fx.gun.keyframe_insert('timing', frame=1)
    effects.build(scene)
    before, animation = fx_style(scene), build_state(scene)
    owned = set(effects._collections(scene)[0].all_objects)
    result = workflow.create_reaction(scene, 'AURORA_FLOW', style_scope='OBJECT')
    assert result['applied'] == result['calibrated'] == result['baked'] == len(objects)
    assert 'effects' not in result
    assert fx_style(scene) == before and build_state(scene) == animation
    assert set(effects._collections(scene)[0].all_objects) == owned
    assert not effects.outdated(scene) and not bake.outdated(scene)


def test_object_reaction_can_apply_a_snapshot_without_overwriting_fx():
    scene, collection, objects = stage()
    data = lookbook.capture(scene)
    data['object']['Decay'] = 0.66
    data['layers']['lasers']['enabled'] = True
    scene.afterglow.effects.blooms.keyframe_insert('weight', frame=1)
    before = fx_style(scene)
    result = workflow.create_reaction(scene, look_data=data, style_scope='OBJECT')
    assert result['baked'] == len(objects) and not bake.outdated(scene)
    assert fx_style(scene) == before
    assert all(abs(nodes.get_input(core.find_modifier(obj), 'Decay') - 0.66) < 1e-5
               for obj in objects)


helpers.run(globals())
