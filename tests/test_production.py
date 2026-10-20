import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import audition, delivery, production, starter


def test_stage_picker_operator_uses_chosen_design():
    s = bpy.context.scene.afterglow
    for layout, count in (("EQUALIZER", 16), ("TUNNEL", 10)):
        s.stage_layout = layout
        assert bpy.ops.afterglow.create_starter() == {"FINISHED"}
        assert len(s.collection.objects) == count
        if layout == "EQUALIZER":
            assert all(ob.afterglow.meter for ob in s.collection.objects)


def test_excerpt_operator_jumps_to_loop_and_restores_previous_preview():
    scene = bpy.context.scene
    starter.create(bpy.context)
    scene.use_preview_range = True
    scene.frame_preview_start, scene.frame_preview_end = 11, 25
    render_range = scene.frame_start, scene.frame_end
    scene.afterglow.preview_offset = 0.5
    scene.afterglow.preview_duration = 1.0
    assert bpy.ops.afterglow.preview_excerpt() == {"FINISHED"}
    assert scene.frame_current == scene.frame_preview_start == 13
    assert scene.frame_preview_end == 36
    assert (scene.frame_start, scene.frame_end) == render_range
    assert bpy.ops.afterglow.restore_preview() == {"FINISHED"}
    assert (scene.frame_preview_start, scene.frame_preview_end, scene.use_preview_range) == (11, 25, True)


def test_explicit_song_range_respects_tail_choice():
    scene = bpy.context.scene
    starter.create(bpy.context)
    scene.afterglow.output_include_tail = False
    assert bpy.ops.afterglow.song_range() == {"FINISHED"}
    assert (scene.frame_start, scene.frame_end) == (1, 96)
    scene.afterglow.output_include_tail = True
    bpy.ops.afterglow.song_range()
    assert (scene.frame_start, scene.frame_end) == (1, 144)


def test_format_then_explicit_camera_fit_preserves_timing():
    scene = bpy.context.scene
    collection = starter.create(bpy.context)
    camera = starter.camera_for(collection)
    old_transform = camera.matrix_world.copy()
    timing = scene.frame_start, scene.frame_end, scene.render.fps, scene.render.fps_base
    scene.afterglow.delivery_format = "PORTRAIT"
    assert bpy.ops.afterglow.set_output_format() == {"FINISHED"}
    assert (scene.render.resolution_x, scene.render.resolution_y) == (1080, 1920)
    assert camera.matrix_world == old_transform
    assert bpy.ops.afterglow.fit_stage_camera() == {"FINISHED"}
    assert scene.camera == camera and camera.matrix_world != old_transform
    assert (scene.frame_start, scene.frame_end, scene.render.fps, scene.render.fps_base) == timing


def test_prepare_output_applies_fields_without_creating_files():
    scene = bpy.context.scene
    starter.create(bpy.context)
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "clip.mp4"
        scene.afterglow.output_format = "MP4"
        scene.afterglow.output_path = str(target)
        assert bpy.ops.afterglow.prepare_output() == {"FINISHED"}
        assert scene.render.image_settings.media_type == "VIDEO"
        assert scene.render.filepath == str(target) and not target.exists()
        scene.afterglow.output_format = "PNG_SEQUENCE"
        scene.afterglow.output_path = str(Path(folder) / "frames")
        assert bpy.ops.afterglow.prepare_output() == {"FINISHED"}
        assert scene.render.image_settings.media_type == "IMAGE"
        assert scene.render.image_settings.file_format == "PNG"
        assert not scene.render.use_overwrite and not (Path(folder) / "frames").exists()
        assert delivery.status(scene)["ready"]


class Layout:
    def __init__(self, log, enabled=True):
        self.log, self.enabled = log, enabled

    def child(self, *args, **kwargs):
        return Layout(self.log, self.enabled)

    row = column = box = child

    def panel(self, *args, **kwargs):
        return self.child(), self.child()

    def label(self, **kwargs):
        pass

    def prop(self, data, name, **kwargs):
        assert name in data.bl_rna.properties, name

    def operator(self, name, **kwargs):
        props = SimpleNamespace()
        self.log.append((name, self.enabled, props))
        return props


def test_export_panel_disables_heavy_actions_during_playback():
    scene = bpy.context.scene
    starter.create(bpy.context)
    context = SimpleNamespace(scene=scene, screen=SimpleNamespace(is_animation_playing=True))
    log = []
    production.AFTERGLOW_PT_delivery.draw(SimpleNamespace(layout=Layout(log)), context)
    heavy = {"afterglow.preview_excerpt", "afterglow.set_output_format", "afterglow.fit_stage_camera",
             "afterglow.prepare_output", "render.render"}
    assert heavy <= {name for name, _, _ in log}
    assert all(not enabled for name, enabled, _ in log if name in heavy)
    render = next(props for name, _, props in log if name == "render.render")
    assert render.animation is True


def test_output_errors_disable_render_button():
    scene = bpy.context.scene
    scene.camera = None
    log = []
    production.AFTERGLOW_PT_delivery.draw(SimpleNamespace(layout=Layout(log)), bpy.context)
    assert next(enabled for name, enabled, _ in log if name == "render.render") is False


helpers.run(globals())
