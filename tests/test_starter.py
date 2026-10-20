import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import numpy as np
import helpers
from helpers import emission_material, plane, snapshot, sound
from afterglow import core, effects, starter
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector


def test_starter_is_self_contained_and_every_column_can_react():
    scene = bpy.context.scene
    venue = starter.create(bpy.context)
    columns = core.targets(venue)
    assert len(columns) == 12
    assert len({obj.data for obj in columns}) == 12
    assert len({obj.active_material for obj in columns}) == 12
    assert scene.camera is not None
    assert scene.afterglow.sound.packed_file is not None
    assert not os.path.exists(bpy.path.abspath(scene.afterglow.sound.filepath))
    samples, rate = core.load_mono(scene.afterglow.sound)
    assert rate == 44100 and len(samples) == 4 * rate
    assert np.isfinite(samples).all() and np.max(np.abs(samples)) > 0.5
    applied, skipped, outside, linked = core.apply(scene)
    assert len(applied) == 12 and not skipped and not outside and not linked
    scene.afterglow.effects.strikes.enabled = True
    events = effects.analyze(scene)
    assert len(events["strikes"]) >= 6, events


def test_starter_preserves_existing_scene_and_selected_audio():
    scene = bpy.context.scene
    existing = plane("User object", material=emission_material("User material"))
    original_material = snapshot(existing.active_material)
    existing.select_set(True)
    bpy.context.view_layer.objects.active = existing
    camera = bpy.data.objects.new("User camera", bpy.data.cameras.new("User camera"))
    scene.collection.objects.link(camera)
    scene.camera = camera
    world = bpy.data.worlds.new("User world")
    scene.world = world
    chosen_sound = sound()
    scene.afterglow.sound = chosen_sound
    scene.afterglow.start_frame = 30
    scene.frame_set(45)
    scene.render.fps = 30
    before = (scene.frame_start, scene.frame_end, scene.render.engine)
    venue = starter.create(bpy.context)
    assert scene.camera == camera and scene.world == world
    assert scene.afterglow.sound == chosen_sound and not chosen_sound.packed_file
    assert scene.afterglow.start_frame == 30 and scene.frame_current == 45
    assert scene.render.fps == 30
    assert before == (scene.frame_start, scene.frame_end, scene.render.engine)
    assert snapshot(existing.active_material) == original_material
    assert existing.select_get() and bpy.context.view_layer.objects.active == existing
    assert existing not in venue.all_objects.values()


def test_repeated_starters_are_independent_and_keep_the_first_sound():
    scene = bpy.context.scene
    first = starter.create(bpy.context)
    first_sound = scene.afterglow.sound
    second = starter.create(bpy.context)
    assert first != second and scene.afterglow.collection == second
    assert len(first.objects) == len(second.objects) == 12
    assert not set(first.objects) & set(second.objects)
    assert scene.afterglow.sound == first_sound and len(bpy.data.sounds) == 1
    assert all(core.find_modifier(obj) is None for obj in second.objects)
    assert starter.camera_for(first) != starter.camera_for(second)
    assert starter.camera_for(first) == scene.camera


def test_camera_lookup_survives_nesting_and_rejects_unrelated_collections():
    venue = starter.create(bpy.context)
    camera = starter.camera_for(venue)
    stage = next(coll for coll in bpy.data.collections if venue.name in coll.children)
    nested = bpy.data.collections.new("User grouping")
    stage.children.link(nested)
    nested.children.link(venue)
    stage.children.unlink(venue)
    assert starter.camera_for(venue) == camera
    assert starter.camera_for(None) is None
    assert starter.camera_for(bpy.data.collections.new("Unrelated")) is None


def test_starter_follows_the_cursor_without_moving_it():
    scene = bpy.context.scene
    scene.cursor.location = (12, -5, 2)
    venue = starter.create(bpy.context)
    positions = np.array([obj.location[:] for obj in venue.objects])
    assert np.allclose(positions[:, :2].mean(axis=0), (12, -5))
    assert np.min(positions[:, 2]) > 2
    assert tuple(scene.cursor.location) == (12, -5, 2)


def test_starter_camera_frames_the_floor_and_default_effect_height():
    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
    venue = starter.create(bpy.context)
    camera = starter.camera_for(venue)
    fitted = effects.make_venue(scene)
    points = [camera.users_collection[0].objects["Afterglow Stage"].matrix_world @ vertex.co
              for vertex in camera.users_collection[0].objects["Afterglow Stage"].data.vertices]
    for step in range(32):
        point = fitted.path_point(step / 32)
        points.append(Vector((point.x, point.y, fitted.z1)))
    for point in points:
        co = world_to_camera_view(scene, camera, point)
        assert 0.04 <= co.x <= 0.96 and 0.04 <= co.y <= 0.96 and co.z > 0, co


def test_new_templates_are_distinct_and_every_target_can_react():
    scene = bpy.context.scene
    for layout, count in (("EQUALIZER", 16), ("TUNNEL", 10)):
        venue = starter.create(bpy.context, layout=layout)
        objects = core.targets(venue)
        assert venue[starter.LAYOUT_KEY] == layout
        assert len(objects) == count
        assert len({obj.data for obj in objects}) == count
        assert len({obj.active_material for obj in objects}) == count
        assert all(obj.afterglow.meter for obj in objects) == (layout == "EQUALIZER")
        applied, skipped, outside, linked = core.apply(scene)
        assert len(applied) == count and not skipped and not outside and not linked
        assert starter.camera_for(venue) is not None


def test_new_templates_preserve_existing_content_audio_and_camera():
    scene = bpy.context.scene
    user = plane("Existing", material=emission_material("User material"))
    user.select_set(True)
    bpy.context.view_layer.objects.active = user
    camera = bpy.data.objects.new("Existing camera", bpy.data.cameras.new("Existing camera"))
    scene.collection.objects.link(camera)
    scene.camera = camera
    scene.afterglow.sound = sound()
    scene.afterglow.start_frame = 17
    scene.frame_set(23)
    scene.render.fps, scene.render.fps_base = 30, 1.001
    original_material = snapshot(user.active_material)
    original = (scene.afterglow.sound, scene.world, scene.render.engine, scene.render.fps,
                scene.render.fps_base, scene.frame_start, scene.frame_end, scene.frame_current)
    first = starter.create(bpy.context, "EQUALIZER")
    second = starter.create(bpy.context, "TUNNEL")
    assert original == (scene.afterglow.sound, scene.world, scene.render.engine, scene.render.fps,
                        scene.render.fps_base, scene.frame_start, scene.frame_end, scene.frame_current)
    assert scene.camera == camera and user.select_get()
    assert bpy.context.view_layer.objects.active == user
    assert snapshot(user.active_material) == original_material
    assert not set(first.objects).intersection(second.objects)
    assert starter.camera_for(first) != starter.camera_for(second)


def test_unknown_template_rejects_before_creating_data():
    scene = bpy.context.scene
    before = (len(bpy.data.objects), len(bpy.data.collections), len(bpy.data.sounds), scene.afterglow.collection)
    try:
        starter.create(bpy.context, "UNKNOWN")
    except ValueError as error:
        assert "Unknown starter layout" in str(error)
    else:
        raise AssertionError("unknown template must fail")
    assert before == (len(bpy.data.objects), len(bpy.data.collections), len(bpy.data.sounds), scene.afterglow.collection)


def test_explicit_camera_refit_uses_moved_geometry_and_portrait_aspect():
    scene = bpy.context.scene
    venue = starter.create(bpy.context, "TUNNEL")
    camera = starter.camera_for(venue)
    user_camera = bpy.data.objects.new("User active camera", bpy.data.cameras.new("User active camera"))
    scene.collection.objects.link(user_camera)
    scene.camera = user_camera
    stage = camera.users_collection[0]
    for obj in stage.all_objects:
        if obj.type == "MESH":
            obj.location += Vector((11, -7, 3))
            obj.scale.x = 1.2
    scene.render.resolution_x, scene.render.resolution_y = 1080, 1920
    original_range = (scene.frame_start, scene.frame_end, scene.render.fps)
    assert starter.fit_camera(scene, venue) == camera
    assert scene.camera == user_camera
    assert original_range == (scene.frame_start, scene.frame_end, scene.render.fps)
    fitted = effects.make_venue(scene)
    points = [obj.matrix_world @ vertex.co for obj in stage.all_objects if obj.type == "MESH"
              for vertex in obj.data.vertices]
    for step in range(64):
        p = fitted.path_point(step / 64)
        points.append(Vector((p.x, p.y, fitted.z1)))
    for p in points:
        co = world_to_camera_view(scene, camera, p)
        assert 0.04 <= co.x <= 0.96 and 0.04 <= co.y <= 0.96 and co.z > 0, co


def test_refit_protects_animated_generated_camera_before_changes():
    scene = bpy.context.scene
    venue = starter.create(bpy.context, "EQUALIZER")
    camera = starter.camera_for(venue)
    camera.keyframe_insert("location", frame=1)
    before = tuple(tuple(row) for row in camera.matrix_world)
    try:
        starter.fit_camera(scene, venue)
    except ValueError as error:
        assert "animation and drivers" in str(error)
    else:
        raise AssertionError("animated camera must not be overwritten")
    assert before == tuple(tuple(row) for row in camera.matrix_world)


helpers.run(globals())
