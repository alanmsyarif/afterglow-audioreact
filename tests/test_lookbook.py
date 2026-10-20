import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from helpers import emission_material, plane, sound
from afterglow import bake, core, effects, lookbook, nodes, presets


def stage():
    scene = bpy.context.scene
    collection = bpy.data.collections.new("Lookbook targets")
    scene.collection.children.link(collection)
    objects = [plane(name, collection, emission_material("Material " + name))
               for name in ("a_first", "z_second")]
    scene.afterglow.collection = collection
    scene.afterglow.sound = sound()
    core.apply(scene)
    return scene, objects


def rejects(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError("Expected look validation to reject this input")


def legacy_look(scene):
    """The complete schema exported before laser support, without v2 fields."""
    snapshot = lookbook.capture(scene)
    snapshot["version"] = 1
    del snapshot["layers"]["lasers"]
    for name in ("laser_count", "laser_spread", "laser_sweep", "laser_glow"):
        del snapshot["effects"][name]
    return snapshot


def test_capture_roundtrip_restores_fx_and_common_object_style():
    scene, objects = stage()
    presets.apply(scene, "NEON_CLUB")
    fx = scene.afterglow.effects
    fx.beam_gaps = 0.8
    fx.signal_width = 0.055
    first = core.find_modifier(objects[0])
    second = core.find_modifier(objects[1])
    nodes.set_input(first, "Glow", 0.13)
    nodes.set_input(second, "Glow", 0.88)
    expected = lookbook.loads(lookbook.dumps(scene))
    assert abs(expected["object"]["Glow"] - 0.13) < 1e-6
    presets.apply(scene, "AURORA_FLOW")
    assert lookbook.apply(scene, expected) == 3
    assert lookbook.capture(scene) == expected
    assert abs(nodes.get_input(second, "Glow") - 0.13) < 1e-6


def test_empty_scene_captures_fx_only_and_does_not_create_modifiers():
    scene = bpy.context.scene
    snapshot = lookbook.capture(scene)
    assert snapshot["object"] is None
    assert lookbook.loads(lookbook.dumps(scene)) == snapshot
    assert lookbook.apply(scene, snapshot) == 0
    assert not bpy.data.objects and not effects._collections(scene)


def test_saved_style_preserves_project_sources_routing_and_geometry():
    scene, objects = stage()
    snapshot = lookbook.capture(scene)
    fx = scene.afterglow.effects
    anchors = bpy.data.collections.new("User anchors")
    scene.collection.children.link(anchors)
    fx.signal_anchors = anchors
    fx.strike_points = anchors
    fx.strike_placement = "POINTS"
    fx.signal_route, fx.signal_direction, fx.signal_speed = "SPOKES", "REVERSE", 13.0
    fx.height, fx.seed, fx.venue_shape, fx.margin = 14.0, 809, "TRIANGLE", 1.7
    fx.cascade_direction, fx.cascade_placement = "FROM_CAMERA", "CENTER"
    scene.afterglow.start_frame = 31
    initial = (scene.afterglow.collection, scene.afterglow.sound, scene.afterglow.start_frame,
               fx.signal_anchors, fx.strike_points, fx.strike_placement, fx.signal_route,
               fx.signal_direction, fx.signal_speed, fx.height, fx.seed, fx.venue_shape,
               fx.margin, fx.emitter, fx.cascade_direction, fx.cascade_placement,
               scene.camera, scene.world, len(bpy.data.objects))
    lookbook.apply(scene, snapshot)
    after = (scene.afterglow.collection, scene.afterglow.sound, scene.afterglow.start_frame,
             fx.signal_anchors, fx.strike_points, fx.strike_placement, fx.signal_route,
             fx.signal_direction, fx.signal_speed, fx.height, fx.seed, fx.venue_shape,
             fx.margin, fx.emitter, fx.cascade_direction, fx.cascade_placement,
             scene.camera, scene.world, len(bpy.data.objects))
    assert initial == after
    assert not effects._collections(scene)
    assert "filepath" not in json.dumps(snapshot)


def test_bad_fields_and_values_rejected_before_any_setting_changes():
    scene, objects = stage()
    initial = lookbook.capture(scene)
    invalid = []
    data = copy.deepcopy(initial); data["version"] = True; invalid.append(data)
    data = copy.deepcopy(initial); data["version"] = lookbook.VERSION + 1; invalid.append(data)
    data = copy.deepcopy(initial); data["layers"]["strikes"]["extra"] = 1; invalid.append(data)
    data = copy.deepcopy(initial); del data["effects"]["beams"]; invalid.append(data)
    data = copy.deepcopy(initial); data["effects"]["beams"] = 1; invalid.append(data)
    data = copy.deepcopy(initial); data["effects"]["strike_companions"] = 2.0; invalid.append(data)
    data = copy.deepcopy(initial); data["effects"]["strike_companions"] = -1; invalid.append(data)
    data = copy.deepcopy(initial); data["effects"]["gun_style"] = "UNKNOWN"; invalid.append(data)
    data = copy.deepcopy(initial); data["effects"]["cascade_count"] = 99; invalid.append(data)
    data = copy.deepcopy(initial); data["effects"]["laser_count"] = 7.0; invalid.append(data)
    data = copy.deepcopy(initial); data["effects"]["laser_count"] = 13; invalid.append(data)
    data = copy.deepcopy(initial); data["effects"]["laser_spread"] = 0.01; invalid.append(data)
    data = copy.deepcopy(initial); data["effects"]["laser_sweep"] = 1.61; invalid.append(data)
    data = copy.deepcopy(initial); data["effects"]["laser_glow"] = -1; invalid.append(data)
    data = copy.deepcopy(initial); data["layers"]["strikes"]["timing"] = 10; invalid.append(data)
    data = copy.deepcopy(initial); data["layers"]["gun"]["color"] = [0.1, 0.2]; invalid.append(data)
    data = copy.deepcopy(initial); data["layers"]["gun"]["color"][0] = float("nan"); invalid.append(data)
    data = copy.deepcopy(initial); data["layers"]["strikes"]["intensity"] = float("inf"); invalid.append(data)
    data = copy.deepcopy(initial); data["object"]["Decay"] = -0.1; invalid.append(data)
    data = copy.deepcopy(initial); data["object"]["Use Colors"] = "true"; invalid.append(data)
    data = copy.deepcopy(initial); data["object"]["Decay"] = 10 ** 400; invalid.append(data)
    data = copy.deepcopy(initial); data["audio"] = "do-not-read.wav"; invalid.append(data)
    for snapshot in invalid:
        rejects(lambda: lookbook.apply(scene, snapshot))
        assert lookbook.capture(scene) == initial


def test_json_rejects_duplicates_constants_malformed_and_oversize_payloads():
    for text in ('{}', '{"format":"a","format":"b"}', '{"a":NaN}', '{"a":Infinity}',
                 '{"a":-Infinity}', '[1,2]', 'not-json', ' ' * (lookbook.MAX_BYTES + 1),
                 '[' * 1500 + ']' * 1500, '\ud800'):
        rejects(lambda: lookbook.loads(text))
    rejects(lambda: lookbook.loads(b'{}'))


def test_scene_and_modifier_animation_conflicts_are_atomic():
    scene, objects = stage()
    snapshot = lookbook.capture(scene)
    snapshot["layers"]["strikes"]["enabled"] = True
    scene.afterglow.effects.blooms.keyframe_insert("intensity", frame=1)
    before = lookbook.capture(scene)
    rejects(lambda: lookbook.apply(scene, snapshot))
    assert lookbook.capture(scene) == before
    scene.animation_data_clear()
    mod = core.find_modifier(objects[1])
    nodes._socket(mod, "Color A").driver_add("value", 0).driver.expression = "0.2"
    before = lookbook.capture(scene)
    rejects(lambda: lookbook.apply(scene, snapshot))
    assert lookbook.capture(scene) == before


def test_unrelated_animation_is_preserved_and_bake_state_remains_truthful():
    scene, objects = stage()
    scene.afterglow.fast_playback = True
    objects[0].keyframe_insert("location", frame=1)
    action = objects[0].animation_data.action
    snapshot = lookbook.capture(scene)
    snapshot["object"]["Decay"] = 0.75
    lookbook.apply(scene, snapshot)
    assert objects[0].animation_data.action == action
    assert all(ob in bake.outdated(scene) for ob in objects)


def test_preflight_and_builtin_layer_metadata_do_not_apply_values():
    scene, objects = stage()
    before = lookbook.capture(scene)
    presets.preflight(scene, "NEON_CLUB")
    assert lookbook.capture(scene) == before
    assert presets.enabled_layers("NEON_CLUB") == ("strikes", "blooms", "gun")
    rejects(lambda: presets.preflight(scene, "INVALID"))
    rejects(lambda: presets.enabled_layers("INVALID"))
    scene.afterglow.effects.blooms.keyframe_insert("weight", frame=1)
    rejects(lambda: presets.preflight(scene, "NEON_CLUB"))


def test_version_one_looks_keep_their_style_and_disable_lasers():
    scene, objects = stage()
    presets.apply(scene, "NEON_CLUB")
    legacy = legacy_look(scene)
    before = copy.deepcopy(legacy)
    migrated = lookbook.loads(json.dumps(legacy))
    assert legacy == before
    assert migrated["version"] == 2
    assert {name: values for name, values in migrated["layers"].items()
            if name != "lasers"} == legacy["layers"]
    assert all(migrated["effects"][name] == value for name, value in legacy["effects"].items())
    assert migrated["object"] == legacy["object"]
    assert migrated["layers"]["lasers"] == {
        "enabled": False, "weight": 0.8, "trigger_band": "LOW",
        "timing": 1.0, "intensity": 1.0, "color": [0.3, 1.0, 0.55]}
    assert {name: migrated["effects"][name] for name in
            ("laser_count", "laser_spread", "laser_sweep", "laser_glow")} == {
        "laser_count": 7, "laser_spread": 1.0, "laser_sweep": 0.7, "laser_glow": 1.0}
    fx = scene.afterglow.effects
    fx.lasers.enabled, fx.laser_count, fx.laser_sweep = True, 12, 1.3
    assert lookbook.apply(scene, legacy) == 3
    assert not fx.lasers.enabled and fx.laser_count == 7
    assert abs(fx.laser_sweep - 0.7) < 1e-6
    exported = json.loads(lookbook.dumps(scene))
    assert exported["version"] == 2 and "lasers" in exported["layers"]
    assert all(exported["effects"][name] == value for name, value in legacy["effects"].items())
    migrated["layers"]["strikes"]["color"][0] = 0.0
    assert legacy == before


def test_version_two_preserves_laser_style_and_legal_rna_boundaries():
    scene, objects = stage()
    fx = scene.afterglow.effects
    fx.lasers.enabled, fx.lasers.weight, fx.lasers.trigger_band = True, 0.35, "HIGH"
    fx.lasers.timing, fx.lasers.intensity, fx.lasers.color = 1.5, 2.0, (0.1, 0.9, 0.8)
    fx.laser_count, fx.laser_spread, fx.laser_sweep, fx.laser_glow = 12, 2.6, 1.6, 2.5
    expected = lookbook.loads(lookbook.dumps(scene))
    assert abs(expected["effects"]["laser_spread"] - 2.6) < 1e-6
    assert abs(expected["effects"]["laser_sweep"] - 1.6) < 1e-6
    fx.lasers.enabled, fx.laser_count, fx.laser_glow = False, 3, 0.0
    assert lookbook.apply(scene, expected) == 1
    assert lookbook.capture(scene) == expected


def test_versions_require_their_own_complete_schema():
    scene, objects = stage()
    current = lookbook.capture(scene)
    legacy = legacy_look(scene)
    invalid = []
    data = copy.deepcopy(legacy); data["version"] = 2; invalid.append(data)
    data = copy.deepcopy(current); data["version"] = 1; invalid.append(data)
    data = copy.deepcopy(legacy); data["effects"]["laser_glow"] = 1.0; invalid.append(data)
    data = copy.deepcopy(legacy); data["layers"]["lasers"] = current["layers"]["lasers"]; invalid.append(data)
    data = copy.deepcopy(legacy); del data["layers"]["signals"]; invalid.append(data)
    data = copy.deepcopy(legacy); del data["effects"]["signal_width"]; invalid.append(data)
    data = copy.deepcopy(current); del data["effects"]["laser_glow"]; invalid.append(data)
    data = copy.deepcopy(current); del data["layers"]["lasers"]; invalid.append(data)
    for snapshot in invalid:
        rejects(lambda: lookbook.apply(scene, snapshot))
        assert lookbook.capture(scene) == current


def test_laser_animation_guards_also_protect_legacy_migration():
    scene, objects = stage()
    legacy = legacy_look(scene)
    fx = scene.afterglow.effects
    fx.lasers.enabled = True
    fx.lasers.keyframe_insert("intensity", frame=1)
    before = lookbook.capture(scene)
    rejects(lambda: lookbook.apply(scene, legacy))
    assert lookbook.capture(scene) == before
    scene.animation_data_clear()
    fx.keyframe_insert("laser_count", frame=1)
    before = lookbook.capture(scene)
    rejects(lambda: lookbook.apply(scene, legacy))
    assert lookbook.capture(scene) == before


helpers.run(globals())
