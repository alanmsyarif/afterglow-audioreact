import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import helpers  # noqa: E402
from helpers import at, attr, emission_material, plane, snapshot, sound  # noqa: E402
from afterglow import core, materials, nodes  # noqa: E402


def stage():
    """Collection 'Lights' with 3 emissive planes, 1 non-emissive plane, 1 empty."""
    scene = bpy.context.scene
    coll = bpy.data.collections.new("Lights")
    scene.collection.children.link(coll)
    for name in ("a_bass", "b_mid", "c_treble"):
        plane(name, coll, emission_material("mat_" + name))
    plane("d_plain", coll, bpy.data.materials.new("mat_plain"))
    coll.objects.link(bpy.data.objects.new("e_empty", None))
    s = scene.afterglow
    s.collection = coll
    s.sound = sound()
    return scene, coll


def band_of(name):
    mod = core.find_modifier(bpy.data.objects[name])
    return nodes.get_input(mod, "Low"), nodes.get_input(mod, "High")


def test_apply_requires_collection_and_sound():
    scene = bpy.context.scene
    for expected in ("Pick a collection first", "Pick a sound file first"):
        try:
            core.apply(scene)
        except ValueError as e:
            assert str(e) == expected, str(e)
        else:
            raise AssertionError("no ValueError")
        scene.afterglow.collection = bpy.data.collections.new("C")


def test_apply_spreads_and_skips():
    scene, _ = stage()
    applied, skipped, outside, linked = core.apply(scene)
    assert linked == []
    assert [o.name for o in applied] == ["a_bass", "b_mid", "c_treble"]
    assert [o.name for o in skipped] == ["d_plain"]
    assert outside == []
    assert math.isclose(band_of("a_bass")[0], 20.0, rel_tol=1e-4)
    assert math.isclose(band_of("c_treble")[1], 16000.0, rel_tol=1e-4)
    assert math.isclose(band_of("a_bass")[1], band_of("b_mid")[0], rel_tol=1e-4)
    assert core.find_modifier(bpy.data.objects["d_plain"]) is None
    assert materials.is_patched(bpy.data.materials["mat_a_bass"])
    assert not materials.is_patched(bpy.data.materials["mat_plain"])


def test_end_to_end_levels():
    scene, _ = stage()
    core.apply(scene)
    a, c = bpy.data.objects["a_bass"], bpy.data.objects["c_treble"]
    at(13)
    assert attr(a) > 0.3 and attr(c) < 0.05, (attr(a), attr(c))
    at(61)
    assert attr(c) > 0.3 and attr(a) < 0.05, (attr(a), attr(c))


def test_reapply_no_duplicates():
    scene, _ = stage()
    core.apply(scene)
    counts = {m.name: len(m.node_tree.nodes) for m in bpy.data.materials}
    core.apply(scene)
    for name in ("a_bass", "b_mid", "c_treble"):
        mods = [m for m in bpy.data.objects[name].modifiers if m.type == "NODES"]
        assert len(mods) == 1, (name, len(mods))
    assert counts == {m.name: len(m.node_tree.nodes) for m in bpy.data.materials}


def test_locked_band_survives():
    scene, _ = stage()
    core.apply(scene)
    b = bpy.data.objects["b_mid"]
    b.afterglow.locked = True
    mod = core.find_modifier(b)
    nodes.set_input(mod, "Low", 500.0)
    nodes.set_input(mod, "High", 600.0)
    core.apply(scene)
    assert band_of("b_mid") == (500.0, 600.0)
    assert all(math.isclose(x, y, rel_tol=1e-4) for x, y in zip(band_of("a_bass"), core.band(0, 2)))
    assert all(math.isclose(x, y, rel_tol=1e-4) for x, y in zip(band_of("c_treble"), core.band(1, 2)))


def test_sync_start_frame_and_sound():
    scene, _ = stage()
    core.apply(scene)
    scene.afterglow.start_frame = 25
    for name in ("a_bass", "b_mid", "c_treble"):
        assert nodes.get_input(core.find_modifier(bpy.data.objects[name]), "Start Frame") == 25


def test_remove_restores():
    scene, coll = stage()
    before = {m.name: snapshot(m) for m in bpy.data.materials}
    core.apply(scene)
    core.remove(scene)
    assert before == {m.name: snapshot(m) for m in bpy.data.materials}
    assert all(core.find_modifier(o) is None for o in coll.all_objects)
    core.remove(scene)  # second remove is a no-op


def test_shared_material_outside_reported():
    scene, _ = stage()
    plane("outsider", None, bpy.data.materials["mat_a_bass"])
    _, _, outside, _ = core.apply(scene)
    assert [o.name for o in outside] == ["outsider"]


def test_nested_collection_included():
    scene, coll = stage()
    child = bpy.data.collections.new("Child")
    coll.children.link(child)
    plane("f_child", child, emission_material("mat_f"))
    applied, _, _, _ = core.apply(scene)
    assert "f_child" in [o.name for o in applied]


def test_multi_material_object():
    scene, coll = stage()
    plain = bpy.data.materials.new("mat_plain2")
    before = snapshot(plain)
    ob = plane("g_multi", coll, emission_material("mat_g"))
    ob.data.materials.append(plain)
    core.apply(scene)
    assert materials.is_patched(bpy.data.materials["mat_g"])
    assert snapshot(plain) == before



def test_renamed_group_not_duplicated():
    scene, _ = stage()
    core.apply(scene)
    nodes.get_group().name = "My Audio"
    core.apply(scene)
    for name in ("a_bass", "b_mid", "c_treble"):
        mods = [m for m in bpy.data.objects[name].modifiers if m.type == "NODES"]
        assert len(mods) == 1, (name, len(mods))
    geo = [g for g in bpy.data.node_groups if g.bl_idname == "GeometryNodeTree"]  # the shader Wave group is separate
    assert len(geo) == 1, [g.name for g in geo]


def test_remove_after_collection_switch():
    scene, coll = stage()
    before = {m.name: snapshot(m) for m in bpy.data.materials}
    core.apply(scene)
    scene.afterglow.collection = bpy.data.collections.new("Other")
    core.remove(scene)
    assert all(core.find_modifier(o) is None for o in coll.all_objects)
    assert before == {m.name: snapshot(m) for m in bpy.data.materials}


def test_remove_and_sync_reach_object_moved_out():
    scene, coll = stage()
    core.apply(scene)
    a = bpy.data.objects["a_bass"]
    coll.objects.unlink(a)
    scene.collection.objects.link(a)
    scene.afterglow.start_frame = 25
    assert nodes.get_input(core.find_modifier(a), "Start Frame") == 25
    core.remove(scene)
    assert core.find_modifier(a) is None
    assert not materials.is_patched(bpy.data.materials["mat_a_bass"])


def test_linked_material_not_patched():
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "afterglow_lib.blend")
    lib_mat = emission_material("libglow")
    bpy.data.libraries.write(path, {lib_mat})
    bpy.data.materials.remove(lib_mat)
    with bpy.data.libraries.load(path, link=True) as (_, dst):
        dst.materials = ["libglow"]
    linked_mat = dst.materials[0]
    scene, coll = stage()
    plane("h_linked", coll, linked_mat)
    applied, skipped, _, linked = core.apply(scene)
    assert linked == [linked_mat]
    assert "h_linked" in [o.name for o in skipped]
    assert not materials.is_patched(linked_mat)


def test_calibrate_normalizes_each_band():
    scene, _ = stage()
    core.apply(scene)
    a, c = bpy.data.objects["a_bass"], bpy.data.objects["c_treble"]
    nodes.set_input(core.find_modifier(a), "Decay", 0.2)
    done = core.calibrate(scene, samples=48)
    assert math.isclose(nodes.get_input(core.find_modifier(a), "Decay"), 0.2, rel_tol=1e-6)
    assert {o.name for o in done} == {"a_bass", "b_mid", "c_treble"}
    for ob in (a, c):  # each tone fills 1/3 of the file: median ~0, p90 ~tone level
        mod = core.find_modifier(ob)
        assert nodes.get_input(mod, "Threshold") < 0.05, nodes.get_input(mod, "Threshold")
        assert 0.5 < nodes.get_input(mod, "Gain") < 5.0, nodes.get_input(mod, "Gain")
    at(13)  # bass playing
    assert attr(a) > 0.9 and attr(c) < 0.05, (attr(a), attr(c))
    at(61)  # treble playing
    assert attr(c) > 0.9 and attr(a) < 0.05, (attr(a), attr(c))
    assert scene.frame_current == 61


def test_calibrate_requires_applied_objects():
    scene, _ = stage()
    try:
        core.calibrate(scene)
    except ValueError as e:
        assert str(e) == "Apply first", str(e)
    else:
        raise AssertionError("no ValueError")


def our_strips(scene):
    return [st for st in scene.sequence_editor.strips_all if st.name.startswith(core.STRIP_NAME)]


def test_apply_sets_up_playback():
    scene, _ = stage()
    scene.frame_end = 10
    core.apply(scene)
    (strip,) = our_strips(scene)
    assert strip.sound == scene.afterglow.sound
    assert strip.frame_start == 1
    assert scene.sync_mode == "AUDIO_SYNC"
    assert scene.frame_end == 72, scene.frame_end  # 3 s at 24 fps from frame 1


def test_start_frame_moves_strip_without_duplicates():
    scene, _ = stage()
    core.apply(scene)
    scene.afterglow.start_frame = 25
    core.apply(scene)
    (strip,) = our_strips(scene)
    assert strip.frame_start == 25


def test_remove_deletes_only_our_strip():
    scene, _ = stage()
    se = scene.sequence_editor_create()
    mine = se.strips.new_sound("Music", helpers.tone_wav(), channel=1, frame_start=1)
    core.apply(scene)
    assert our_strips(scene)
    core.remove(scene)
    assert not our_strips(scene)
    assert se.strips.get("Music") == mine


def ripple_position(name):
    return nodes.get_input(core.find_modifier(bpy.data.objects[name]), "Ripple Position")


def test_place_orders_by_location_on_longest_axis():
    scene, _ = stage()
    for name, y in (("a_bass", 10.0), ("b_mid", 0.0), ("c_treble", 5.0)):
        bpy.data.objects[name].location = (0.3, y, 0.0)
    core.apply(scene)
    assert [round(ripple_position(n), 3) for n in ("a_bass", "b_mid", "c_treble")] == [1.0, 0.0, 0.5]


def test_place_coincident_objects_all_zero():
    scene, _ = stage()
    core.apply(scene)
    assert all(ripple_position(n) == 0.0 for n in ("a_bass", "b_mid", "c_treble"))


def test_sync_ripple_settings():
    scene, _ = stage()
    core.apply(scene)
    scene.afterglow.ripple = True
    scene.afterglow.beats_per_sweep = 2.0
    for name in ("a_bass", "b_mid", "c_treble"):
        mod = core.find_modifier(bpy.data.objects[name])
        assert nodes.get_input(mod, "Ripple") is True
        assert nodes.get_input(mod, "Beats per Sweep") == 2.0


def test_apply_refuses_missing_sound_file():
    # Blender 5.2 crashes evaluating Sample Sound on a missing file, so Apply must stop first.
    scene, coll = stage()
    scene.afterglow.sound.filepath = "//gone.wav"
    try:
        core.apply(scene)
    except ValueError as e:
        assert str(e).startswith("Sound file not found"), str(e)
    else:
        raise AssertionError("no ValueError")
    assert all(core.find_modifier(o) is None for o in coll.all_objects)


def test_calibrate_with_ripple_fills_ripple_pair():
    scene, _ = stage()
    core.apply(scene)
    a = core.find_modifier(bpy.data.objects["a_bass"])
    nodes.set_input(a, "Gain", 7.0)
    scene.afterglow.ripple = True
    core.calibrate(scene, samples=24)
    assert nodes.get_input(a, "Gain") == 7.0
    assert nodes.get_input(a, "Ripple Gain") != 1.0


def test_sync_before_apply_on_old_file():
    scene, _ = stage()
    core.apply(scene)
    helpers.v1_group()  # the file was saved by v0.2
    scene.afterglow.start_frame = 5
    for name in ("a_bass", "b_mid", "c_treble"):
        assert nodes.get_input(core.find_modifier(bpy.data.objects[name]), "Start Frame") == 5


def place_at(locs):
    for name, loc in locs.items():
        bpy.data.objects[name].location = loc


def positions():
    return [round(ripple_position(n), 3) for n in ("a_bass", "b_mid", "c_treble")]


def test_ripple_from_explicit_axis():
    scene, _ = stage()
    place_at({"a_bass": (0, 10, 0), "b_mid": (10, 0, 0), "c_treble": (5, 5, 0)})  # X and Y tie
    scene.afterglow.ripple_from = "Y"
    core.apply(scene)
    assert positions() == [1.0, 0.0, 0.5], positions()


def test_ripple_from_empty_is_radial():
    scene, _ = stage()
    origin = bpy.data.objects.new("origin", None)
    scene.collection.objects.link(origin)
    origin.location = (-1, 0, 0)
    place_at({"a_bass": (4, 0, 0), "b_mid": (0, 0, 0), "c_treble": (-1, 2, 0)})  # distances 5, 1, 2
    scene.afterglow.ripple_from = "EMPTY"
    scene.afterglow.ripple_origin = origin
    core.apply(scene)
    assert positions() == [1.0, 0.0, 0.25], positions()


def test_ripple_from_collection_center():
    scene, _ = stage()
    place_at({"a_bass": (-4, 0, 0), "b_mid": (0, 0, 0), "c_treble": (4, 0, 0)})
    scene.afterglow.ripple_from = "CENTER"
    core.apply(scene)
    assert positions() == [1.0, 0.0, 1.0], positions()


def test_ripple_reverse():
    scene, _ = stage()
    place_at({"a_bass": (0, 10, 0), "b_mid": (0, 0, 0), "c_treble": (0, 5, 0)})
    scene.afterglow.ripple_reverse = True
    core.apply(scene)
    assert positions() == [0.0, 1.0, 0.5], positions()


def test_ripple_from_change_replaces_and_rebakes():
    from afterglow import bake
    scene, _ = stage()
    place_at({"a_bass": (0, 10, 0), "b_mid": (10, 0, 0), "c_treble": (5, 5, 0)})
    core.apply(scene)
    scene.afterglow.fast_playback = True
    scene.afterglow.ripple_from = "Y"
    assert positions() == [1.0, 0.0, 0.5], positions()
    assert bake.outdated(scene) == []


def test_empty_mode_without_origin_errors():
    scene, coll = stage()
    scene.afterglow.ripple = True
    scene.afterglow.ripple_from = "EMPTY"
    try:
        core.apply(scene)
    except ValueError as e:
        assert "Origin" in str(e), str(e)
    else:
        raise AssertionError("no ValueError")
    assert all(core.find_modifier(o) is None for o in coll.all_objects)

def bar(name, coll, x0, x1, n=11):
    me = bpy.data.meshes.new(name)
    me.from_pydata([(x0 + (x1 - x0) * i / (n - 1), 0.0, 0.0) for i in range(n)], [], [])
    me.materials.append(emission_material("mat_" + name))
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


def bars_stage(specs, **settings):
    scene = bpy.context.scene
    coll = bpy.data.collections.new("Bars")
    scene.collection.children.link(coll)
    for name, x0, x1 in specs:
        bar(name, coll, x0, x1)
    s = scene.afterglow
    s.collection = coll
    s.sound = sound()
    for k, v in settings.items():
        setattr(s, k, v)
    core.apply(scene)
    return scene


def tap_range(name):
    mod = core.find_modifier(bpy.data.objects[name])
    return round(nodes.get_input(mod, "Tap Min"), 4), round(nodes.get_input(mod, "Tap Max"), 4)


def test_wave_axis_ranges():
    bars_stage([("long", 0.0, 10.0), ("small", 20.0, 21.0)], ripple_from="X", through=True)
    mod = core.find_modifier(bpy.data.objects["long"])
    assert tuple(nodes.get_input(mod, "Wave Axis")) == (1.0, 0.0, 0.0)
    assert (nodes.get_input(mod, "Wave Lo"), nodes.get_input(mod, "Wave Span")) == (0.0, 21.0)
    assert tap_range("long") == (0.0, round(10 / 21, 4)) and tap_range("small") == (round(20 / 21, 4), 1.0)


def test_wave_radial_ranges():
    scene = bpy.context.scene
    origin = bpy.data.objects.new("origin", None)
    scene.collection.objects.link(origin)
    bars_stage([("near", 2.0, 4.0), ("far", 6.0, 8.0)], ripple_from="EMPTY", ripple_origin=origin, through=True)
    mod = core.find_modifier(bpy.data.objects["near"])
    assert nodes.get_input(mod, "Wave Radial") is True
    assert tap_range("near") == (0.0, round(2 / 6, 4)) and tap_range("far") == (round(4 / 6, 4), 1.0)


def test_wave_reverse_flips_ranges():
    bars_stage([("long", 0.0, 10.0), ("small", 20.0, 21.0)], ripple_from="X", ripple_reverse=True, through=True)
    assert tap_range("long") == (round(1 - 10 / 21, 4), 1.0) and tap_range("small") == (0.0, round(1 / 21, 4))


def test_through_toggle_rewaves():
    scene = bars_stage([("long", 0.0, 10.0), ("small", 20.0, 21.0)], ripple_from="X")
    scene.afterglow.through = True
    assert tap_range("long") == (0.0, round(10 / 21, 4))
    assert nodes.get_input(core.find_modifier(bpy.data.objects["long"]), "Through") is True

def test_calibrate_ripple_shares_gain():
    scene = bars_stage([("a", 0.0, 10.0), ("b", 10.0, 20.0), ("c", 20.0, 30.0)], ripple_from="X")
    for name, x in (("a", 5.0), ("b", 15.0), ("c", 25.0)):  # origins at the bar centres
        bpy.data.objects[name].location.x = 0.0
        bpy.data.objects[name].data.transform(__import__("mathutils").Matrix.Translation((-x, 0, 0)))
        bpy.data.objects[name].location.x = x
    scene.afterglow.ripple = True
    core.apply(scene)
    core.calibrate(scene, samples=24)
    pairs = {(round(nodes.get_input(core.find_modifier(bpy.data.objects[n]), "Ripple Gain"), 6),
              round(nodes.get_input(core.find_modifier(bpy.data.objects[n]), "Ripple Threshold"), 6))
             for n in ("a", "b", "c")}
    assert len(pairs) == 1, pairs  # one shared pair: seams can't jump


def test_wave_longest_axis_from_vertices():
    scene = bpy.context.scene
    coll = bpy.data.collections.new("One")
    scene.collection.children.link(coll)
    me = bpy.data.meshes.new("ybar")
    me.from_pydata([(0.0, float(y), 0.0) for y in range(31)], [], [])  # 30 units along Y, origin at 0
    me.materials.append(emission_material("mat_ybar"))
    coll.objects.link(bpy.data.objects.new("ybar", me))
    s = scene.afterglow
    s.collection, s.sound, s.through = coll, sound(), True
    core.apply(scene)
    mod = core.find_modifier(bpy.data.objects["ybar"])
    assert tuple(nodes.get_input(mod, "Wave Axis")) == (0.0, 1.0, 0.0), tuple(nodes.get_input(mod, "Wave Axis"))
    assert nodes.get_input(mod, "Wave Span") == 30.0


def test_through_toggle_switches_material_layout():
    scene = bars_stage([("long", 0.0, 10.0)], ripple_from="X")
    mat = bpy.data.materials["mat_long"]

    def groups():
        return [n.node_tree for n in mat.node_tree.nodes if n.get(materials.TAG) and n.bl_idname == "ShaderNodeGroup"]

    assert groups() == [materials.level_group()]
    scene.afterglow.through = True
    assert groups() == [materials.wave_group()]
    scene.afterglow.through = False
    assert groups() == [materials.level_group()]

def test_meter_object_props_reach_modifier():
    scene, _ = stage()
    core.apply(scene)
    a = bpy.data.objects["a_bass"]
    a.afterglow.meter = True
    a.afterglow.meter_axis = "X"
    a.afterglow.meter_invert = True
    mod = core.find_modifier(a)
    assert nodes.get_input(mod, "Meter") is True
    assert nodes.get_input(mod, "Meter Axis") == 0 and nodes.get_input(mod, "Meter Invert") is True
    b = bpy.data.objects["b_mid"]
    b.afterglow.meter = True  # also survives a re-apply
    core.apply(scene)
    assert nodes.get_input(core.find_modifier(b), "Meter") is True

def test_meter_toggle_on_old_file():
    scene, _ = stage()
    core.apply(scene)
    helpers.v1_group()  # the file was saved before the Level Meter existed
    mat = bpy.data.materials["mat_a_bass"]
    for n in mat.node_tree.nodes:
        if n.get(materials.TAG):
            n[materials.TAG] = 1  # and its materials use the old classic patch
    a = bpy.data.objects["a_bass"]
    a.afterglow.meter = True  # must not raise; takes effect without Apply
    assert nodes.get_input(core.find_modifier(a), "Meter") is True
    assert all(n[materials.TAG] == materials.LAYOUT_CLASSIC for n in mat.node_tree.nodes if n.get(materials.TAG))

def test_sync_skips_missing_sound():  # keep last: without the guard Blender crashes here
    scene, _ = stage()
    core.apply(scene)
    good = scene.afterglow.sound
    bad = bpy.data.sounds.load(helpers.tone_wav(), check_existing=False)
    bad.filepath = "//gone.wav"
    scene.afterglow.sound = bad
    assert nodes.get_input(core.find_modifier(bpy.data.objects["a_bass"]), "Sound") == good
    at(13)

helpers.run(globals())
