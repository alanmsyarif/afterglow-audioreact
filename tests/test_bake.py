import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aud  # noqa: E402
import bpy  # noqa: E402
import numpy as np  # noqa: E402
import helpers  # noqa: E402
from helpers import at, attr, emission_material, plane, sound  # noqa: E402
from afterglow import bake, core, nodes  # noqa: E402

NAMES = ("a_bass", "b_mid", "c_treble")
FRAMES = (13, 31, 37, 49, 61)


def stage():
    scene = bpy.context.scene
    coll = bpy.data.collections.new("Lights")
    scene.collection.children.link(coll)
    for i, name in enumerate(NAMES):
        plane(name, coll, emission_material("mat_" + name)).location.x = 5.0 * i
    scene.afterglow.collection = coll
    scene.afterglow.sound = sound()
    core.apply(scene)
    return scene


def use_baked(scene, on=True):
    for ob in scene.objects:
        mod = core.find_modifier(ob)
        if mod:
            nodes.set_input(mod, "Use Baked", on)


def levels():
    out = {}
    for f in FRAMES:
        at(f)
        for n in NAMES:
            out[(n, f)] = attr(bpy.data.objects[n])
    return out


def assert_close(live, baked):
    for key in live:
        a, b = live[key], baked[key]
        assert abs(a - b) <= max(0.03, 0.05 * abs(a)), (key, a, b)


def test_band_series_matches_node():
    scene = stage()
    for n in NAMES:
        nodes.set_input(core.find_modifier(bpy.data.objects[n]), "Decay", 0.0)
    clip = aud.Sound(helpers.tone_wav())
    mono = np.asarray(clip.data()).mean(axis=1)
    bands = [tuple(nodes.get_input(core.find_modifier(bpy.data.objects[n]), k) for k in ("Low", "High")) for n in NAMES]
    times, series = bake.band_series(mono, clip.specs[0], bands)
    for f in FRAMES:
        at(f)
        for b, n in enumerate(NAMES):
            live = attr(bpy.data.objects[n])
            mine = float(np.interp((f - 1) / 24.0, times, series[b]))
            assert abs(live - mine) <= max(0.02, 0.05 * live), (n, f, live, mine)


def test_baked_matches_live():
    scene = stage()
    live = levels()
    assert bake.bake_all(scene) == 3
    use_baked(scene)
    assert_close(live, levels())


def test_baked_ripple_matches_live():
    scene = stage()
    scene.afterglow.ripple = True  # also bakes a tempo curve; both paths read the same curve
    live = levels()
    bake.bake_all(scene)
    use_baked(scene)
    assert_close(live, levels())


def test_before_start_dark_when_baked():
    scene = stage()
    scene.afterglow.start_frame = 25
    bake.bake_all(scene)
    use_baked(scene)
    at(10)
    assert all(attr(bpy.data.objects[n]) == 0.0 for n in NAMES)


def test_after_song_dark_when_baked():
    scene = stage()
    bake.bake_all(scene)
    use_baked(scene)
    at(1 + 3 * 24 + 3 * 24)  # 3 s past the 3 s song
    assert all(attr(bpy.data.objects[n]) < 1e-4 for n in NAMES)


def test_outdated_after_edit():
    scene = stage()
    bake.bake_all(scene)
    assert bake.outdated(scene) == []
    a = bpy.data.objects["a_bass"]
    nodes.set_input(core.find_modifier(a), "Decay", 0.4)
    assert bake.outdated(scene) == [a]
    bake.bake_all(scene)
    assert bake.outdated(scene) == []


def test_rebake_replaces_keys():
    scene = stage()
    bake.bake_all(scene)
    bake.bake_all(scene)
    ob = bpy.data.objects["a_bass"]
    paths = [fc.data_path for fc in helpers.fcurves(ob)]
    assert paths.count(nodes._socket(core.find_modifier(ob), "Baked Peak").path_from_id("value")) == 1, paths


def test_fast_playback_toggle_bakes_and_switches():
    scene = stage()
    scene.afterglow.fast_playback = True
    for n in NAMES:
        mod = core.find_modifier(bpy.data.objects[n])
        assert nodes.get_input(mod, "Use Baked") is True
        assert bpy.data.objects[n].get(bake.SIG)


def test_baked_playback_much_faster():
    import time
    scene = stage()

    def per_frame():
        scene.frame_set(4)
        t0 = time.perf_counter()
        for f in range(5, 35):  # inside the 3 s tone: past its end the node returns instantly
            scene.frame_set(f)
            for n in NAMES:
                bpy.data.objects[n].evaluated_get(bpy.context.evaluated_depsgraph_get())
        return time.perf_counter() - t0

    live = per_frame()
    scene.afterglow.fast_playback = True
    fast = per_frame()
    assert fast * 20 < live, (live, fast)


def test_remove_keeps_user_animation():
    scene = stage()
    a = bpy.data.objects["a_bass"]
    a.keyframe_insert("location", frame=1)
    scene.afterglow.fast_playback = True
    core.remove(scene)
    paths = {fc.data_path for fc in helpers.fcurves(a)}
    assert "location" in paths and not any("Socket" in p for p in paths), paths
    assert bake.SIG not in a


def test_long_delay_still_dark_after_song():
    scene = stage()
    for n in NAMES:
        nodes.set_input(core.find_modifier(bpy.data.objects[n]), "Delay", 3.0)
    bake.bake_all(scene)
    use_baked(scene)
    at(721)  # ~27 s after the 3 s song: the delayed tail ended long ago
    assert all(attr(bpy.data.objects[n]) < 1e-4 for n in NAMES), [attr(bpy.data.objects[n]) for n in NAMES]


def test_shared_action_objects_bake_separately():
    scene = stage()
    a, c = bpy.data.objects["a_bass"], bpy.data.objects["c_treble"]
    a.keyframe_insert("location", frame=1)
    c.animation_data_create()
    c.animation_data.action = a.animation_data.action  # what Alt+D / Link Animation Data do
    c.animation_data.action_slot = a.animation_data.action_slot
    live = levels()
    bake.bake_all(scene)
    use_baked(scene)
    assert_close(live, levels())
    assert any(fc.data_path == "location" for fc in helpers.fcurves(c))  # the user's curves stay


def test_fps_change_flags_outdated():
    scene = stage()
    bake.bake_all(scene)
    scene.render.fps = 60
    assert len(bake.outdated(scene)) == 3


def test_calibrate_with_fast_playback_measures_raw_like_live():
    scene = stage()
    for n in NAMES:
        nodes.set_input(core.find_modifier(bpy.data.objects[n]), "Decay", 1.0)  # long tails
    core.calibrate(scene, samples=48)
    live = {n: (nodes.get_input(core.find_modifier(bpy.data.objects[n]), "Threshold"),
                nodes.get_input(core.find_modifier(bpy.data.objects[n]), "Gain")) for n in NAMES}
    scene.afterglow.fast_playback = True
    core.calibrate(scene, samples=48)
    for n in NAMES:
        mod = core.find_modifier(bpy.data.objects[n])
        thr, gain = nodes.get_input(mod, "Threshold"), nodes.get_input(mod, "Gain")
        assert abs(thr - live[n][0]) < 0.02, (n, live[n], thr, gain)
        assert abs(gain - live[n][1]) <= 0.1 * live[n][1], (n, live[n], thr, gain)


def through_stage(specs, n=21, **settings):
    scene = bpy.context.scene
    coll = bpy.data.collections.new("Bars")
    scene.collection.children.link(coll)
    for name, x0, x1 in specs:
        me = bpy.data.meshes.new(name)
        me.from_pydata([(x0 + (x1 - x0) * i / (n - 1), 0.0, 0.0) for i in range(n)], [], [])
        me.materials.append(emission_material("mat_" + name))
        coll.objects.link(bpy.data.objects.new(name, me))
    s = scene.afterglow
    s.collection, s.sound, s.ripple_from = coll, sound(), "X"
    s.ripple = True
    for k, v in settings.items():
        setattr(s, k, v)
    core.apply(scene)
    s.through = True
    s.fast_playback = True
    return scene


def tap_curve(name, k):
    ob = bpy.data.objects[name]
    path = nodes._socket(core.find_modifier(ob), f"Baked Tap {k}").path_from_id("value")
    return next(fc for fc in helpers.fcurves(ob) if fc.data_path == path)


def test_through_seam_taps_match():
    through_stage([("left", 0.0, 10.0), ("right", 10.0, 20.0)])
    left, right = tap_curve("left", 7), tap_curve("right", 0)  # both sit at wave position 0.5
    for f in range(1, 80, 3):
        assert abs(left.evaluate(f) - right.evaluate(f)) < 1e-5, (f, left.evaluate(f), right.evaluate(f))


def test_through_taps_lag_along_the_bar():
    through_stage([("bar", 0.0, 20.0)], beats_per_sweep=0.5)
    f = 5  # bass onset: the near end of the bar is lit before the far end
    vals = [tap_curve("bar", k).evaluate(f) for k in range(nodes.TAPS)]
    assert all(b <= a + 1e-6 for a, b in zip(vals, vals[1:])) and vals[0] > vals[-1] + 0.1, vals


def test_through_remove_clears_taps():
    scene = through_stage([("bar", 0.0, 20.0)])
    bake.bake_all(scene)  # re-bake must not duplicate tap curves
    ob = bpy.data.objects["bar"]
    paths = [fc.data_path for fc in helpers.fcurves(ob)]
    assert len(paths) == len(set(paths)) == 1 + nodes.TAPS, paths
    core.remove(scene)
    assert not helpers.fcurves(ob), [fc.data_path for fc in helpers.fcurves(ob)]


def test_through_toggle_keeps_bake_current():
    scene = through_stage([("bar", 0.0, 20.0)])
    assert bake.outdated(scene) == []
    scene.afterglow.through = False
    assert bake.outdated(scene) == []


helpers.run(globals())
