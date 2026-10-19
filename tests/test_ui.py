import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import helpers  # noqa: E402  (puts repo root on sys.path)
import afterglow  # noqa: E402
from helpers import emission_material, plane, tone_wav  # noqa: E402
from afterglow import core, nodes, tempo  # noqa: E402


def stage():
    coll = bpy.data.collections.new("Lights")
    bpy.context.scene.collection.children.link(coll)
    plane("a", coll, emission_material("ma"))
    plane("b", coll, emission_material("mb"))
    bpy.context.scene.afterglow.collection = coll
    return coll


def test_register_roundtrip():
    afterglow.unregister()
    assert "afterglow" not in bpy.types.Scene.bl_rna.properties
    afterglow.register()
    assert hasattr(bpy.types, "AFTERGLOW_PT_panel")


def test_apply_without_sound_reports_error():
    stage()
    # An operator that reports {'ERROR'} makes bpy.ops raise RuntimeError instead of returning.
    try:
        bpy.ops.afterglow.apply()
    except RuntimeError as e:
        assert "Pick a sound file first" in str(e), str(e)
    else:
        raise AssertionError("expected an error report")


def test_load_sound_reuses_datablock():
    assert bpy.ops.afterglow.load_sound(filepath=tone_wav()) == {"FINISHED"}
    assert bpy.ops.afterglow.load_sound(filepath=tone_wav()) == {"FINISHED"}
    assert bpy.context.scene.afterglow.sound is not None
    assert len(bpy.data.sounds) == 1


def test_operator_cycle():
    coll = stage()
    bpy.ops.afterglow.load_sound(filepath=tone_wav())
    assert bpy.ops.afterglow.apply() == {"FINISHED"}
    assert all(core.find_modifier(o) for o in coll.all_objects)
    assert bpy.ops.afterglow.respread() == {"FINISHED"}
    assert bpy.ops.afterglow.calibrate() == {"FINISHED"}
    assert bpy.ops.afterglow.remove() == {"FINISHED"}
    assert not any(core.find_modifier(o) for o in coll.all_objects)


def test_modifier_inputs_drawable():
    stage()
    bpy.ops.afterglow.load_sound(filepath=tone_wav())
    bpy.ops.afterglow.apply()
    mod = core.find_modifier(bpy.data.objects["a"])
    for name in nodes.OBJECT_INPUTS:
        assert "value" in nodes._socket(mod, name).bl_rna.properties, name


def test_apply_reports_tempo_or_skip():
    stage()
    bpy.ops.afterglow.load_sound(filepath=tone_wav())
    assert bpy.ops.afterglow.apply() == {"FINISHED"}
    assert nodes.get_group().animation_data is not None
    # Can't use a missing file here: Blender 5.2 crashes evaluating Sample Sound on one.
    real_bake = tempo.bake

    def failing_bake(scene):
        raise ValueError("Tempo analysis needs the sound file on disk")

    tempo.bake = failing_bake
    try:
        assert bpy.ops.afterglow.apply() == {"FINISHED"}  # tempo skipped is a warning, not an error
    finally:
        tempo.bake = real_bake


def test_bake_operator_and_outdated_panel_data():
    stage()
    bpy.ops.afterglow.load_sound(filepath=tone_wav())
    bpy.ops.afterglow.apply()
    bpy.context.scene.afterglow.fast_playback = True
    assert bpy.ops.afterglow.bake() == {"FINISHED"}
    from afterglow import bake
    assert bake.outdated(bpy.context.scene) == []


class FakeLayout:
    """Records what a panel draws; every prop must exist on its data."""

    def __init__(self, log):
        self.log, self.active, self.alert = log, True, False

    def _child(self, *a, **k):
        return FakeLayout(self.log)

    row = column = box = _child

    def panel(self, idname, default_closed=False):
        self.log.append(("panel", idname))
        return FakeLayout(self.log), FakeLayout(self.log)       # drawn open

    def prop(self, data, name, **k):
        assert name in data.bl_rna.properties, name
        self.log.append(("prop", name))

    def operator(self, idname, **k):
        self.log.append(("op", idname))

    def label(self, **k):
        pass

    def separator(self, **k):
        pass


def test_next_step_walks_setup():
    from afterglow import ui
    scene = bpy.context.scene
    assert "collection" in ui.next_step(scene)
    stage()
    assert "sound" in ui.next_step(scene)
    bpy.ops.afterglow.load_sound(filepath=tone_wav())
    assert "Apply" in ui.next_step(scene)
    bpy.ops.afterglow.apply()
    assert ui.next_step(scene) == ui.CALIBRATE
    bpy.ops.afterglow.calibrate()
    assert ui.next_step(scene) is None
    log = []
    ui.AFTERGLOW_PT_panel.draw(type("P", (), {"layout": FakeLayout(log)})(), bpy.context)
    assert ("op", "afterglow.calibrate") in log


def test_fx_next_step_and_empty_build():
    from afterglow import effects, ui
    scene = bpy.context.scene
    stage()
    bpy.ops.afterglow.load_sound(filepath=tone_wav())
    assert "Enable a layer" in ui.fx_next_step(scene)
    try:
        effects.build(scene)
    except ValueError as e:
        assert "Enable an effect layer" in str(e), str(e)
    else:
        raise AssertionError("an empty build must refuse, not wipe the last build")
    scene.afterglow.effects.strikes.enabled = True
    assert ui.fx_next_step(scene) == "Press Build Effects"


def test_strikes_panel_draws_strike_points():
    stage()
    from afterglow import ui
    fx = bpy.context.scene.afterglow.effects
    fx.strikes.enabled, fx.strike_placement = True, "POINTS"
    log = []
    ui.AFTERGLOW_PT_effects.draw(type("P", (), {"layout": FakeLayout(log)})(), bpy.context)
    drawn = {n for kind, n in log if kind == "prop"}
    assert {"strike_placement", "strike_points", "strike_order"} <= drawn, drawn
    assert ("op", "afterglow.add_strike_point") in log


def test_object_box_draws_every_object_input():
    stage()
    bpy.ops.afterglow.load_sound(filepath=tone_wav())
    bpy.ops.afterglow.apply()
    from afterglow import ui
    ob = bpy.data.objects["a"]
    mod = core.find_modifier(ob)
    drawn = []
    real = nodes.draw_input
    nodes.draw_input = lambda layout, m, name, text=None: drawn.append(name)
    try:
        for ripple in (False, True):
            bpy.context.scene.afterglow.ripple = ripple
            ui.draw_object(FakeLayout([]), bpy.context.scene.afterglow, ob, mod)
    finally:
        nodes.draw_input = real
    want = set(nodes.OBJECT_INPUTS) | set(nodes.RIPPLE_PAIR.values())
    assert want <= set(drawn), want - set(drawn)


def test_cascade_panel_draws_its_controls():
    stage()
    from afterglow import ui
    bpy.context.scene.afterglow.effects.downward.enabled = True
    log = []
    ui.AFTERGLOW_PT_effects.draw(type("P", (), {"layout": FakeLayout(log)})(), bpy.context)
    drawn = {n for kind, n in log if kind == "prop"}
    assert {"cascade_direction", "cascade_placement", "cascade_count", "cascade_spacing",
            "cascade_size"} <= drawn, drawn


def test_effects_panel_exposes_the_global_timing_offset():
    stage()
    from afterglow import ui
    log = []
    ui.AFTERGLOW_PT_effects.draw(type("P", (), {"layout": FakeLayout(log)})(), bpy.context)
    assert ("prop", "timing_offset") in log
    prop = bpy.context.scene.afterglow.effects.bl_rna.properties["timing_offset"]
    assert not prop.is_animatable  # one static post-snap shift, not an unsampled F-curve


def test_effects_panel_draws_its_sections():
    stage()
    from afterglow import ui
    log = []
    panel = type("P", (), {"layout": FakeLayout(log)})()
    ui.AFTERGLOW_PT_effects.draw(panel, bpy.context)
    assert ("panel", "AFTERGLOW_fx_venue") in log and ("panel", "AFTERGLOW_fx_beams") in log
    drawn = {n for kind, n in log if kind == "prop"}
    for name in ("venue_shape", "emitter", "beams", "beam_glow", "beam_walk", "beam_walk_time",
                 "beam_flow", "beam_direction", "beam_gaps", "beam_dash"):
        assert name in drawn, name
    assert ("op", "afterglow.build_effects") in log


def test_signal_panel_exposes_controls_with_graphics_off_by_default():
    stage()
    from afterglow import ui
    fx = bpy.context.scene.afterglow.effects
    fx.signals.enabled = True
    assert not fx.signal_graphics
    for enabled in (False, True):
        fx.signal_graphics = enabled
        log = []
        ui.AFTERGLOW_PT_effects.draw(type("P", (), {"layout": FakeLayout(log)})(), bpy.context)
        drawn = {name for kind, name in log if kind == "prop"}
        assert {"signal_anchors", "signal_speed", "signal_route", "signal_direction",
                "signal_surface", "signal_graphics"} <= drawn, drawn
        assert ("op", "afterglow.add_signal_anchor") in log
        if enabled:
            assert {"signal_width", "signal_tail", "signal_frame_size", "signal_glow"} <= drawn


helpers.run(globals())
