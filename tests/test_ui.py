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


helpers.run(globals())
