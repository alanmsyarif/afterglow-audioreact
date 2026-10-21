"""Independent beam appearance with legacy .blend and live-mixer compatibility."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import effects, presets


def stage():
    scene = bpy.context.scene
    collection = bpy.data.collections.new("My effect bounds")
    scene.collection.children.link(collection)
    helpers.plane("left", collection).location.x = -3
    helpers.plane("right", collection).location.x = 3
    scene.afterglow.collection = collection
    scene.afterglow.sound = helpers.sound()
    fx = scene.afterglow.effects
    for name in ("strikes", "gun"):
        layer = getattr(fx, name)
        layer.enabled, layer.weight, layer.trigger_band = True, 1.0, "FULL"
    bpy.context.view_layer.update()
    return scene


def objects(scene, name, kind):
    return [ob for collection in effects._collections(scene) for ob in collection.objects
            if ob.name.startswith(f"AFX_{name}_") and ob.type == kind]


def materials(scene, name):
    return {slot.material for ob in objects(scene, name, "MESH")
            for slot in ob.material_slots if slot.material}


def test_new_layer_controls_read_legacy_values_until_individually_edited():
    fx = bpy.context.scene.afterglow.effects
    assert not fx.strikes.visible_beam and not fx.gun.visible_beam
    fx.beams, fx.beam_glow = True, 2.5
    assert fx.strikes.visible_beam and fx.gun.visible_beam
    assert fx.strikes.beam_glow == fx.gun.beam_glow == 2.5
    fx.strikes.visible_beam, fx.strikes.beam_glow = False, 0.0
    fx.beam_glow = 3.0
    assert not fx.strikes.visible_beam and fx.gun.visible_beam
    assert fx.strikes.beam_glow == 0.0 and fx.gun.beam_glow == 3.0
    assert fx.bl_rna.properties['beams'].is_hidden
    assert fx.bl_rna.properties['beam_glow'].is_hidden
    for name in effects.LAYERS:
        fields = set(getattr(fx, name).bl_rna.properties.keys())
        assert ({'visible_beam', 'beam_glow'} <= fields) == (name in ('strikes', 'gun'))


def test_each_effect_builds_its_own_beam_without_enabling_the_other():
    scene = stage()
    fx = scene.afterglow.effects
    for strike, gun in ((True, False), (False, True), (True, True), (False, False)):
        fx.strikes.visible_beam, fx.gun.visible_beam = strike, gun
        counts = effects.build(scene)
        assert counts['strikes'] > 0 and counts['gun'] > 0
        assert bool(objects(scene, 'strikes', 'MESH')) == strike
        assert bool(objects(scene, 'gun', 'MESH')) == gun
        assert len(objects(scene, 'strikes', 'LIGHT')) == effects.POOL['strikes']
        assert len(objects(scene, 'gun', 'LIGHT')) == effects.POOL['gun']
        assert not effects.outdated(scene)


def test_glow_retunes_only_its_effect_without_rebuilding_or_changing_light_energy():
    scene = stage()
    fx = scene.afterglow.effects
    fx.strikes.visible_beam = fx.gun.visible_beam = True
    effects.build(scene)
    identity = set(bpy.data.objects), set(bpy.data.actions)
    energy = {ob.data.name: tuple(tuple(point.co) for fc in helpers.fcurves(ob.data)
              if fc.data_path == 'energy' for point in fc.keyframe_points)
              for name in ('strikes', 'gun') for ob in objects(scene, name, 'LIGHT')}
    for strike, gun in ((0.0, 2.0), (3.0, 0.25), (1.0, 1.0)):
        fx.strikes.beam_glow, fx.gun.beam_glow = strike, gun
        for name, amount in (('strikes', strike), ('gun', gun)):
            for material in materials(scene, name):
                value = material.node_tree.nodes['Glow'].inputs[1].default_value
                assert abs(value - material[effects.GAIN] * amount) < 1e-4
        assert identity == (set(bpy.data.objects), set(bpy.data.actions))
        assert not effects.outdated(scene)
    assert energy == {ob.data.name: tuple(tuple(point.co) for fc in helpers.fcurves(ob.data)
                      if fc.data_path == 'energy' for point in fc.keyframe_points)
                      for name in ('strikes', 'gun') for ob in objects(scene, name, 'LIGHT')}


def test_visibility_changes_need_rebuild_but_overridden_legacy_controls_do_not():
    scene = stage()
    fx = scene.afterglow.effects
    fx.strikes.visible_beam, fx.gun.visible_beam = True, False
    effects.build(scene)
    fx.beams = True  # Neither layer reads the old shared property any longer.
    assert not effects.outdated(scene)
    fx.gun.visible_beam = True
    assert effects.outdated(scene)
    effects.build(scene)
    fx.strikes.visible_beam = False
    assert effects.outdated(scene)
    effects.build(scene)
    assert not objects(scene, 'strikes', 'MESH') and objects(scene, 'gun', 'MESH')


def test_old_project_fallback_and_new_independent_values_survive_save_and_reopen():
    scene = stage()
    fx = scene.afterglow.effects
    fx.beams, fx.beam_glow = True, 2.0
    scene.afterglow.sound.pack()
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, 'legacy-beams.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        scene = bpy.context.scene
        fx = scene.afterglow.effects
        assert fx.strikes.visible_beam and fx.gun.visible_beam
        assert fx.strikes.beam_glow == fx.gun.beam_glow == 2.0
        fx.strikes.visible_beam = False
        fx.gun.beam_glow = 0.0
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
    scene = bpy.context.scene
    fx = scene.afterglow.effects
    assert not fx.strikes.visible_beam and fx.gun.visible_beam
    assert fx.strikes.beam_glow == 2.0 and fx.gun.beam_glow == 0.0
    effects.build(scene)
    assert not objects(scene, 'strikes', 'MESH') and objects(scene, 'gun', 'MESH')
    fx.gun.beam_glow = 1.5
    assert all(abs(mat.node_tree.nodes['Glow'].inputs[1].default_value - mat[effects.GAIN] * 1.5) < 1e-4
               for mat in materials(scene, 'gun'))


def test_curated_looks_reset_both_beam_modes_and_glows_atomically():
    scene = stage()
    fx = scene.afterglow.effects
    fx.strikes.visible_beam, fx.gun.visible_beam = False, True
    fx.strikes.beam_glow, fx.gun.beam_glow = 0.2, 2.5
    presets.apply(scene, 'NEON_CLUB')
    assert fx.strikes.visible_beam and fx.gun.visible_beam
    assert fx.strikes.beam_glow == fx.gun.beam_glow == 1.0
    fx.strikes.beam_glow = 0.4
    fx.strikes.keyframe_insert('beam_glow', frame=1)
    try:
        presets.apply(scene, 'AURORA_FLOW')
    except ValueError as error:
        assert 'animated controls' in str(error)
    else:
        raise AssertionError('Animated per-effect beam controls must be protected')
    assert fx.strikes.visible_beam and fx.gun.visible_beam
    assert abs(fx.strikes.beam_glow - 0.4) < 1e-6


helpers.run(globals())
