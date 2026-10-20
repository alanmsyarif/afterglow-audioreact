import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from helpers import emission_material, plane, snapshot, sound
from afterglow import bake, core, effects, materials, nodes, workflow


def stage():
    scene = bpy.context.scene
    coll = bpy.data.collections.new("Venue")
    scene.collection.children.link(coll)
    for name in ("Bass", "Treble"):
        plane(name, coll, emission_material(name + " Material"))
    scene.afterglow.collection = coll
    scene.afterglow.sound = sound()
    return scene, coll


def test_readiness_reports_setup_without_creating_datablocks():
    scene = bpy.context.scene
    initial = len(bpy.data.node_groups)
    assert not workflow.inspect(scene)["ready"]
    assert workflow.inspect(scene)["collection"] is None
    scene, coll = stage()
    plain = plane("Unlit", coll)
    result = workflow.inspect(scene)
    assert result["ready"], result
    assert len(result["eligible"]) == 2 and result["skipped"] == [plain]
    assert result["applied"] == []
    assert len(bpy.data.node_groups) == initial
    assert not any(materials.is_patched(mat) for mat in bpy.data.materials)


def test_one_click_calibrates_and_bakes_once_and_can_refresh():
    scene, coll = stage()
    real_bake = bake.bake_all
    calls = []

    def counted(scene):
        calls.append(scene.name)
        return real_bake(scene)

    bake.bake_all = counted
    try:
        for i in range(2):
            result = workflow.create_reaction(scene)
            assert result["applied"] == result["calibrated"] == result["baked"] == 2
            assert len(calls) == i + 1, calls
            assert scene.afterglow.fast_playback
            assert bake.outdated(scene) == []
            assert all(nodes.get_input(core.find_modifier(ob), "Use Baked")
                       for ob in coll.objects)
            assert any(nodes.get_input(core.find_modifier(ob), "Gain") != 1
                       for ob in coll.objects)
        assert len(scene.sequence_editor.strips) == 1
    finally:
        bake.bake_all = real_bake


def test_shared_material_is_rejected_before_patch_or_playback_changes():
    scene, coll = stage()
    mat = bpy.data.objects["Bass"].data.materials[0]
    outsider = plane("Keep My Original Look", material=mat)
    before = snapshot(mat)
    strips = list(scene.sequence_editor.strips) if scene.sequence_editor else []
    result = workflow.inspect(scene)
    assert outsider in result["shared"] and not result["ready"]
    try:
        workflow.create_reaction(scene)
    except ValueError as error:
        assert "single-user" in str(error)
    else:
        raise AssertionError("Unsafe sharing must fail before changing materials")
    assert snapshot(mat) == before
    assert not any(core.find_modifier(ob) for ob in coll.objects)
    assert not scene.afterglow.fast_playback
    assert (list(scene.sequence_editor.strips) if scene.sequence_editor else []) == strips


def test_shared_material_in_another_scene_is_also_protected():
    scene, coll = stage()
    mat = bpy.data.objects["Bass"].data.materials[0]
    other = bpy.data.scenes.new("Another Scene")
    outside = plane("Other Scene", other.collection, mat)
    state = workflow.inspect(scene)
    assert outside in state["shared"] and not state["ready"]


def test_bad_audio_fails_before_modifying_objects():
    scene, coll = stage()
    strips = list(scene.sequence_editor.strips) if scene.sequence_editor else []
    with tempfile.NamedTemporaryFile(suffix=".wav") as broken:
        broken.write(b"this is not audio")
        broken.flush()
        scene.afterglow.sound.filepath = broken.name
        assert workflow.inspect(scene)["ready"]  # expensive decoding is deferred to the action
        try:
            workflow.create_reaction(scene)
        except ValueError as error:
            assert "Create Reaction" in str(error)
        else:
            raise AssertionError("Unreadable audio must not start setup")
    assert not any(core.find_modifier(ob) for ob in coll.objects)
    assert (list(scene.sequence_editor.strips) if scene.sequence_editor else []) == strips
    assert not scene.afterglow.fast_playback


def test_missing_ripple_origin_preserves_the_venue():
    scene, coll = stage()
    scene.afterglow.ripple = True
    scene.afterglow.ripple_from = "EMPTY"
    state = workflow.inspect(scene)
    assert "Origin" in state["reason"]
    try:
        workflow.create_reaction(scene)
    except ValueError:
        pass
    else:
        raise AssertionError("A missing origin must fail preflight")
    assert not any(core.find_modifier(ob) for ob in coll.objects)


def test_empty_or_unlit_collection_has_an_actionable_reason():
    scene, coll = stage()
    other = bpy.data.collections.new("Empty")
    scene.collection.children.link(other)
    scene.afterglow.collection = other
    assert "Emission" in workflow.inspect(scene)["reason"]
    plane("Unlit", other)
    assert not workflow.inspect(scene)["ready"]


def test_use_selected_keeps_existing_collection_links_and_skips_unlit_objects():
    scene, coll = stage()
    plain = plane("Unlit", coll)
    for ob in coll.objects:
        ob.select_set(True)
    before = {ob.name: set(ob.users_collection) for ob in coll.objects}
    selected = workflow.use_selected(bpy.context)
    assert selected == scene.afterglow.collection
    assert len(selected.objects) == 2 and plain.name not in selected.objects
    for ob in selected.objects:
        assert before[ob.name] < set(ob.users_collection)


def test_empty_selection_does_not_create_a_collection():
    initial = len(bpy.data.collections)
    try:
        workflow.use_selected(bpy.context)
    except ValueError as error:
        assert "Select" in str(error)
    else:
        raise AssertionError("Empty selection must not create an empty collection")
    assert len(bpy.data.collections) == initial


def test_packed_audio_reopens_and_creates_playback_without_its_source_file():
    import shutil
    from helpers import at, attr

    scene, coll = stage()
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "portable.wav")
        shutil.copyfile(scene.afterglow.sound.filepath, path)
        portable = bpy.data.sounds.load(path, check_existing=False)
        portable.pack()
        scene.afterglow.sound = portable
        # The saved file contains no sequencer strip yet, proving creation works
        # from packed data rather than reusing one created while the WAV existed.
        blend = os.path.join(folder, "portable.blend")
        bpy.ops.wm.save_as_mainfile(filepath=blend)
        os.remove(path)
        bpy.ops.wm.open_mainfile(filepath=blend)
        scene = bpy.context.scene
        assert scene.afterglow.sound.packed_file
        sound_count = len(bpy.data.sounds)
        result = workflow.create_reaction(scene)
        assert result["baked"] == 2 and bake.outdated(scene) == []
        strip = scene.sequence_editor.strips[core.STRIP_NAME]
        assert strip.sound == scene.afterglow.sound
        assert strip.frame_final_end > scene.afterglow.start_frame
        assert len(bpy.data.sounds) == sound_count  # no Sound referring to a deleted bootstrap file
        at(13)
        assert attr(bpy.data.objects["Bass"]) > 0.1


def test_complete_show_styles_bakes_once_and_rebuilds_without_duplicates():
    scene, coll = stage()
    scene.afterglow.effects.master_brightness = 0.65
    scene.afterglow.effects.solo_layer = "blooms"
    real_bake = bake.bake_all
    calls = []
    def counted(scene):
        calls.append(scene.name)
        return real_bake(scene)
    bake.bake_all = counted
    try:
        object_count = None
        for number in range(2):
            result = workflow.create_show(scene, "NEON_CLUB")
            assert result["applied"] == result["calibrated"] == result["baked"] == 2
            assert len(calls) == number + 1, calls
            assert set(result["effects"]) == {"strikes", "blooms", "gun"}
            assert sum(result["effects"].values()) > 0
            assert len(effects._collections(scene)) == 1
            assert not bake.outdated(scene) and not effects.outdated(scene)
            assert abs(scene.afterglow.effects.master_brightness - 0.65) < 1e-5
            assert scene.afterglow.effects.solo_layer == "blooms"
            assert all(nodes.get_input(core.find_modifier(ob), "Use Colors") for ob in coll.objects)
            assert abs(nodes.get_input(core.find_modifier(coll.objects[0]), "Decay") - 0.12) < 1e-5
            if object_count is None:
                object_count = len(scene.objects)
            else:
                assert len(scene.objects) == object_count
        assert len(scene.sequence_editor.strips) == 1
    finally:
        bake.bake_all = real_bake


def test_reaction_can_apply_a_look_before_its_final_bake():
    scene, coll = stage()
    result = workflow.create_reaction(scene, "AURORA_FLOW")
    assert result["baked"] == 2 and not bake.outdated(scene)
    assert not effects._collections(scene)
    for ob in coll.objects:
        assert abs(nodes.get_input(core.find_modifier(ob), "Decay") - 0.5) < 1e-5


def test_invalid_show_look_is_rejected_before_material_or_modifier_changes():
    scene, coll = stage()
    before = snapshot(coll.objects[0].data.materials[0])
    try:
        workflow.create_show(scene, "UNKNOWN_LOOK")
    except ValueError as error:
        assert "Unknown" in str(error)
    else:
        raise AssertionError("Unknown looks must fail before starting a show")
    assert not any(core.find_modifier(ob) for ob in coll.objects)
    assert snapshot(coll.objects[0].data.materials[0]) == before
    assert not scene.afterglow.fast_playback


def test_show_animation_conflict_preserves_unapplied_objects_and_layer_settings():
    scene, coll = stage()
    layer = scene.afterglow.effects.blooms
    layer.intensity = 0.37
    layer.keyframe_insert("intensity", frame=1)
    before = snapshot(coll.objects[0].data.materials[0])
    try:
        workflow.create_show(scene, "NEON_CLUB")
    except ValueError as error:
        assert "animated controls" in str(error)
    else:
        raise AssertionError("Animated look controls must fail preflight")
    assert not any(core.find_modifier(ob) for ob in coll.objects)
    assert snapshot(coll.objects[0].data.materials[0]) == before
    assert not scene.afterglow.effects.strikes.enabled
    assert abs(layer.intensity - 0.37) < 1e-5


def test_complete_show_preserves_custom_style_when_no_builtin_look_is_selected():
    scene, coll = stage()
    fx = scene.afterglow.effects
    fx.blooms.enabled = True
    fx.blooms.color = (0.2, 0.35, 0.7)
    before = tuple(fx.blooms.color)
    result = workflow.create_show(scene)
    assert set(result["effects"]) == {"blooms"}
    assert tuple(fx.blooms.color) == before
    assert not bake.outdated(scene) and not effects.outdated(scene)


def test_show_checks_future_look_routing_before_reaction_changes():
    scene, coll = stage()
    fx = scene.afterglow.effects
    # Aurora Flow enables cascades; its chosen direction still belongs to the user.
    fx.cascade_direction = "TO_CAMERA"
    try:
        workflow.create_show(scene, "AURORA_FLOW")
    except ValueError as error:
        assert "camera" in str(error)
    else:
        raise AssertionError("A future enabled camera cascade needs a camera")
    assert not any(core.find_modifier(ob) for ob in coll.objects)
    assert not fx.downward.enabled
    # Neon Club enables strikes but keeps the user's chosen placement mode.
    fx.strike_placement = "POINTS"
    try:
        workflow.create_show(scene, "NEON_CLUB")
    except ValueError as error:
        assert "strike point" in str(error)
    else:
        raise AssertionError("A future point strike needs a point")
    assert not any(core.find_modifier(ob) for ob in coll.objects)
    assert not fx.strikes.enabled
    # Signal Grid enables signals even though they are currently off.
    fx.signal_anchors = bpy.data.collections.new("Empty anchors")
    fx.strike_placement = "RANDOM"
    try:
        workflow.create_show(scene, "SIGNAL_GRID")
    except ValueError as error:
        assert "two distinct anchors" in str(error)
    else:
        raise AssertionError("A future enabled signal layer needs a valid route")
    assert not any(core.find_modifier(ob) for ob in coll.objects)
    assert not fx.signals.enabled


def test_complete_show_requires_an_enabled_layer_when_preserving_custom_style():
    scene, coll = stage()
    try:
        workflow.create_show(scene)
    except ValueError as error:
        assert "Enable an effect layer" in str(error)
    else:
        raise AssertionError("An empty atmosphere should fail before setup")
    assert not any(core.find_modifier(ob) for ob in coll.objects)


def test_custom_snapshot_builds_with_its_style_before_the_final_bake():
    from afterglow import lookbook
    scene, coll = stage()
    core.apply(scene)
    fx = scene.afterglow.effects
    fx.blooms.enabled = True
    fx.blooms.color = (0.3, 0.6, 0.2)
    for ob in coll.objects:
        nodes.set_input(core.find_modifier(ob), "Decay", 0.61)
    data = lookbook.capture(scene)
    fx.blooms.enabled = False
    for ob in coll.objects:
        nodes.set_input(core.find_modifier(ob), "Decay", 0.05)
    result = workflow.create_show(scene, look_data=data)
    assert set(result["effects"]) == {"blooms"}
    assert not bake.outdated(scene) and not effects.outdated(scene)
    assert tuple(round(v, 2) for v in fx.blooms.color) == (0.3, 0.6, 0.2)
    for ob in coll.objects:
        assert abs(nodes.get_input(core.find_modifier(ob), "Decay") - 0.61) < 1e-5


def test_builtin_and_custom_look_sources_are_mutually_exclusive():
    scene, coll = stage()
    try:
        workflow.create_show(scene, look_identifier="NEON_CLUB", look_data={})
    except ValueError as error:
        assert "either" in str(error)
    else:
        raise AssertionError("Conflicting style sources must fail before changes")
    assert not any(core.find_modifier(ob) for ob in coll.objects)


def test_invalid_custom_snapshot_fails_before_starting_a_reaction():
    scene, coll = stage()
    try:
        workflow.create_reaction(scene, look_data={"unknown": True})
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid saved style must not start a reaction")
    assert not any(core.find_modifier(ob) for ob in coll.objects)


helpers.run(globals())
