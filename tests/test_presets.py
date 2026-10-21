import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from helpers import emission_material, plane, sound
from afterglow import bake, core, effects, nodes, presets


def stage():
    scene = bpy.context.scene
    coll = bpy.data.collections.new("Preset targets")
    scene.collection.children.link(coll)
    ob = plane("reactive", coll, emission_material("emission"))
    scene.afterglow.collection = coll
    scene.afterglow.sound = sound()
    core.apply(scene)
    return scene, ob, core.find_modifier(ob)


def test_all_looks_have_distinct_layer_and_color_choices():
    scene = bpy.context.scene
    snapshots = set()
    for identifier, label, description in presets.ITEMS:
        assert description == presets.DESCRIPTIONS[identifier] and label
        count = presets.apply(scene, identifier)
        fx = scene.afterglow.effects
        enabled = tuple(name for name in effects.LAYERS if getattr(fx, name).enabled)
        assert count == len(enabled) > 0
        snapshots.add((enabled, tuple(fx.blooms.color)))
    assert len(snapshots) == 5


def test_look_styles_reactive_objects_without_building_or_rebaking():
    scene, ob, mod = stage()
    scene.afterglow.fast_playback = True
    assert not bake.outdated(scene)
    presets.apply(scene, "AURORA_FLOW")
    assert nodes.get_input(mod, "Use Colors")
    assert abs(nodes.get_input(mod, "Decay") - 0.5) < 1e-5
    assert ob in bake.outdated(scene)
    assert not effects._collections(scene)


def test_presets_preserve_audio_placement_routing_and_unmanaged_objects():
    scene, ob, mod = stage()
    outside = plane("outside", material=emission_material("outside_material"))
    fx = scene.afterglow.effects
    anchor = bpy.data.collections.new("Custom route")
    scene.collection.children.link(anchor)
    fx.signal_anchors = anchor
    fx.signal_route, fx.signal_direction = "SPOKES", "REVERSE"
    fx.signal_speed, fx.signal_width = 7.5, 0.04
    fx.venue_shape, fx.height, fx.margin, fx.seed = "TRIANGLE", 12.0, 1.8, 99
    fx.strike_placement, fx.strike_points = "POINTS", anchor
    fx.cascade_direction = "FROM_CAMERA"
    initial = (scene.afterglow.collection, scene.afterglow.sound, scene.afterglow.start_frame,
               scene.world, scene.camera, fx.emitter, fx.signal_anchors, fx.signal_route,
               fx.signal_direction, fx.signal_speed, fx.signal_width, fx.venue_shape,
               fx.height, fx.margin, fx.seed, fx.strike_placement, fx.strike_points,
               fx.cascade_direction)
    presets.apply(scene, "SIGNAL_GRID")
    after = (scene.afterglow.collection, scene.afterglow.sound, scene.afterglow.start_frame,
             scene.world, scene.camera, fx.emitter, fx.signal_anchors, fx.signal_route,
             fx.signal_direction, fx.signal_speed, fx.signal_width, fx.venue_shape,
             fx.height, fx.margin, fx.seed, fx.strike_placement, fx.strike_points,
             fx.cascade_direction)
    assert initial == after
    assert not core.find_modifier(outside)
    assert not fx.flashes.enabled  # Rapid flashes are an explicit creative choice.


def test_scene_animation_conflict_is_atomic():
    scene, ob, mod = stage()
    fx = scene.afterglow.effects
    fx.blooms.intensity = 0.37
    fx.blooms.keyframe_insert("intensity", frame=1)
    old_color = tuple(nodes.get_input(mod, "Color A"))
    try:
        presets.apply(scene, "NEON_CLUB")
    except ValueError as e:
        assert "animated controls" in str(e)
    else:
        raise AssertionError("Expected animated scene control conflict")
    assert not fx.strikes.enabled and abs(fx.blooms.intensity - 0.37) < 1e-5
    assert tuple(nodes.get_input(mod, "Color A")) == old_color


def test_modifier_animation_conflict_is_atomic():
    scene, ob, mod = stage()
    nodes._socket(mod, "Color B").keyframe_insert("value", frame=1)
    try:
        presets.apply(scene, "NEON_CLUB")
    except ValueError as e:
        assert "reactive" in str(e)
    else:
        raise AssertionError("Expected animated modifier conflict")
    assert not scene.afterglow.effects.strikes.enabled
    assert not nodes.get_input(mod, "Use Colors")


def test_nla_and_driver_conflicts_are_detected():
    scene, ob, mod = stage()
    fx = scene.afterglow.effects
    fx.blooms.keyframe_insert("weight", frame=1)
    ad = scene.animation_data
    action = ad.action
    track = ad.nla_tracks.new()
    strip = track.strips.new("Existing show", 1, action)
    strip.action_slot = ad.action_slot
    ad.action = None
    try:
        presets.apply(scene, "NEON_CLUB")
    except ValueError:
        pass
    else:
        raise AssertionError("NLA controls must be protected")
    ad.nla_tracks.remove(track)
    fx.strikes.driver_add("intensity").driver.expression = "0.4"
    try:
        presets.apply(scene, "NEON_CLUB")
    except ValueError:
        pass
    else:
        raise AssertionError("Driven controls must be protected")


def test_unrelated_animation_is_preserved_and_repeated_application_is_stable():
    scene, ob, mod = stage()
    ob.keyframe_insert("location", frame=1)
    curves = helpers.fcurves(ob)
    before = [(fc.data_path, fc.array_index, [tuple(k.co) for k in fc.keyframe_points]) for fc in curves]
    presets.apply(scene, "CINEMATIC_PULSE")
    first = tuple(nodes.get_input(mod, "Color A"))
    presets.apply(scene, "CINEMATIC_PULSE")
    assert tuple(nodes.get_input(mod, "Color A")) == first
    assert before == [(fc.data_path, fc.array_index, [tuple(k.co) for k in fc.keyframe_points])
                      for fc in helpers.fcurves(ob)]


def test_invalid_look_leaves_settings_unchanged():
    scene = bpy.context.scene
    try:
        presets.apply(scene, "MISSING")
    except ValueError:
        pass
    else:
        raise AssertionError("Unknown look must report an error")
    assert not any(getattr(scene.afterglow.effects, name).enabled for name in effects.LAYERS)


def test_laser_show_pairs_green_rays_with_violet_washes():
    scene = bpy.context.scene
    presets.apply(scene, "LASER_SHOW")
    fx = scene.afterglow.effects
    assert fx.lasers.enabled and fx.blooms.enabled and not fx.beams
    assert fx.lasers.color.g > fx.lasers.color.r and fx.lasers.color.g > fx.lasers.color.b
    assert fx.blooms.color.b > fx.blooms.color.g and fx.blooms.color.r > fx.blooms.color.g


helpers.run(globals())
