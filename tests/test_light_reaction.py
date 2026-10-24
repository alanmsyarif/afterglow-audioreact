"""User-owned Blender lights share the simple, reversible object-reaction workflow."""
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import bake, core, easy, effect_schema, lookbook, nodes, presets, ui, workflow


def light(name='My light', kind='POINT', collection=None, data=None):
    data = data or bpy.data.lights.new(name, kind)
    data.energy = 2.5 if data.type == 'SUN' else 800.0
    data.color = (0.2, 0.4, 0.7)
    obj = bpy.data.objects.new(name, data)
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def stage(kinds=('POINT',)):
    scene = bpy.context.scene
    collection = bpy.data.collections.new('My music lights')
    scene.collection.children.link(collection)
    objects = [light(f'Light {index:02} {kind}', kind, collection)
               for index, kind in enumerate(kinds)]
    for index, obj in enumerate(objects):
        obj.location = (index * 5.0, 0.0, 2.0)
    scene.afterglow.collection = collection
    scene.afterglow.sound = helpers.sound()
    scene.afterglow.easy_glow = False
    bpy.context.view_layer.update()
    return scene, collection, objects


def inputs(obj, **values):
    reaction = core.reaction(obj)
    for name, value in values.items():
        nodes.set_input(reaction, name.replace('_', ' '), value)
    return reaction


def energy(obj, frame):
    helpers.at(frame)
    return obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.energy


def native_color(obj):
    bpy.context.view_layer.update()
    return tuple(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.color)


def curves(owner):
    return [(fc.data_path, fc.array_index,
             tuple(tuple(point.co) for point in fc.keyframe_points))
            for fc in helpers.fcurves(owner)]


def rejected(action):
    try:
        action()
    except ValueError:
        return
    raise AssertionError('Invalid light reaction setup must be rejected before changing the scene')


def test_all_native_light_types_build_without_materials_or_geometry_modifiers():
    scene, collection, objects = stage(('POINT', 'SPOT', 'AREA', 'SUN'))
    originals = {obj: obj.data for obj in objects}
    matrices = {obj: obj.matrix_world.copy() for obj in objects}
    native = (set(scene.objects), scene.camera, scene.world,
              scene.frame_start, scene.frame_end, scene.render.engine)
    assert workflow.inspect(scene)['ready']
    result = workflow.create_reaction(scene)
    assert result['applied'] == result['calibrated'] == result['baked'] == 4
    assert core.targets(collection) == sorted(objects, key=lambda obj: obj.name)
    for obj in objects:
        assert core.find_modifier(obj) is None and core.reaction(obj) is not None
        assert not obj.modifiers and not obj.material_slots
        assert obj.data != originals[obj] and obj.data.type == originals[obj].type
        assert obj.matrix_world == matrices[obj]
        assert obj.data.animation_data and obj.data.animation_data.drivers
        assert any(energy(obj, frame) > 0 for frame in (13, 37, 61))
    assert (set(scene.objects), scene.camera, scene.world,
            scene.frame_start, scene.frame_end, scene.render.engine) == native
    assert bake.outdated(scene) == []


def test_use_selected_and_build_reaction_accept_mixed_meshes_and_lights():
    scene = bpy.context.scene
    mesh = helpers.plane('My plain mesh')
    lamp = light()
    original_links = {obj: set(obj.users_collection) for obj in (mesh, lamp)}
    for obj in (mesh, lamp):
        obj.select_set(True)
    bpy.context.view_layer.objects.active = lamp
    assert bpy.ops.afterglow.use_selected() == {'FINISHED'}
    assert set(scene.afterglow.collection.all_objects) == {mesh, lamp}
    assert mesh.material_slots and not lamp.material_slots
    assert all(original_links[obj] <= set(obj.users_collection) for obj in (mesh, lamp))
    scene.afterglow.sound = helpers.sound()
    scene.afterglow.easy_glow = False
    result = easy.create_visual(bpy.context)
    assert result['applied'] == result['baked'] == 2
    assert core.find_modifier(mesh) and core.reaction(lamp)
    owned = lamp.data
    count = len(bpy.data.lights)
    assert bpy.ops.afterglow.create_visual() == {'FINISHED'}
    assert lamp.data == owned and len(bpy.data.lights) == count


def test_native_power_follows_the_music_and_is_dark_outside_the_song():
    scene, _, (lamp,) = stage()
    original_power = lamp.data.energy
    core.apply(scene)
    reaction = inputs(lamp, Low=70.0, High=150.0, Decay=0.0,
                      Gain=1.0, Threshold=0.03, Glow=0.0, Intensity=1.0)
    assert bake.bake_all(scene) == 1
    bass, mids = energy(lamp, 13), energy(lamp, 49)
    assert bass > 0.01 * original_power and bass > max(mids * 100, 1e-4), (bass, mids)
    assert energy(lamp, 0) < 1e-6
    assert energy(lamp, 160) < 1e-6
    assert nodes.get_input(reaction, 'Low') == 70.0


def test_light_color_and_brightness_controls_update_without_rebaking():
    scene, _, (lamp,) = stage()
    original_color = tuple(lamp.data.color)
    core.apply(scene)
    with patch.object(bake, 'bake_all', side_effect=AssertionError('Live controls must not bake')):
        inputs(lamp, Baked_Peak=0.5, Threshold=0.1, Gain=2.0, Glow=0.15,
               Intensity=1.3, Use_Colors=True,
               Color_A=(1.0, 0.0, 0.0, 1.0), Color_B=(0.0, 0.0, 1.0, 1.0))
        bpy.context.view_layer.update()
        evaluated = lamp.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
        assert math.isclose(evaluated.energy, 800.0 * 0.8 * 1.3, rel_tol=1e-5), evaluated.energy
        assert all(abs(a - b) < 1e-5 for a, b in zip(evaluated.color, (0.2, 0.0, 0.8)))
        inputs(lamp, Use_Colors=False)
        assert all(abs(a - b) < 1e-5 for a, b in zip(native_color(lamp), original_color))
        assert not helpers.fcurves(lamp)


def test_shared_light_data_is_isolated_and_original_animation_is_restored():
    scene, collection, (first,) = stage()
    original = first.data
    original.energy = 900.0
    original.keyframe_insert('energy', frame=1)
    original.energy = 1200.0
    original.keyframe_insert('energy', frame=30)
    second = bpy.data.objects.new('Second selected light', original)
    collection.objects.link(second)
    outside = bpy.data.objects.new('Unselected light', original)
    scene.collection.objects.link(outside)
    first.location.x = 3.0
    first.keyframe_insert('location', frame=1)
    first.location.x = 7.0
    first.keyframe_insert('location', frame=30)
    original_curves, transform_curves = curves(original), curves(first)
    workflow.create_reaction(scene)
    assert len({first.data, second.data, outside.data}) == 3
    assert outside.data == original and curves(original) == original_curves
    owned = {first.data.name, second.data.name}
    core.remove(scene)
    assert first.data == second.data == outside.data == original
    assert curves(original) == original_curves and curves(first) == transform_curves
    assert all(name not in bpy.data.lights for name in owned)
    assert not core.reaction(first) and not core.reaction(second)


def test_removal_preserves_a_later_manual_light_data_swap():
    scene, _, (lamp,) = stage()
    original = lamp.data
    workflow.create_reaction(scene)
    owned_name = lamp.data.name
    replacement = bpy.data.lights.new('My replacement light', 'SPOT')
    replacement.energy = 321.0
    lamp.data = replacement
    core.remove(scene)
    assert lamp.data == replacement and lamp.data.energy == 321.0
    assert owned_name not in bpy.data.lights and original.name in bpy.data.lights
    assert not core.reaction(lamp)


def test_update_calibrates_and_bakes_managed_lights_outside_the_current_collection():
    scene, collection, objects = stage(('POINT', 'SPOT'))
    workflow.create_reaction(scene)
    other = objects[1]
    inputs(other, Gain=0.123, Threshold=0.5)
    collection.objects.unlink(other)
    scene.collection.objects.link(other)
    assert other not in core.targets(collection)
    result = workflow.create_reaction(scene)
    assert result['applied'] == 1 and result['calibrated'] == result['baked'] == 2
    assert abs(nodes.get_input(core.reaction(other), 'Gain') - 0.123) > 1e-5
    assert bake.outdated(scene) == []
    core.remove(scene)
    assert all(not core.reaction(obj) for obj in objects)


def test_ripple_and_through_objects_use_light_origins_without_geometry_evaluation():
    scene, _, objects = stage(('POINT', 'POINT'))
    scene.afterglow.ripple = True
    scene.afterglow.ripple_from = 'X'
    scene.afterglow.beats_per_sweep = 1.0
    scene.afterglow.through = True
    core.apply(scene)
    for obj in objects:
        inputs(obj, Glow=0.0, Intensity=1.0, Decay=0.0,
               Ripple_Gain=1.0, Ripple_Threshold=0.0)
    assert nodes.get_input(core.reaction(objects[0]), 'Ripple Position') == 0.0
    assert nodes.get_input(core.reaction(objects[1]), 'Ripple Position') == 1.0
    assert bake.bake_all(scene) == 2
    assert energy(objects[0], 7) > 0
    assert energy(objects[1], 7) < 1e-6
    beat = float(bake._beat_lengths([7])[0])
    delayed = 7 + round(beat * scene.afterglow.beats_per_sweep
                        * scene.render.fps / scene.render.fps_base)
    assert energy(objects[1], delayed) > 0
    scene.afterglow.ripple_reverse = True
    core.replace(scene)
    assert nodes.get_input(core.reaction(objects[0]), 'Ripple Position') == 1.0
    assert nodes.get_input(core.reaction(objects[1]), 'Ripple Position') == 0.0


def test_light_only_object_looks_and_animation_guards_share_the_existing_format():
    scene, _, (lamp,) = stage()
    core.apply(scene)
    assert presets.apply(scene, 'NEON_CLUB', scope='OBJECT') == 1
    data = lookbook.capture(scene, scope='OBJECT')
    assert data['format'] == 'afterglow.object-look' and data['version'] == 1
    assert data['object'] is not None
    assert presets.apply(scene, 'AURORA_FLOW', scope='OBJECT') == 1
    assert lookbook.apply(scene, data, scope='OBJECT') == 1
    assert math.isclose(nodes.get_input(core.reaction(lamp), 'Decay'), data['object']['Decay'], rel_tol=1e-5)
    socket = nodes._socket(core.reaction(lamp), 'Color B')
    socket.keyframe_insert('value', frame=1)
    rejected(lambda: presets.apply(scene, 'AURORA_FLOW', scope='OBJECT'))
    rejected(lambda: lookbook.apply(scene, data, scope='OBJECT'))


def test_light_playback_and_manual_control_edits_work_with_fast_playback_disabled():
    scene, _, (lamp,) = stage()
    workflow.create_reaction(scene)
    inputs(lamp, Glow=0.0)
    scene.afterglow.fast_playback = False
    assert energy(lamp, 13) > 0 and bake.outdated(scene) == []
    with patch.object(bake, 'bake_all', wraps=bake.bake_all) as refresh:
        # The scripting alias keeps automatic refresh even though the UI exposes
        # directly animatable native custom properties with explicit refresh.
        lamp.afterglow_light.delay = 0.25
        assert refresh.call_count == 1
    assert bake.outdated(scene) == []
    assert energy(lamp, 3) < 1e-6 and energy(lamp, 20) > 0
    path = nodes._socket(core.reaction(lamp), 'Delay').path_from_id('value')
    lamp[json.loads(path[1:-1])] = 0.5
    lamp.update_tag()
    assert bake.outdated(scene) == [lamp]
    assert bpy.ops.afterglow.bake() == {'FINISHED'}
    assert bake.outdated(scene) == []
    assert energy(lamp, 7) < 1e-6 and energy(lamp, 25) > 0


def test_scene_music_timing_and_ripple_edits_refresh_lights_without_fast_playback():
    scene, _, objects = stage(('POINT', 'SPOT'))
    workflow.create_reaction(scene)
    scene.afterglow.fast_playback = False
    with patch.object(bake, 'bake_all', wraps=bake.bake_all) as refresh:
        scene.afterglow.start_frame = 25
        assert refresh.call_count == 1
        scene.afterglow.ripple = True
        assert refresh.call_count == 2
        scene.afterglow.beats_per_sweep = 2.0
        assert refresh.call_count == 3
        for obj in objects:
            reaction = core.reaction(obj)
            assert nodes.get_input(reaction, 'Start Frame') == 25
            assert nodes.get_input(reaction, 'Ripple')
            assert nodes.get_input(reaction, 'Beats per Sweep') == 2.0
        assert all(energy(obj, 13) < 1e-6 for obj in objects)
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'my-new-silent-track.wav')
            with wave.open(path, 'wb') as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(22050)
                audio.writeframes(bytes(22050 * 2))
            music = bpy.data.sounds.load(path)
            scene.afterglow.sound = music
            assert refresh.call_count == 4
            assert all(nodes.get_input(core.reaction(obj), 'Sound') == music for obj in objects)
            assert bake.outdated(scene) == []
            assert all(energy(obj, 37) < 1e-6 for obj in objects)


def test_advanced_selected_light_ui_exposes_power_and_hides_surface_meter_controls():
    scene, _, (lamp,) = stage()
    core.apply(scene)
    bpy.context.window_manager.afterglow_ui.workflow = 'OBJECT'
    bpy.context.window_manager.afterglow_ui.object_page = 'STYLE'
    bpy.context.view_layer.objects.active = lamp
    lamp.select_set(True)
    log = []

    class Layout:
        def child(self, *args, **kwargs):
            return Layout()

        row = column = box = child

        def panel(self, *args, **kwargs):
            return self.child(), self.child()

        def prop(self, owner, name, **kwargs):
            if name not in owner.bl_rna.properties:
                owner.path_resolve(name)
            log.append((name, kwargs.get('text')))

        def operator(self, *args, **kwargs):
            return SimpleNamespace()

        def label(self, **kwargs):
            pass

        def separator(self, **kwargs):
            pass

    ui.AFTERGLOW_PT_panel.draw(SimpleNamespace(layout=Layout()), bpy.context)
    assert any(label == 'Base Power' for _, label in log)
    assert not {'meter', 'meter_axis', 'meter_invert'}.intersection(name for name, _ in log)


def test_invalid_light_power_in_a_mixed_selection_preserves_plain_mesh_materials():
    scene, collection, (lamp,) = stage()
    mesh = helpers.plane('My untouched mesh', collection)
    original = lamp.data
    lamp.data.energy = float('nan')
    assert not math.isfinite(lamp.data.energy)
    before = (set(bpy.data.collections), set(bpy.data.materials),
              set(bpy.data.lights), tuple(mesh.material_slots), mesh.data)
    rejected(lambda: workflow.use_selected(SimpleNamespace(scene=scene, selected_objects=[mesh, lamp])))
    rejected(lambda: workflow.create_reaction(scene))
    assert (set(bpy.data.collections), set(bpy.data.materials),
            set(bpy.data.lights), tuple(mesh.material_slots), mesh.data) == before
    assert lamp.data == original and core.reaction(lamp) is None


def test_missing_audio_rejects_a_light_build_before_any_changes():
    scene, _, (lamp,) = stage()
    scene.afterglow.sound = None
    before = (lamp.data, set(bpy.data.objects), set(bpy.data.lights), set(bpy.data.actions))
    strips = list(scene.sequence_editor.strips) if scene.sequence_editor else []
    rejected(lambda: workflow.create_reaction(scene))
    assert (lamp.data, set(bpy.data.objects), set(bpy.data.lights), set(bpy.data.actions)) == before
    assert core.reaction(lamp) is None
    assert (list(scene.sequence_editor.strips) if scene.sequence_editor else []) == strips


def test_generated_environment_lights_cannot_become_user_reaction_sources():
    scene, collection, (lamp,) = stage()
    lamp[effect_schema.TAG] = 10
    original = lamp.data
    assert lamp not in core.targets(collection)
    rejected(lambda: workflow.use_selected(SimpleNamespace(scene=scene, selected_objects=[lamp])))
    rejected(lambda: workflow.create_reaction(scene))
    assert lamp.data == original and core.reaction(lamp) is None


def test_light_drivers_and_reaction_survive_saving_and_reopening_the_project():
    scene, _, (lamp,) = stage(('SPOT',))
    original_name = lamp.data.name
    workflow.create_reaction(scene, look_identifier='NEON_CLUB', style_scope='OBJECT')
    scene.afterglow.sound.pack()
    name, owned_name = lamp.name, lamp.data.name
    values = [energy(lamp, frame) for frame in (0, 13, 37, 61, 160)]
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'my-reactive-light.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        bpy.ops.wm.open_mainfile(filepath=str(path))
    scene, lamp = bpy.context.scene, bpy.data.objects[name]
    assert lamp.data.name == owned_name and core.reaction(lamp)
    assert bake.outdated(scene) == []
    after = [energy(lamp, frame) for frame in (0, 13, 37, 61, 160)]
    assert all(math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-5) for a, b in zip(values, after))
    assert workflow.create_reaction(scene)['applied'] == 1
    core.remove(scene)
    assert lamp.data.name == original_name and core.reaction(lamp) is None


helpers.run(globals())
