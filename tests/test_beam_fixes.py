"""Regression coverage for strike envelopes, visible beams, and gun tracers."""
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import helpers  # noqa: E402
from afterglow import effects  # noqa: E402


def stage(*layers):
    scene = bpy.context.scene
    coll = bpy.data.collections.new("Venue")
    scene.collection.children.link(coll)
    for x in (-4.0, 4.0):
        helpers.plane("floor", coll).location.x = x
    bpy.context.view_layer.update()
    scene.afterglow.collection = coll
    scene.afterglow.sound = helpers.sound()
    scene.render.fps = 24
    for name in layers:
        layer = getattr(scene.afterglow.effects, name)
        layer.enabled, layer.weight = True, 1.0
    return scene


def test_short_effects_keep_their_attack_inside_the_envelope():
    """Small Timing values must not sort the attack after the hold or dark key."""
    for name in effects.DUR:
        for timing in (0.05, 0.1, 0.25, 1.0, 4.0):
            dur = effects.DUR[name] * timing
            env = effects._envelope(1.0, dur, effects.ATTACK[name], effects.HOLD[name])
            assert all(a[0] <= b[0] for a, b in zip(env, env[1:])), (name, timing, env)
            assert env[0] == (1.0, 0.0) and env[-1] == (1.0 + dur, 0.0)
            assert max(v for _, v in env) == 1.0


def test_gun_tracer_is_behind_the_light_not_in_front_of_it():
    scene = stage("gun")
    venue = effects.make_venue(scene)
    coll = bpy.data.collections.new("Tracer test")
    scene.collection.children.link(coll)
    light = effects._make_unit(coll, "gun", 0, (1, 1, 1), beams=True)[0]
    mat = effects._beam_material("gun", "tracer", (1, 1, 1), 1.0)
    shaft = effects._make_shaft(coll, light, effects._beam_tubes("gun", venue, random.Random(0)), [mat])
    # A Blender spot fires along local -Z. Its visible tail must trail toward +Z.
    zs = [v.co.z for v in shaft.data.vertices]
    assert min(zs) >= -1e-6 and max(zs) > 0.5 * venue.radius, (min(zs), max(zs))


def gun_tail_profile(engine):
    from mathutils import Vector
    scene = stage("gun")
    venue = effects.make_venue(scene)
    coll = bpy.data.collections.new("Tracer visibility test")
    scene.collection.children.link(coll)
    light = effects._make_unit(coll, "gun", 0, (1, 1, 1), beams=True)[0]
    mat = effects._beam_material("gun", "tracer", (1, 1, 1), 1.0)
    shaft = effects._make_shaft(coll, light, effects._beam_tubes("gun", venue, random.Random(0)), [mat])
    shaft[effects.GLOW] = 1.0
    # Keep the light at zero power: these pixels must come from the tracer, not spill.
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.location = (0, -3 * venue.radius, 0)
    cam.rotation_euler = (Vector() - cam.location).to_track_quat('-Z', 'Y').to_euler()
    cam.data.type, cam.data.ortho_scale = "ORTHO", 2.4 * venue.radius
    scene.collection.objects.link(cam)
    scene.camera = cam
    scene.world = bpy.data.worlds.new("Black world")
    next(n for n in scene.world.node_tree.nodes if n.type == "BACKGROUND").inputs["Strength"].default_value = 0.0
    scene.render.engine = engine
    if engine == "CYCLES":
        scene.cycles.samples = 16
    scene.render.resolution_x = scene.render.resolution_y = 128
    scene.eevee.taa_render_samples = 16
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.exposure = -3.0  # compare the fade without clipping the bright head
    out = os.path.join(os.environ.get("TMPDIR", tempfile.gettempdir()), "afterglow-beam-fixes")
    os.makedirs(out, exist_ok=True)
    scene.render.filepath = os.path.join(out, f"gun-tail-{engine}.png")
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(scene.render.filepath, check_existing=False)
    w, h = image.size
    px = image.pixels[:]
    bpy.data.images.remove(image)

    def peak(lo, hi):
        return max(px[(y * w + x) * 4] for y in range(int(lo * h), int(hi * h)) for x in range(w))

    return peak(0.54, 0.68), peak(0.82, 0.91), peak(0.05, 0.48)


def test_gun_tracer_renders_a_fading_tail_behind_the_head():
    for engine in ("BLENDER_EEVEE", "CYCLES"):
        helpers.fresh()
        near, far, front = gun_tail_profile(engine)
        assert near > 0.3 and near > 1.4 * far and front < 0.02, (engine, near, far, front)


def test_zero_built_intensity_can_be_restored_live():
    scene = stage("strikes", "gun")
    fx = scene.afterglow.effects
    fx.beams, fx.beam_walk = True, "OFF"
    fx.strikes.intensity = fx.gun.intensity = 0.0
    counts = effects.build(scene)
    assert counts["strikes"] > 0 and counts["gun"] > 0, counts
    objects = set(bpy.data.objects)

    def peaks(name):
        lights = [o for o in bpy.data.objects if o.type == "LIGHT" and o.name.startswith(f"AFX_{name}_")]
        shafts = [c for o in lights for c in o.children if c.get(effects.TAG)]
        energy = max(p.co.y for o in lights for fc in helpers.fcurves(o.data) for p in fc.keyframe_points)
        glow = max(p.co.y for o in shafts for fc in helpers.fcurves(o)
                   if fc.data_path == effects.GLOW_PATH for p in fc.keyframe_points)
        return energy, glow

    for name in ("strikes", "gun"):
        assert peaks(name) == (0.0, 0.0), (name, peaks(name))
        layer = getattr(fx, name)
        layer.intensity = 2.0
        full = peaks(name)
        assert full[0] > 100 and full[1] > 0.1, (name, full)
        layer.intensity = 0.0
        assert peaks(name) == (0.0, 0.0), (name, peaks(name))
        layer.intensity = 0.5
        assert all(abs(a - 0.25 * b) < 1e-4 for a, b in zip(peaks(name), full)), (name, peaks(name), full)
    assert set(bpy.data.objects) == objects
    assert not effects.outdated(scene)


def test_zero_intensity_recovery_is_shared_by_all_effect_layers():
    scene = stage(*effects.LAYERS)
    fx = scene.afterglow.effects
    fx.beams = fx.signal_graphics = True
    for name in effects.LAYERS:
        getattr(fx, name).intensity = 0.0
    counts = effects.build(scene)
    for name in effects.LAYERS:
        assert counts[name] > 0, (name, counts)
        lights = [o for o in bpy.data.objects if o.type == "LIGHT" and o.name.startswith(f"AFX_{name}_")]
        shafts = [c for o in lights for c in o.children if c.get(effects.TAG)]
        assert all(p.co.y == 0 for o in lights for fc in helpers.fcurves(o.data) for p in fc.keyframe_points), name
        getattr(fx, name).intensity = 1.0
        assert max(p.co.y for o in lights for fc in helpers.fcurves(o.data) for p in fc.keyframe_points) > 10, name
        if shafts:
            assert max(p.co.y for o in shafts for fc in helpers.fcurves(o)
                       if fc.data_path == effects.GLOW_PATH for p in fc.keyframe_points) > 0.1, name


def test_zero_intensity_can_be_restored_after_saving_and_reopening():
    scene = stage("strikes", "gun")
    fx = scene.afterglow.effects
    fx.beams = True
    fx.strikes.intensity = fx.gun.intensity = 0.0
    effects.build(scene)
    out = os.path.join(os.environ.get("TMPDIR", tempfile.gettempdir()), "afterglow-beam-fixes")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "zero-intensity.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    fx = bpy.context.scene.afterglow.effects
    fx.strikes.intensity = fx.gun.intensity = 1.0
    for name in ("strikes", "gun"):
        lights = [o for o in bpy.data.objects if o.type == "LIGHT" and o.name.startswith(f"AFX_{name}_")]
        assert max(p.co.y for o in lights for fc in helpers.fcurves(o.data) for p in fc.keyframe_points) > 100, name


def test_gun_tail_stays_behind_the_real_animated_motion():
    from mathutils import Vector
    scene = stage("gun")
    scene.afterglow.effects.beams = True
    effects.build(scene)
    venue = effects.make_venue(scene)
    light = bpy.data.objects["AFX_gun_0_light"]
    shaft = next(c for c in light.children if c.get(effects.TAG))
    motion = next(fc for fc in helpers.fcurves(light) if fc.data_path == "location")
    f = motion.keyframe_points[0].co.x + 0.25 * effects.DUR["gun"] * scene.render.fps
    scene.frame_set(int(f), subframe=f % 1)
    previous = light.matrix_world.translation.copy()
    f += 0.02 * scene.render.fps
    scene.frame_set(int(f), subframe=f % 1)
    head = light.matrix_world.translation.copy()
    tail = shaft.matrix_world @ Vector((0, 0, effects.GUN_TRACER * venue.radius))
    direction = (head - previous).normalized()
    assert (tail - head).dot(direction) < -0.9 * effects.GUN_TRACER * venue.radius
    assert (light.matrix_world.to_3x3() @ Vector((0, 0, -1))).dot(direction) > 0.99


def test_unlit_visible_beams_leave_the_background_unchanged():
    from mathutils import Vector
    import numpy as np
    for engine in ("BLENDER_EEVEE", "CYCLES"):
        helpers.fresh()
        scene = stage("strikes", "gun")
        scene.afterglow.effects.beams = True
        effects.build(scene)
        shafts = [o for o in bpy.data.objects if o.type == "MESH" and o.get(effects.TAG)]
        backdrop = helpers.plane("Untouched background", material=helpers.emission_material(
            "Background", strength=0.2, color=(1, 1, 1, 1)))
        backdrop.location, backdrop.rotation_euler.x, backdrop.scale = (0, 20, 4), 1.5707963267948966, (20, 20, 20)
        cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
        cam.location = (0, -20, 4)
        cam.rotation_euler = (Vector((0, 0, 4)) - cam.location).to_track_quat('-Z', 'Y').to_euler()
        cam.data.type, cam.data.ortho_scale = "ORTHO", 20
        scene.collection.objects.link(cam)
        scene.camera = cam
        scene.render.engine = engine
        scene.render.resolution_x = scene.render.resolution_y = 64
        scene.eevee.taa_render_samples = 16
        if engine == "CYCLES":
            scene.cycles.samples = 16
        scene.frame_set(-100)
        out = os.path.join(os.environ.get("TMPDIR", tempfile.gettempdir()), "afterglow-beam-fixes")
        os.makedirs(out, exist_ok=True)
        scene.render.filepath = os.path.join(out, f"dark-beams-{engine}.png")

        def pixels(hidden):
            for shaft in shafts:
                shaft.hide_render = hidden
            bpy.ops.render.render(write_still=True)
            image = bpy.data.images.load(scene.render.filepath, check_existing=False)
            values = np.asarray(image.pixels[:])
            bpy.data.images.remove(image)
            return values

        baseline, unlit = pixels(True), pixels(False)
        assert baseline[0::4].mean() > 0.1
        assert np.max(np.abs(unlit - baseline)) < 0.02, engine


if __name__ == "__main__":
    helpers.run(globals())
