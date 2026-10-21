"""Beam looks: real silhouettes and seeded per-event variation, not placement jitter."""
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import helpers  # noqa: E402
from afterglow import effects  # noqa: E402
from test_beam_fixes import stage  # noqa: E402


def geometry(tubes):
    return tuple((tuple(tuple(round(c, 6) for c in v) for v in verts), tuple(faces), slot)
                 for verts, faces, slot in tubes)


def test_strike_styles_change_the_actual_silhouette():
    scene = stage("strikes")
    fx = scene.afterglow.effects
    venue = effects.make_venue(scene)
    shapes = []
    for style in ("FILAMENT", "TWIN", "RIBBON"):
        fx.strike_style = style
        tubes = effects._beam_tubes("strikes", venue, random.Random(1), fx=fx)
        shapes.append(geometry(tubes))
        zs = [v[2] for verts, _, _ in tubes for v in verts]
        assert abs(min(zs) + effects._strike_base(venue)) < 1e-5
        assert abs(max(zs) - (venue.span_z - effects._strike_base(venue))) < 1e-5
    assert len(set(shapes)) == 3, "Style must change geometry, not just a UI enum or material name"


def test_strike_width_and_companion_controls_change_the_mesh():
    scene = stage("strikes")
    fx = scene.afterglow.effects
    venue = effects.make_venue(scene)
    original = effects._beam_tubes("strikes", venue, random.Random(1), fx=fx)
    fx.strike_width = 2.0
    fx.strike_companions = 3
    fx.strike_spread = 1.25
    changed = effects._beam_tubes("strikes", venue, random.Random(1), fx=fx)
    radius = lambda tube: max((x * x + y * y) ** 0.5 for x, y, _ in tube[0])
    assert abs(radius(changed[0]) - 2 * radius(original[0])) < 1e-6
    assert len([part for part in changed if part[2] == 2]) == 3
    for verts, _, slot in changed:
        if slot == 2:
            x = sum(v[0] for v in verts) / len(verts)
            y = sum(v[1] for v in verts) / len(verts)
            assert abs((x * x + y * y) ** 0.5 - fx.strike_spread) < 1e-5
    fx.strike_companions = 0
    assert not any(slot == 2 for _, _, slot in effects._beam_tubes("strikes", venue, random.Random(1), fx=fx))


def test_gun_styles_length_width_and_taper_change_actual_geometry():
    scene = stage("gun")
    fx = scene.afterglow.effects
    venue = effects.make_venue(scene)
    shapes = []
    for style in ("TRACER", "DART", "STREAK"):
        fx.gun_style = style
        shapes.append(geometry(effects._beam_tubes("gun", venue, random.Random(1), fx=fx)))
    assert len(set(shapes)) == 3
    fx.gun_style = "TRACER"
    original = effects._beam_tubes("gun", venue, random.Random(1), fx=fx)[0][0]
    fx.gun_length, fx.gun_width, fx.gun_taper = 0.5, 2.0, 0.5
    changed = effects._beam_tubes("gun", venue, random.Random(1), fx=fx)[0][0]
    assert min(v[2] for v in changed) == 0  # never points ahead of its light
    assert abs(max(v[2] for v in changed) - 0.5 * max(v[2] for v in original)) < 1e-6
    assert abs(changed[0][0] - 2 * original[0][0]) < 1e-6  # head width independent of length
    assert abs(changed[-1][0] - original[-1][0]) < 1e-6  # tail width = Width * Taper


def shot_looks(name, amount, seed=20260):
    helpers.fresh()
    scene = stage(name)
    fx = scene.afterglow.effects
    fx.beams, fx.beam_flow, fx.seed = True, 0.0, seed
    fx.strike_variation = fx.gun_variation = amount
    coll = bpy.data.collections.new("Repeated hits")
    scene.collection.children.link(coll)
    effects._build_layer(coll, scene, effects.make_venue(scene), name,
                         [(0.5, 1), (1.5, 1), (2.5, 1)], 24, 1, 120)
    light = next(o for o in coll.objects if o.type == "LIGHT" and o.name.startswith(f"AFX_{name}_0_"))
    shaft = next(c for c in light.children if c.get(effects.TAG))
    looks, locations = [], []
    for t in (0.58, 1.58, 2.58):
        frame = 1 + t * 24
        scene.frame_set(int(frame), subframe=frame % 1)
        looks.append(tuple(round(v, 5) for v in (*shaft.scale, shaft.rotation_euler.z,
                           shaft.get("afterglow_look_phase", 0), shaft.get("afterglow_look_dash", 1))))
        locations.append(tuple(round(v, 5) for v in light.location))
    times = tuple(round(p.co.x, 4) for fc in helpers.fcurves(light.data) for p in fc.keyframe_points)
    return looks, locations, times, len(coll.objects)


def test_reused_pool_slots_get_a_new_seeded_look_per_hit_without_moving_the_beats():
    for name in ("strikes", "gun"):
        uniform = shot_looks(name, 0.0)
        varied = shot_looks(name, 0.8)
        assert len(set(uniform[0])) == 1, (name, uniform[0])
        assert len(set(varied[0])) == 3, (name, varied[0])
        assert uniform[1:] == varied[1:], "Look variation must not change positions, beat times or pool size"
        assert varied == shot_looks(name, 0.8), "Same seed must rebuild the same show"
        assert varied[0] != shot_looks(name, 0.8, seed=100)[0], "Seed must change appearance"


def test_per_hit_breakup_and_tail_fade_reach_the_materials():
    scene = stage("strikes", "gun")
    fx = scene.afterglow.effects
    fx.beams = True
    fx.gun_fade = 3.0
    effects.build(scene)
    for name in ("strikes", "gun"):
        shaft = next(o for o in bpy.data.objects if o.type == "MESH" and o.name.startswith(f"AFX_{name}_"))
        for mat in shaft.data.materials:
            attrs = {n.attribute_name for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeAttribute"}
            want = {"fade"} if name == "gun" else {"phase", "dash", "gaps", "flow"}
            assert {f"afterglow_look_{prop}" for prop in want} <= attrs, (mat.name, attrs)
            if name == "gun":
                assert abs(mat.node_tree.nodes["Tail Fade"].inputs[1].default_value - 3.0) < 1e-6


def test_strike_and_gun_panels_keep_their_own_visible_beam_controls():
    from afterglow import ui
    fx = bpy.context.scene.afterglow.effects
    drawn = set()

    class Layout:
        active = True

        def column(self, *a, **k):
            return self

        row = column

        def prop(self, data, name, **k):
            assert name in data.bl_rna.properties, name
            drawn.add(name)

        def label(self, **k):
            pass

        def separator(self):
            pass

    fx.strikes.visible_beam = fx.gun.visible_beam = True
    ui.draw_strike_beam(Layout(), fx)
    assert {"visible_beam", "beam_glow", "strike_style", "strike_width", "strike_companions",
            "strike_spread", "strike_variation", "beam_walk", "beam_walk_time"} <= drawn, drawn
    assert not any(name.startswith("gun_") for name in drawn), drawn
    drawn.clear()
    ui.draw_gun_beam(Layout(), fx)
    assert {"visible_beam", "beam_glow", "gun_style", "gun_length", "gun_width",
            "gun_taper", "gun_fade", "gun_variation"} <= drawn, drawn
    assert not any(name.startswith("strike_") for name in drawn), drawn
    assert not {"beam_walk", "beam_flow", "beams"} & drawn
    for layer, draw in ((fx.strikes, ui.draw_strike_beam), (fx.gun, ui.draw_gun_beam)):
        layer.visible_beam = False
        drawn.clear()
        draw(Layout(), fx)
        assert drawn == {"visible_beam"}, drawn


def test_all_look_edits_mark_the_build_outdated():
    scene = stage("strikes", "gun")
    fx = scene.afterglow.effects
    fx.beams = True
    effects.build(scene)
    for prop, value in (("strike_style", "TWIN"), ("strike_width", 2.0), ("strike_companions", 0),
                        ("strike_spread", 1.0), ("strike_variation", 0.8), ("gun_style", "DART"),
                        ("gun_length", 0.5), ("gun_width", 2.0), ("gun_taper", 0.5),
                        ("gun_fade", 4.0), ("gun_variation", 0.8)):
        original = getattr(fx, prop)
        setattr(fx, prop, value)
        assert effects.outdated(scene), prop
        assert not fx.bl_rna.properties[prop].is_animatable, prop
        setattr(fx, prop, original)
        assert not effects.outdated(scene), prop


def test_event_phase_changes_the_rendered_breakup_not_just_a_property():
    from mathutils import Vector
    import numpy as np
    for engine in ("BLENDER_EEVEE", "CYCLES"):
        helpers.fresh()
        scene = stage("strikes")
        coll = bpy.data.collections.new("Breakup proof")
        scene.collection.children.link(coll)
        light = effects._make_unit(coll, "strikes", 0, (1, 1, 1), beams=True)[0]
        mat = effects._beam_material("strikes", "filament", (1, 1, 1), 0.25, vary=True)
        shaft = effects._make_shaft(coll, light, [effects._tube(((-3, 0.04), (3, 0.04)), 0)], [mat])
        shaft[effects.GLOW] = 1.0
        cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
        cam.location = (0, -12, 0)
        cam.rotation_euler = (Vector() - cam.location).to_track_quat('-Z', 'Y').to_euler()
        cam.data.type, cam.data.ortho_scale = "ORTHO", 8
        scene.collection.objects.link(cam)
        scene.camera = cam
        scene.render.engine = engine
        scene.render.resolution_x, scene.render.resolution_y = 96, 256
        scene.eevee.taa_render_samples = 16
        if engine == "CYCLES":
            scene.cycles.samples = 16
        scene.world = bpy.data.worlds.new("Black")
        next(n for n in scene.world.node_tree.nodes if n.type == "BACKGROUND").inputs["Strength"].default_value = 0
        out = os.path.join(os.environ.get("TMPDIR", tempfile.gettempdir()), "afterglow-beam-looks")
        os.makedirs(out, exist_ok=True)

        def pixels(phase):
            shaft["afterglow_look_phase"] = phase
            scene.render.filepath = os.path.join(out, f"breakup-{engine}-{phase}.png")
            bpy.ops.render.render(write_still=True)
            image = bpy.data.images.load(scene.render.filepath, check_existing=False)
            data = np.asarray(image.pixels[:]).reshape(-1, 4)[:, :3]
            bpy.data.images.remove(image)
            return data

        first, changed = pixels(0), pixels(23)
        assert first.max() > 0.1 and changed.max() > 0.1, engine
        assert np.max(np.abs(first - changed)) > 0.1, engine
        assert np.mean(np.abs(first - changed)) > 0.0001, engine
        assert np.array_equal(first, pixels(0)), "Seeded appearance must not flicker on a re-render"
        shaft[effects.GLOW] = 0
        assert pixels(0).max() < 0.02, "Zero glow must remain transparent/dark"


def test_dense_look_animation_is_held_per_event_and_removed_safely():
    scene = stage("strikes", "gun")
    fx = scene.afterglow.effects
    fx.beams, fx.strike_style, fx.gun_style = True, "TWIN", "DART"
    fx.strikes.timing = fx.gun.timing = 4.0
    fx.beam_walk_time = 1.0
    coll = bpy.data.collections.new(effects.COLLECTION)
    coll[effects.TAG] = effects.VERSION
    scene.collection.children.link(coll)
    before = set(scene.afterglow.collection.all_objects)
    events = [(0.5 + i * 0.2, 1) for i in range(20)]
    for name in ("strikes", "gun"):
        effects._build_layer(coll, scene, effects.make_venue(scene), name, events, 24, 1, 200)
    shafts = [o for o in coll.objects if o.type == "MESH"]
    assert len(shafts) == effects.POOL["strikes"] + effects.POOL["gun"]
    for shaft in shafts:
        curves = [fc for fc in helpers.fcurves(shaft) if fc.data_path.startswith('["afterglow_look_') or fc.data_path == "scale"]
        assert curves and all(k.interpolation == "CONSTANT" for fc in curves for k in fc.keyframe_points)
        glow = next(fc for fc in helpers.fcurves(shaft) if fc.data_path == effects.GLOW_PATH)
        transitions = [k.co.x for fc in curves for k in list(fc.keyframe_points)[1:]
                       if any(abs(k.co.y - p.co.y) > 1e-5 for p in list(fc.keyframe_points)[:1])]
        assert transitions
        scene.frame_set(250)
        assert shaft[effects.GLOW] == 0 and glow.evaluate(250) == 0
    effects.remove(scene)
    assert set(scene.afterglow.collection.all_objects) == before
    for store in (bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.actions):
        assert not any(db.get(effects.TAG) for db in store)


def test_seeded_look_channels_survive_save_and_reopen():
    from pathlib import Path
    from afterglow import looks
    shot_looks("strikes", 0.8)

    def samples():
        scene = bpy.context.scene
        shaft = bpy.data.objects["AFX_strikes_0_shaft"]
        result = []
        for time in (0.58, 1.58, 2.58):
            frame = 1 + time * 24
            scene.frame_set(int(frame), subframe=frame % 1)
            result.append(tuple(round(x, 6) for x in (*shaft.scale, shaft.rotation_euler.z,
                          *(shaft[f"afterglow_look_{p}"] for p in looks.ATTRS))))
        return result

    before = samples()
    path = Path(os.environ.get("TMPDIR", tempfile.gettempdir())) / "afterglow-beam-looks" / "saved-looks.blend"
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    assert samples() == before


if __name__ == "__main__":
    helpers.run(globals())
