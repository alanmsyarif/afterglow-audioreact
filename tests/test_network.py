"""Signal Network: connected world-space motion, opt-in graphics and scene ownership."""
import os
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import numpy as np  # noqa: E402
import helpers  # noqa: E402
from helpers import plane  # noqa: E402
from afterglow import effects  # noqa: E402


PULSES = (0.5, 1.5, 2.5)


def track(pulses=PULSES, seconds=4):
    path = os.path.join(tempfile.gettempdir(), f"afterglow_network_{len(pulses)}_{seconds}.wav")
    rate = 44100
    t = np.arange(int(seconds * rate)) / rate
    x = np.zeros_like(t)
    for onset in pulses:
        u = (t - onset) / 0.12
        x += 0.8 * np.where((u >= 0) & (u <= 1), np.sin(np.pi * u) ** 2, 0) * np.sin(2 * np.pi * 80 * t)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    return path


def stage(anchors=True):
    scene = bpy.context.scene
    coll = bpy.data.collections.new("Existing venue")
    scene.collection.children.link(coll)
    floor = plane("Existing floor", coll)
    floor.scale = (4, 4, 1)
    scene.afterglow.collection = coll
    scene.afterglow.sound = bpy.data.sounds.load(track(), check_existing=False)
    scene.render.fps = 24
    fx = scene.afterglow.effects
    fx.signals.enabled = True
    fx.signals.weight = 1.0
    fx.signal_speed = 4.0
    fx.signal_surface = False
    if anchors:
        points = bpy.data.collections.new("My anchors")
        scene.collection.children.link(points)
        for name, p in zip(("A", "B", "C"), ((-2, 0, 2), (0, 2, 3), (2, 0, 1))):
            ob = bpy.data.objects.new(name, None)
            points.objects.link(ob)
            ob.location = p
        fx.signal_anchors = points
    bpy.context.view_layer.update()
    return scene


def at(scene, seconds):
    f = scene.afterglow.start_frame + seconds * scene.render.fps / scene.render.fps_base
    scene.frame_set(int(np.floor(f)), subframe=f % 1.0)


def objects(kind=None):
    return sorted((ob for ob in bpy.data.objects if ob.get(effects.TAG)
                   and ob.name.startswith("AFX_signals_") and (kind is None or ob.type == kind)),
                  key=lambda ob: ob.name)


def test_signal_lights_follow_connected_world_space_paths():
    scene = stage()
    assert not scene.afterglow.effects.signal_graphics  # explicit opt-in, never a new default
    original_meshes, original_curves = set(bpy.data.meshes), set(bpy.data.curves)
    counts = effects.build(scene)
    assert counts["signals"] == len(PULSES), counts
    lights = objects("LIGHT")
    assert len(lights) == effects.POOL["signals"]
    first = effects.analyze(scene)["signals"][0][0]
    at(scene, first + 0.25)  # 4 units/s: one unit along the first orthogonal X segment
    assert np.allclose(lights[0].location, (-1, 0, 2), atol=1e-4), tuple(lights[0].location)
    assert lights[0].data.energy > 0
    at(scene, first + 1.0)  # two units along X, then two along Y, without restarting per object
    assert np.allclose(lights[0].location, (0, 2, 2), atol=1e-4), tuple(lights[0].location)
    assert set(bpy.data.meshes) == original_meshes and set(bpy.data.curves) == original_curves


def test_surface_impact_uses_the_hit_normal_not_a_flat_floor():
    from mathutils import Vector
    scene = stage()
    floor = scene.afterglow.collection.objects["Existing floor"]
    floor.rotation_euler.y = np.pi / 6
    floor.location.z = 1.0
    scene.afterglow.effects.signal_surface = True
    bpy.context.view_layer.update()
    before = floor.matrix_world.copy()
    effects.build(scene)
    at(scene, 30.0)  # endpoints hold after their events
    end = objects("LIGHT")[0].location
    normal = floor.matrix_world.to_quaternion() @ Vector((0, 0, 1))
    clearance = (end - floor.location).dot(normal)
    assert abs(clearance - 0.06) < 1e-4, (tuple(end), clearance)
    assert np.allclose(floor.matrix_world, before)


def test_invalid_anchor_setup_keeps_the_previous_build():
    scene = stage()
    effects.build(scene)
    before = set(objects())
    for ob in list(scene.afterglow.effects.signal_anchors.objects)[1:]:
        bpy.data.objects.remove(ob, do_unlink=True)
    try:
        effects.build(scene)
    except ValueError as e:
        assert "two distinct" in str(e), str(e)
    else:
        raise AssertionError("a one-anchor network must be rejected before removing the last build")
    assert set(objects()) == before


def test_spokes_and_alternating_direction_change_the_actual_routes():
    scene = stage()
    fx = scene.afterglow.effects
    fx.signal_route = "SPOKES"
    effects.build(scene)
    second = effects.analyze(scene)["signals"][1][0]
    at(scene, second + 0.75)
    assert np.allclose(objects("LIGHT")[1].location, (1, 0, 2), atol=1e-4)
    fx.signal_direction = "ALTERNATE"
    effects.build(scene)
    at(scene, second + 0.25)
    assert np.allclose(objects("LIGHT")[1].location, (2, 0, 2), atol=1e-4)  # retrace the same path


def test_opt_in_graphics_follow_the_head_and_reveal_an_arrival_frame():
    scene = stage()
    scene.afterglow.effects.signal_graphics = True
    original_meshes = set(bpy.data.meshes)
    effects.build(scene)
    curves = objects("CURVE")
    assert len(curves) == 6, [ob.name for ob in curves]
    light = objects("LIGHT")[0]
    path = next(ob for ob in light.children if ob.get("afterglow_signal_kind") == "path")
    frame = next(ob for ob in light.children if ob.get("afterglow_signal_kind") == "frame")
    first = effects.analyze(scene)["signals"][0][0]
    at(scene, first + 0.5)
    assert abs(path.data.bevel_factor_end - 2.0 / 11.0) < 1e-4
    assert abs(path.data.bevel_factor_start - (2.0 - 0.75) / 11.0) < 1e-4
    assert path[effects.GLOW] > 0 and frame[effects.GLOW] == 0
    assert np.allclose(path.matrix_world @ path.data.splines[0].points[0].co.xyz, (-2, 0, 2), atol=1e-4)
    at(scene, first + 11.0 / 4.0 + 0.1)
    assert frame[effects.GLOW] > 0
    assert frame.data.bevel_factor_end > 0.9
    at(scene, 20.0)
    assert all(ob[effects.GLOW] == 0 for ob in curves)
    assert set(bpy.data.meshes) == original_meshes  # thin native curves, no solid shaft meshes


def test_graphics_rebuild_and_remove_preserve_user_data():
    scene = stage()
    scene.afterglow.effects.signal_graphics = True
    user_curve = bpy.data.curves.new("My existing curve", "CURVE")
    user_object = bpy.data.objects.new("My existing curve", user_curve)
    scene.afterglow.collection.objects.link(user_object)
    effects.build(scene)
    effects.build(scene)
    assert len([c for c in bpy.data.curves if c.get(effects.TAG)]) == 6
    effects.remove(scene)
    for store in (bpy.data.curves, bpy.data.lights, bpy.data.actions, bpy.data.materials):
        assert not any(db.get(effects.TAG) for db in store), [db.name for db in store if db.get(effects.TAG)]
    assert bpy.data.objects.get(user_object.name) == user_object
    assert bpy.data.curves.get(user_curve.name) == user_curve
    assert len(scene.afterglow.effects.signal_anchors.objects) == 3


def test_network_live_controls_are_independent_of_beam_glow():
    scene = stage()
    fx = scene.afterglow.effects
    fx.signal_graphics = True
    effects.build(scene)
    first = effects.analyze(scene)["signals"][0][0]
    at(scene, first + 0.5)
    light = objects("LIGHT")[0]
    path = next(ob for ob in light.children if ob.get("afterglow_signal_kind") == "path")
    old_energy, old_glow = light.data.energy, path[effects.GLOW]
    fx.signals.intensity = 2.0
    fx.signals.color = (0.2, 0.4, 0.8)
    fx.signal_glow = 0.25
    fx.beam_glow = 3.0
    at(scene, first + 0.5)
    mat = path.data.materials[0]
    assert abs(light.data.energy - 2 * old_energy) < 1e-3
    assert abs(path[effects.GLOW] - 2 * old_glow) < 1e-4
    assert np.allclose(light.data.color, (0.2, 0.4, 0.8))
    assert abs(mat.node_tree.nodes["Glow"].inputs[1].default_value - 7.5) < 1e-4
    assert not effects.outdated(scene)


def test_network_settings_and_anchor_moves_mark_the_build_outdated():
    scene = stage()
    fx = scene.afterglow.effects
    effects.build(scene)
    for name, value in (("signal_speed", 12.0), ("signal_route", "SPOKES"),
                        ("signal_direction", "REVERSE"), ("signal_surface", True),
                        ("signal_graphics", True), ("signal_width", 0.03),
                        ("signal_tail", 1.5), ("signal_frame_size", 1.2)):
        setattr(fx, name, value)
        assert effects.outdated(scene), name
        effects.build(scene)
        assert not effects.outdated(scene), name
    fx.signal_anchors.objects["B"].location.z += 0.5
    bpy.context.view_layer.update()
    assert effects.outdated(scene)


def test_add_anchor_operator_keeps_3d_position_and_preserves_markers_on_remove():
    scene = stage(anchors=False)
    scene.cursor.location = (1.0, 2.0, 3.0)
    assert bpy.ops.afterglow.add_signal_anchor() == {"FINISHED"}
    coll = scene.afterglow.effects.signal_anchors
    anchor = coll.objects[0]
    assert anchor.type == "EMPTY" and np.allclose(anchor.location, (1, 2, 3))
    scene.cursor.location = (-1.0, 2.0, 4.0)
    assert bpy.ops.afterglow.add_signal_anchor() == {"FINISHED"}
    effects.build(scene)
    effects.remove(scene)
    assert len(coll.objects) == 2 and anchor.name in coll.objects


def test_silent_network_has_no_lit_graphics_or_generated_material():
    scene = stage()
    scene.afterglow.sound = bpy.data.sounds.load(track(()), check_existing=False)
    scene.afterglow.effects.signal_graphics = True
    assert effects.build(scene)["signals"] == 0
    assert not objects("CURVE")
    assert not any(mat.get(effects.TAG) for mat in bpy.data.materials)
    for t in (-1, 0, 2, 8):
        at(scene, t)
        assert all(ob.data.energy == 0 for ob in objects("LIGHT"))


def test_busy_signal_pool_cuts_old_frames_instead_of_ghost_arrivals():
    scene = stage()
    scene.afterglow.sound = bpy.data.sounds.load(track(tuple(np.arange(0.5, 4, 0.25)), 5), check_existing=False)
    scene.afterglow.effects.signal_graphics = True
    scene.afterglow.effects.signal_speed = 1.0
    counts = effects.build(scene)
    assert counts["signals"] > 10
    assert len(objects("LIGHT")) == effects.POOL["signals"]
    assert len(objects("CURVE")) == 6
    first = effects.analyze(scene)["signals"][0][0]
    at(scene, first + 11.0 + 0.1)  # the first event would arrive now, but its pool slot was stolen
    frames = [ob for ob in objects("CURVE") if ob.get("afterglow_signal_kind") == "frame"]
    assert all(ob[effects.GLOW] == 0 for ob in frames)
    at(scene, 30)
    assert all(ob[effects.GLOW] == 0 for ob in objects("CURVE"))


def test_other_scene_signal_graphics_survive_removal():
    scene = stage()
    scene.afterglow.effects.signal_graphics = True
    effects.build(scene)
    first_curves = {ob.data for ob in objects("CURVE")}
    other = bpy.data.scenes.new("Other show")
    bpy.context.window.scene = other
    stage().afterglow.effects.signal_graphics = True
    effects.build(other)
    other_curves = {ob.data for coll in effects._collections(other) for ob in coll.all_objects if ob.type == "CURVE"}
    effects.remove(scene)
    assert all(c.name in bpy.data.curves for c in other_curves)
    assert not first_curves.intersection(set(bpy.data.curves))
    effects.remove(other)
    assert not any(c.get(effects.TAG) for c in bpy.data.curves)


def test_shifted_network_finishes_its_route_beyond_the_song_end():
    scene = stage()
    fx = scene.afterglow.effects
    fx.signal_graphics = True
    fx.timing_offset = 2.0
    effects.build(scene)
    last = effects.analyze(scene)["signals"][-1][0]
    at(scene, last + 11.0 / fx.signal_speed + 0.1)
    frame = next(ob for ob in objects("LIGHT")[-1].children if ob.get("afterglow_signal_kind") == "frame")
    assert frame[effects.GLOW] > 0
    at(scene, 20)
    assert frame[effects.GLOW] == 0


helpers.run(globals())
