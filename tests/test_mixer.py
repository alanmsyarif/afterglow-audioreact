"""Live art direction preserves the built show's timing and sampled animation."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import numpy as np  # noqa: E402
import helpers  # noqa: E402
from afterglow import effects  # noqa: E402


def stage(*names):
    scene = bpy.context.scene
    venue = bpy.data.collections.new("Mixer venue")
    scene.collection.children.link(venue)
    helpers.plane("left", venue).location.x = -3
    helpers.plane("right", venue).location.x = 3
    scene.afterglow.collection = venue
    scene.afterglow.sound = helpers.sound()
    fx = scene.afterglow.effects
    fx.beams = fx.signal_graphics = True
    for name in names:
        layer = getattr(fx, name)
        layer.enabled, layer.weight, layer.trigger_band = True, 1.0, "FULL"
    bpy.context.view_layer.update()
    return scene


def keys(scene):
    """Brightness source curves grouped by layer; array columns are frame/value."""
    result = {}
    for collection in effects._collections(scene):
        for light in collection.all_objects:
            if light.type != "LIGHT":
                continue
            layer = next(name for name in effects.LAYERS
                         if light.name.startswith(f"{effects.PREFIX}_{name}_"))
            owners = [(light.data, "energy")]
            owners.extend((child, effects.GLOW_PATH) for child in light.children if child.get(effects.TAG))
            for owner, path in owners:
                for curve in helpers.fcurves(owner):
                    if curve.data_path == path:
                        result[(layer, owner.name, path)] = np.array([tuple(point.co) for point in curve.keyframe_points])
    return result


def assert_scale(before, after, scale_for):
    assert before and set(before) == set(after)
    for key, values in before.items():
        assert np.array_equal(values[:, 0], after[key][:, 0]), key
        assert np.allclose(values[:, 1] * scale_for(key[0]), after[key][:, 1], rtol=2e-6, atol=2e-5), key


def test_master_scales_lights_beams_and_signals_without_rebuilding():
    scene = stage(*effects.LAYERS)
    counts = effects.build(scene)
    assert all(counts[name] > 0 for name in effects.LAYERS), counts
    baseline = keys(scene)
    assert {key[0] for key in baseline} == set(effects.LAYERS)
    assert {key[0] for key in baseline if key[2] == effects.GLOW_PATH} == {"strikes", "gun", "signals"}
    identity = (set(bpy.data.objects), set(bpy.data.actions))
    fx = scene.afterglow.effects
    for multiplier in (0.25, 0.0, 2.0, 0.0, 1.0):
        fx.master_brightness = multiplier
        assert_scale(baseline, keys(scene), lambda name: multiplier)
        assert not effects.outdated(scene)
    assert identity == (set(bpy.data.objects), set(bpy.data.actions))


def test_master_updates_evaluated_light_and_beam_at_the_current_frame():
    scene = stage("strikes")
    effects.build(scene)
    light = next(ob for ob in effects._collections(scene)[0].objects
                 if ob.type == "LIGHT" and any(point.co.y > 0
                    for curve in helpers.fcurves(ob.data) if curve.data_path == "energy"
                    for point in curve.keyframe_points))
    curve = next(curve for curve in helpers.fcurves(light.data) if curve.data_path == "energy")
    frame = float(max(curve.keyframe_points, key=lambda point: point.co.y).co.x)
    scene.frame_set(int(frame), subframe=frame % 1)
    shaft = next(child for child in light.children if child.get(effects.TAG))
    baseline = np.array([light.data.energy, shaft[effects.GLOW]])
    assert np.all(baseline > 0), baseline
    for factor in (0.0, 0.5, 1.0):
        scene.afterglow.effects.master_brightness = factor
        bpy.context.view_layer.update()
        assert np.allclose([light.data.energy, shaft[effects.GLOW]], baseline * factor, atol=1e-4)


def test_solo_preserves_layer_settings_and_restores_the_full_mix():
    scene = stage(*effects.LAYERS)
    effects.build(scene)
    baseline = keys(scene)
    fx = scene.afterglow.effects
    state = [(getattr(fx, name).enabled, getattr(fx, name).intensity, getattr(fx, name).weight)
             for name in effects.LAYERS]
    for solo in ("signals", "strikes", "blooms", "ALL"):
        fx.solo_layer = solo
        assert_scale(baseline, keys(scene), lambda name: 1.0 if solo in ("ALL", name) else 0.0)
    assert state == [(getattr(fx, name).enabled, getattr(fx, name).intensity, getattr(fx, name).weight)
                     for name in effects.LAYERS]
    assert not effects.outdated(scene)


def test_intensity_edits_while_muted_recover_from_original_source_keys():
    scene = stage("strikes", "signals")
    effects.build(scene)
    baseline = keys(scene)
    fx = scene.afterglow.effects
    fx.master_brightness = 0.0
    fx.strikes.intensity = 2.0
    fx.signals.intensity = 0.5
    assert all(np.max(values[:, 1]) == 0.0 for values in keys(scene).values())
    fx.solo_layer = "signals"
    fx.master_brightness = 3.0
    assert_scale(baseline, keys(scene), lambda name: 1.5 if name == "signals" else 0.0)
    fx.solo_layer = "ALL"
    assert_scale(baseline, keys(scene), lambda name: 6.0 if name == "strikes" else 1.5)


def test_show_built_at_zero_can_be_unmuted_after_saving_and_reopening():
    scene = stage("strikes", "signals")
    effects.build(scene)
    baseline = keys(scene)
    scene.afterglow.effects.master_brightness = 0.0
    effects.build(scene)
    assert all(np.max(values[:, 1]) == 0.0 for values in keys(scene).values())
    with tempfile.TemporaryDirectory(prefix="afterglow-mixer-") as folder:
        path = os.path.join(folder, "muted-show.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
    scene = bpy.context.scene
    scene.afterglow.effects.master_brightness = 1.0
    assert_scale(baseline, keys(scene), lambda name: 1.0)
    assert not effects.outdated(scene)


def test_sampled_intensity_animation_keeps_its_shape_under_master_and_solo():
    scene = stage("strikes", "signals")
    fx = scene.afterglow.effects
    for name in ("strikes", "signals"):
        layer = getattr(fx, name)
        for frame, value in ((1, 0.2), (60, 3.0), (100, 0.6)):
            layer.intensity = value
            scene.keyframe_insert(f"afterglow.effects.{name}.intensity", frame=frame)
    effects.build(scene)
    baseline = keys(scene)
    scene.frame_set(60)
    fx.master_brightness = 0.5
    assert_scale(baseline, keys(scene), lambda name: 0.5)
    fx.solo_layer = "signals"
    fx.master_brightness = 0.0
    fx.master_brightness = 2.0
    assert_scale(baseline, keys(scene), lambda name: 2.0 if name == "signals" else 0.0)
    fx.solo_layer = "ALL"
    assert_scale(baseline, keys(scene), lambda name: 2.0)
    assert not effects.outdated(scene)


def test_other_scenes_keep_their_built_mix():
    first = stage("strikes")
    effects.build(first)
    baseline = keys(first)
    second = bpy.data.scenes.new("Other mix")
    second.afterglow.collection = first.afterglow.collection
    second.afterglow.sound = first.afterglow.sound
    second.afterglow.effects.strikes.enabled = True
    second.afterglow.effects.strikes.weight = 1.0
    second.afterglow.effects.strikes.trigger_band = "FULL"
    effects.build(second)
    second_baseline = keys(second)
    first.afterglow.effects.master_brightness = 0.0
    assert all(np.max(values[:, 1]) == 0.0 for values in keys(first).values())
    assert_scale(second_baseline, keys(second), lambda name: 1.0)
    # This scene is not the active context scene; the property must update its owner.
    second.afterglow.effects.master_brightness = 0.25
    assert_scale(second_baseline, keys(second), lambda name: 0.25)
    assert all(np.max(values[:, 1]) == 0.0 for values in keys(first).values())
    first.afterglow.effects.master_brightness = 1.0
    assert_scale(baseline, keys(first), lambda name: 1.0)


if __name__ == "__main__":
    helpers.run(globals())
