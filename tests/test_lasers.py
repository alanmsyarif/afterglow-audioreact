"""Real audio onsets, visible laser fans, bounded pooling and scene ownership."""
import os
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import numpy as np  # noqa: E402
import helpers  # noqa: E402
from afterglow import effects, lasers  # noqa: E402

PULSES = (0.5, 1.5, 2.5)


def track(pulses=PULSES, seconds=4):
    path = os.path.join(tempfile.gettempdir(), f"afterglow_lasers_{len(pulses)}_{seconds}.wav")
    rate = 44100
    t = np.arange(int(seconds * rate)) / rate
    samples = np.zeros_like(t)
    for onset in pulses:
        u = (t - onset) / 0.10
        samples += 0.8 * np.where((u >= 0) & (u <= 1), np.sin(np.pi * u) ** 2, 0) * np.sin(2 * np.pi * 80 * t)
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes((samples * 32767).astype("<i2").tobytes())
    return path


def stage(path=None):
    scene = bpy.context.scene
    venue = bpy.data.collections.new("My venue")
    scene.collection.children.link(venue)
    floor = helpers.plane("My floor", venue, helpers.emission_material("My material"))
    floor.scale = (4, 4, 1)
    scene.afterglow.collection = venue
    scene.afterglow.sound = bpy.data.sounds.load(path or track(), check_existing=False)
    scene.render.fps = 24
    fx = scene.afterglow.effects
    fx.lasers.enabled, fx.lasers.weight = True, 1.0
    bpy.context.view_layer.update()
    return scene


def objects(scene, kind=None):
    return sorted((ob for coll in effects._collections(scene) for ob in coll.all_objects
                   if ob.name.startswith("AFX_lasers_") and (kind is None or ob.type == kind)),
                  key=lambda ob: ob.name)


def at(scene, seconds):
    frame = scene.afterglow.start_frame + seconds * scene.render.fps / scene.render.fps_base
    scene.frame_set(int(np.floor(frame)), subframe=frame % 1)


def animation(scene, layer="lasers"):
    out = {}
    for collection in effects._collections(scene):
        for ob in collection.all_objects:
            if not ob.name.startswith(f"AFX_{layer}_"):
                continue
            for owner in (ob, ob.data):
                for curve in helpers.fcurves(owner):
                    out[(owner.name, curve.data_path, curve.array_index)] = tuple(
                        (round(float(p.co.x), 5), round(float(p.co.y), 5)) for p in curve.keyframe_points)
    return out


def test_laser_onsets_make_two_narrow_spots_and_visible_fans_per_pool_slot():
    scene = stage()
    original_meshes = set(bpy.data.meshes)
    assert effects.build(scene)["lasers"] == len(PULSES)
    events = effects.analyze(scene)["lasers"]
    assert all(abs(got[0] - want) < 0.1 for got, want in zip(events, PULSES))
    lights, fans = objects(scene, "LIGHT"), objects(scene, "CURVE")
    assert len(lights) == len(fans) == 2 * effects.POOL["lasers"]
    assert all(light.data.type == "SPOT" and light.data.spot_size < 0.06 for light in lights)
    assert all(fan.parent in lights and fan.get(lasers.RAY_KIND) for fan in fans)
    for fan in fans:
        assert fan.data.bevel_depth > 0
        assert len(fan.data.splines) == scene.afterglow.effects.laser_count
        assert all((spline.points[1].co.xyz - spline.points[0].co.xyz).length > 1 for spline in fan.data.splines)
        assert len({tuple(spline.points[1].co) for spline in fan.data.splines}) == len(fan.data.splines)
        nodes = fan.data.materials[0].node_tree.nodes
        assert any(node.type == "EMISSION" for node in nodes)
        assert not any(node.type.startswith("VOLUME") for node in nodes)
    at(scene, events[0][0] + 0.15)
    assert sum(light.data.energy > 0 for light in lights) == 2
    assert sum(fan[effects.GLOW] > 0 for fan in fans) == 2
    assert set(bpy.data.meshes) == original_meshes


def test_lit_fans_sweep_and_sweep_zero_holds_them_still():
    scene = stage()
    effects.build(scene)
    first = effects.analyze(scene)["lasers"][0][0]
    fan = objects(scene, "CURVE")[0]
    at(scene, first + 0.10)
    before = fan.matrix_world @ fan.data.splines[0].points[1].co.xyz
    assert fan[effects.GLOW] > 0
    at(scene, first + 0.55)
    after = fan.matrix_world @ fan.data.splines[0].points[1].co.xyz
    assert (after - before).length > 0.25
    scene.afterglow.effects.laser_sweep = 0
    effects.build(scene)
    fan = objects(scene, "CURVE")[0]
    at(scene, first + 0.10)
    before = fan.matrix_world.copy()
    at(scene, first + 0.55)
    assert np.allclose(fan.matrix_world, before)


def test_silence_produces_no_rays_materials_or_light_spill():
    scene = stage(track(()))
    assert effects.build(scene)["lasers"] == 0
    assert not objects(scene, "CURVE")
    assert not any(mat.get(effects.TAG) for mat in bpy.data.materials)
    for seconds in (-1, 0, 1, 5, 20):
        at(scene, seconds)
        assert all(light.data.energy == 0 for light in objects(scene, "LIGHT"))


def test_dense_track_reuses_fixed_fans_and_finishes_dark():
    scene = stage(track(tuple(np.arange(0.4, 5.0, 0.20)), 6))
    scene.afterglow.effects.lasers.timing = 4
    assert effects.build(scene)["lasers"] > 15
    assert len(objects(scene)) == effects.POOL["lasers"] * 4
    assert len([mat for mat in bpy.data.materials if mat.get(effects.TAG)]) == 1
    last = effects.analyze(scene)["lasers"][-1][0]
    at(scene, last + lasers.DURATION * 4 + 0.05)
    assert all(ob.data.energy == 0 for ob in objects(scene, "LIGHT"))
    assert all(ob[effects.GLOW] == 0 for ob in objects(scene, "CURVE"))
    before = len(bpy.data.objects), len(bpy.data.actions), len(bpy.data.curves)
    effects.build(scene)
    assert before == (len(bpy.data.objects), len(bpy.data.actions), len(bpy.data.curves))


def test_laser_seed_is_repeatable_and_other_layers_keep_their_stream():
    scene = stage()
    fx = scene.afterglow.effects
    fx.strikes.enabled, fx.strikes.weight = True, 1.0
    fx.lasers.enabled = False
    effects.build(scene)
    original_strikes = animation(scene, "strikes")
    fx.lasers.enabled = True
    effects.build(scene)
    assert original_strikes == animation(scene, "strikes")
    baseline = animation(scene)
    effects.build(scene)
    assert baseline == animation(scene)
    fx.seed += 1
    effects.build(scene)
    assert baseline != animation(scene)


def test_color_intensity_glow_master_zero_and_solo_update_without_rebuilding():
    scene = stage()
    fx = scene.afterglow.effects
    effects.build(scene)
    first = effects.analyze(scene)["lasers"][0][0]
    at(scene, first + 0.15)
    light, fan = objects(scene, "LIGHT")[0], objects(scene, "CURVE")[0]
    energy, brightness = light.data.energy, fan[effects.GLOW]
    identity = set(objects(scene))
    fx.lasers.intensity = 2
    fx.lasers.color = (0.2, 0.4, 0.8)
    fx.laser_glow = 0.25
    fx.signal_glow, fx.beam_glow = 3, 3
    at(scene, first + 0.15)
    assert np.allclose((light.data.energy, fan[effects.GLOW]), (energy * 2, brightness * 2), atol=1e-3)
    assert np.allclose(light.data.color, (0.2, 0.4, 0.8))
    material = fan.data.materials[0]
    assert abs(material.node_tree.nodes["Glow"].inputs[1].default_value - 10) < 1e-5
    emission = next(node for node in material.node_tree.nodes if node.type == "EMISSION")
    assert np.allclose(emission.inputs["Color"].default_value[:3], (0.2, 0.4, 0.8))
    for master in (0, 0.5, 1):
        fx.master_brightness = master
        at(scene, first + 0.15)
        assert np.allclose((light.data.energy, fan[effects.GLOW]),
                           (energy * 2 * master, brightness * 2 * master), atol=1e-3)
    for solo in ("signals", "lasers", "ALL"):
        fx.solo_layer = solo
        at(scene, first + 0.15)
        scale = 0 if solo == "signals" else 2
        assert np.allclose((light.data.energy, fan[effects.GLOW]), (energy * scale, brightness * scale), atol=1e-3)
    assert set(objects(scene)) == identity
    assert not effects.outdated(scene)


def test_count_spread_and_sweep_mark_the_build_outdated_and_change_rays():
    scene = stage()
    fx = scene.afterglow.effects
    effects.build(scene)
    for name, value in (("laser_count", 11), ("laser_spread", 1.7), ("laser_sweep", 0.2)):
        setattr(fx, name, value)
        assert effects.outdated(scene), name
        effects.build(scene)
        assert not effects.outdated(scene), name
    fan = objects(scene, "CURVE")[0]
    assert len(fan.data.splines) == 11
    left, right = (fan.data.splines[i].points[1].co.xyz.normalized() for i in (0, -1))
    assert abs(left.angle(right) - 1.7) < 1e-5


def test_timing_and_intensity_animation_are_sampled_per_onset():
    scene = stage()
    fx = scene.afterglow.effects
    events = effects.analyze(scene)["lasers"]
    for (onset, _), intensity in zip(events, (0.5, 1, 2)):
        frame = scene.afterglow.start_frame + onset * scene.render.fps
        fx.lasers.intensity = intensity
        fx.lasers.keyframe_insert("intensity", frame=frame)
        fx.lasers.timing = 0.5
        fx.lasers.keyframe_insert("timing", frame=frame)
    effects.build(scene)
    for (onset, strength), intensity in zip(events, (0.5, 1, 2)):
        at(scene, onset + 0.1)
        assert abs(sum(fan[effects.GLOW] for fan in objects(scene, "CURVE")) - 2 * strength * intensity) < 1e-3
        at(scene, onset + lasers.DURATION * 0.5 + 0.01)
        assert all(fan[effects.GLOW] == 0 for fan in objects(scene, "CURVE"))


def test_removal_cleans_owned_data_and_keeps_user_materials_and_other_scene():
    scene = stage()
    floor = scene.afterglow.collection.objects["My floor"]
    matrix, material, nodes = floor.matrix_world.copy(), floor.data.materials[0], helpers.snapshot(floor.data.materials[0])
    effects.build(scene)
    first_data = {ob.data.name for ob in objects(scene)}
    other = bpy.data.scenes.new("Another show")
    bpy.context.window.scene = other
    stage()
    effects.build(other)
    other_objects = set(objects(other))
    # An inactive scene's property callback must retune only its own build.
    baseline = animation(other)
    scene.afterglow.effects.lasers.intensity = 0
    assert animation(other) == baseline
    effects.remove(scene)
    assert set(objects(other)) == other_objects
    assert all(name not in bpy.data.curves and name not in bpy.data.lights for name in first_data)
    assert floor.data.materials[0] == material and helpers.snapshot(material) == nodes
    assert np.allclose(floor.matrix_world, matrix)
    effects.remove(other)
    for store in (bpy.data.objects, bpy.data.curves, bpy.data.lights, bpy.data.materials, bpy.data.actions):
        assert not any(db.get(effects.TAG) for db in store), [db.name for db in store if db.get(effects.TAG)]


helpers.run(globals())
