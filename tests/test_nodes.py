import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import helpers  # noqa: E402
from helpers import at, attr, plane, sound  # noqa: E402
from afterglow import nodes  # noqa: E402

BASS = (20.0, 184.0)
TREBLE = (1692.0, 16000.0)


def reactive(name, band, inputs=None, data=None):
    ob = bpy.data.objects.new(name, data) if data else plane(name)
    if data:
        bpy.context.scene.collection.objects.link(ob)
    mod = ob.modifiers.new("Afterglow", "NODES")
    mod.node_group = nodes.get_group()
    nodes.set_input(mod, "Sound", sound())
    nodes.set_input(mod, "Low", band[0])
    nodes.set_input(mod, "High", band[1])
    for k, v in (inputs or {}).items():
        nodes.set_input(mod, k, v)
    return ob


def test_group_has_inputs_and_is_reused():
    g = nodes.get_group()
    names = [i.name for i in g.interface.items_tree if i.in_out == "INPUT" and i.name != "Geometry"]
    assert names == [n for n, _, _ in nodes.INPUTS], names
    assert nodes.get_group() == g
    assert len([n for n in bpy.data.node_groups if n.name.startswith(nodes.GROUP_NAME)]) == 1


def test_set_get_roundtrip():
    ob = reactive("a", BASS, {"Decay": 0.4})
    mod = ob.modifiers[0]
    assert math.isclose(nodes.get_input(mod, "Decay"), 0.4, rel_tol=1e-6)
    assert nodes.get_input(mod, "Sound") == sound()


def test_set_input_after_evaluation_takes_effect():
    ob = reactive("a", BASS)
    at(13)
    before = attr(ob)
    nodes.set_input(ob.modifiers[0], "Gain", 2.0)
    at(13)
    assert math.isclose(attr(ob), 2 * before, rel_tol=1e-3), (before, attr(ob))


def test_bands_separate():
    bass, treble = reactive("bass", BASS), reactive("treble", TREBLE)
    at(13)
    assert attr(bass) > 0.3 and attr(treble) < 0.05, (attr(bass), attr(treble))
    at(61)
    assert attr(treble) > 0.3 and attr(bass) < 0.05, (attr(bass), attr(treble))


def test_decay_holds_after_tone_stops():
    held = reactive("held", BASS, {"Decay": 0.3})
    dry = reactive("dry", BASS, {"Decay": 0.0})
    at(31)  # 1.25 s: bass tone ended at 1.0 s
    assert attr(held) > 0.1, attr(held)
    assert attr(dry) < 0.02, attr(dry)


def test_threshold_and_gain():
    base = reactive("base", BASS)
    doubled = reactive("doubled", BASS, {"Gain": 2.0})
    gated = reactive("gated", BASS, {"Threshold": 1.0})
    at(13)
    assert math.isclose(attr(doubled), 2 * attr(base), rel_tol=1e-3)
    assert attr(gated) == 0.0


def test_delay_and_start_frame():
    delayed = reactive("delayed", BASS, {"Delay": 1.0})
    late = reactive("late", BASS, {"Start Frame": 25})
    at(37)  # 1.5 s scene time = 0.5 s into the audio for both
    assert attr(delayed) > 0.3 and attr(late) > 0.3, (attr(delayed), attr(late))
    at(13)  # sample time is negative for both: before the audio starts
    assert attr(delayed) == 0.0 and attr(late) == 0.0, (attr(delayed), attr(late))


def test_frame_zero_dark_before_start():
    late = reactive("late", BASS, {"Start Frame": 10})
    default = reactive("default", BASS)  # Start Frame 1
    at(0)
    assert attr(late) == 0.0 and attr(default) == 0.0, (attr(late), attr(default))


def test_colors_and_tint():
    tinted = reactive("tinted", BASS, {"Use Colors": True, "Gain": 10.0,
                                       "Color A": (0.0, 0.0, 0.0, 1.0), "Color B": (1.0, 0.0, 0.0, 1.0)})
    plain = reactive("plain", BASS)
    at(13)
    assert all(math.isclose(a, b, abs_tol=1e-4) for a, b in zip(attr(tinted, "ar_color"), (1, 0, 0, 1)))
    assert attr(tinted, "ar_tint") == 1.0
    assert attr(plain, "ar_tint") == 0.0


def test_text_and_curve_get_attribute():
    txt = bpy.data.curves.new("t", "FONT")
    txt.body = "HI"
    crv = bpy.data.curves.new("c", "CURVE")
    crv.dimensions = "3D"
    crv.bevel_depth = 0.1
    spline = crv.splines.new("POLY")
    spline.points.add(1)
    spline.points[1].co = (1, 0, 0, 1)
    text_ob = reactive("text", BASS, data=txt)
    curve_ob = reactive("curve", BASS, data=crv)
    at(13)
    assert attr(text_ob) > 0.3 and attr(curve_ob) > 0.3


def test_ripple_uses_kick_band():
    ob = reactive("treble_but_ripple", TREBLE, {"Ripple": True})
    at(13)  # 100 Hz tone: outside the object's own band, inside the kick band
    assert attr(ob) > 0.3, attr(ob)


def test_ripple_delays_by_position():
    first = reactive("first", BASS, {"Ripple": True, "Decay": 0.0, "Ripple Position": 0.0})
    last = reactive("last", BASS, {"Ripple": True, "Decay": 0.0, "Ripple Position": 1.0})
    at(31)  # 1.25 s: bass ended for 'first'; 'last' lags 1 sweep x 0.5 s -> samples 0.75 s
    assert attr(first) < 0.02 and attr(last) > 0.3, (attr(first), attr(last))


def test_old_group_migrates():
    old = nodes.get_group()
    old[nodes.TAG] = 1  # what v0.2 wrote
    ob = reactive("kept", BASS, {"Gain": 3.0})
    new = nodes.get_group()
    assert new[nodes.TAG] == nodes.VERSION and new.name == nodes.GROUP_NAME
    assert ob.modifiers[0].node_group == new
    assert math.isclose(nodes.get_input(ob.modifiers[0], "Gain"), 3.0)
    assert len([g for g in bpy.data.node_groups if g.get(nodes.TAG)]) == 1


def test_ripple_uses_its_own_gain_and_threshold():
    ob = reactive("calibrated", TREBLE, {"Gain": 100.0, "Threshold": 0.5, "Ripple": True})
    at(13)  # Ripple Gain 1 / Ripple Threshold 0 by default: raw kick level, not 100x
    assert 0.3 < attr(ob) < 1.0, attr(ob)


def test_real_v1_group_migrates():
    old = helpers.v1_group()
    ob = plane("kept")
    mod = ob.modifiers.new("Afterglow", "NODES")
    mod.node_group = old
    nodes.set_input(mod, "Gain", 3.0)
    new = nodes.get_group()
    assert ob.modifiers[0].node_group == new
    assert math.isclose(nodes.get_input(ob.modifiers[0], "Gain"), 3.0)
    assert nodes.get_input(ob.modifiers[0], "Ripple") is False

def test_baked_peak_replaces_live_sampling():
    ob = reactive("baked", BASS, {"Use Baked": True, "Baked Peak": 0.7, "Decay": 0.0})
    at(37)  # 1 kHz playing, so live bass would be ~0; baked says 0.7
    assert math.isclose(attr(ob), 0.7, abs_tol=1e-4), attr(ob)


def close(a, b):
    return all(abs(x - y) < 1e-4 for x, y in zip(a, b))


def test_through_stores_tap_levels_and_wave():
    inputs = {"Use Baked": True, "Through": True, "Gain": 2.0, "Threshold": 0.1,
              "Wave Axis": (0.0, 1.0, 0.0), "Wave Origin": (1.0, 2.0, 3.0), "Wave Lo": -3.0, "Wave Span": 6.0,
              "Wave Radial": True, "Wave Reverse": True, "Tap Min": 0.25, "Tap Max": 0.75,
              "Color A": (1.0, 0.0, 0.0, 1.0), "Color B": (0.0, 0.0, 1.0, 1.0)}
    inputs.update({f"Baked Tap {k}": k * 0.1 for k in range(8)})
    ob = reactive("w", BASS, inputs)
    at(5)
    # level = max(tap - 0.1, 0) * 2 -> 0, 0, .2, .4, .6, .8, 1.0, 1.2; Reverse folds into Lo/Span
    assert close(helpers.attr(ob, "ar_tap0"), (0.0, 0.0, 0.2)), helpers.attr(ob, "ar_tap0")
    assert close(helpers.attr(ob, "ar_tap1"), (0.4, 0.6, 0.8))
    assert close(helpers.attr(ob, "ar_tap2"), (1.0, 1.2, 3.0))    # taps 6, 7, Lo + Span
    assert close(helpers.attr(ob, "ar_tap3"), (-6.0, 0.25, 0.75))  # -Span, Tap Min, Tap Max
    assert close(helpers.attr(ob, "ar_wave"), (1.0, 2.0, 3.0))     # radial: the origin
    assert close(helpers.attr(ob, "ar_base"), (0.0, 0.0, 2.0))     # level, tint, mode 2 = radial
    assert close(helpers.attr(ob, "ar_color_a"), (1.0, 0.0, 0.0))
    assert close(helpers.attr(ob, "ar_color_b"), (0.0, 0.0, 1.0))


def test_through_attributes_absent_unless_baked_and_through():
    only_through = reactive("t", BASS, {"Through": True})
    only_baked = reactive("b", BASS, {"Use Baked": True})
    at(5)
    assert not helpers.has_attr(only_through, "ar_tap0") and not helpers.has_attr(only_baked, "ar_tap0")
    assert helpers.attr(only_through, "ar_base")[2] == 0.0  # mode 0: the shader falls back to the level


def column():
    me = bpy.data.meshes.new("col")
    me.from_pydata([(0.0, 0.0, 0.0), (1.0, 0.0, 3.0)], [], [])  # x 0..1, z 0..3
    return me


def test_meter_stores_height_range():
    up = reactive("up", BASS, {"Meter": True, "Meter Axis": 2}, data=column())
    down = reactive("down", BASS, {"Meter": True, "Meter Axis": 2, "Meter Invert": True}, data=column())
    side = reactive("side", BASS, {"Meter": True, "Meter Axis": 0}, data=column())
    off = reactive("off", BASS, {}, data=column())
    at(5)
    assert close(helpers.attr(up, "ar_meter"), (0.0, 3.0, 3.0)), helpers.attr(up, "ar_meter")
    assert close(helpers.attr(down, "ar_meter"), (0.0, 3.0, -3.0)), helpers.attr(down, "ar_meter")
    assert close(helpers.attr(side, "ar_meter"), (0.0, 1.0, 1.0)), helpers.attr(side, "ar_meter")
    assert helpers.attr(off, "ar_meter")[2] == 0.0


helpers.run(globals())
