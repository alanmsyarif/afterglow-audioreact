"""The default export surface stays small and never renders on failed preparation."""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import production, starter, workflow


class Layout:
    def __init__(self, log, enabled=True):
        self.log, self.enabled = log, enabled

    def child(self, *args, **kwargs):
        return Layout(self.log, self.enabled)

    row = column = box = child

    def panel(self, *args, **kwargs):
        return self.child(), self.child()

    def prop(self, data, name, **kwargs):
        assert name in data.bl_rna.properties, name
        self.log.append(("prop", name))

    def operator(self, name, **kwargs):
        self.log.append(("op", name, self.enabled))
        return SimpleNamespace()

    def label(self, **kwargs):
        pass


def draw(context):
    log = []
    production.AFTERGLOW_PT_delivery.draw(SimpleNamespace(layout=Layout(log)), context)
    return log


def prepared_scene():
    scene = bpy.context.scene
    starter.create(bpy.context)
    workflow.create_show(scene, look_identifier="NEON_CLUB")
    return scene


def execute_export(context):
    reports = []
    operator = SimpleNamespace(report=lambda levels, message: reports.append((levels, message)))
    result = production.AFTERGLOW_OT_export_video.execute(operator, context)
    return result, reports


def test_default_export_has_two_choices_and_one_action():
    assert not bpy.context.scene.afterglow.show_advanced
    log = draw(bpy.context)
    assert {entry[1] for entry in log if entry[0] == "prop"} == {"delivery_format", "output_path"}
    assert [entry[1] for entry in log if entry[0] == "op"] == ["afterglow.export_video"]
    assert ("op", "afterglow.export_video", False) in log  # Nothing has been created yet.
    assert production.AFTERGLOW_PT_delivery.bl_parent_id == "AFTERGLOW_PT_panel"


def test_default_export_is_available_after_creation_and_disabled_during_playback():
    scene = prepared_scene()
    assert ("op", "afterglow.export_video", True) in draw(bpy.context)
    playing = SimpleNamespace(scene=scene, screen=SimpleNamespace(is_animation_playing=True))
    assert ("op", "afterglow.export_video", False) in draw(playing)
    assert not production.AFTERGLOW_OT_export_video.poll(playing)


def test_export_prepares_mp4_range_and_shape_before_starting_render():
    scene = prepared_scene()
    scene.afterglow.delivery_format = "SQUARE"
    original = production._start_animation_render
    calls = []
    def render(context):
        current = context.scene
        calls.append((current.render.image_settings.media_type, current.render.image_settings.file_format,
                      current.render.resolution_x, current.render.resolution_y,
                      current.frame_start, current.frame_end, current.render.filepath))
        return {"FINISHED"}
    production._start_animation_render = render
    try:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "finished.mp4"
            scene.afterglow.output_path = str(path)
            result, reports = execute_export(bpy.context)
            assert result == {"FINISHED"}, reports
            assert calls == [("VIDEO", "FFMPEG", 1080, 1080, 1, 144, str(path))], calls
            assert not path.exists()  # Rendering is deliberately stubbed; preparation writes no media.
    finally:
        production._start_animation_render = original


def test_existing_output_never_starts_render_or_overwrites_file():
    scene = prepared_scene()
    original = production._start_animation_render
    calls = []
    production._start_animation_render = lambda context: calls.append(context) or {"FINISHED"}
    try:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "keep.mp4"
            path.write_bytes(b"Existing finished video")
            scene.afterglow.output_path = str(path)
            result, reports = execute_export(bpy.context)
            assert result == {"CANCELLED"}
            assert reports and "ERROR" in reports[0][0]
            assert not calls and path.read_bytes() == b"Existing finished video"
    finally:
        production._start_animation_render = original


def test_invalid_scene_never_starts_render():
    original = production._start_animation_render
    calls = []
    production._start_animation_render = lambda context: calls.append(context) or {"FINISHED"}
    try:
        result, reports = execute_export(bpy.context)
        assert result == {"CANCELLED"} and reports and not calls
    finally:
        production._start_animation_render = original


def test_native_render_failure_is_reported_without_a_success_claim():
    scene = prepared_scene()
    original = production._start_animation_render
    def failed_render(context):
        raise RuntimeError("Test render device unavailable")
    production._start_animation_render = failed_render
    try:
        with tempfile.TemporaryDirectory() as folder:
            scene.afterglow.output_path = str(Path(folder) / "render.mp4")
            result, reports = execute_export(bpy.context)
            assert result == {"CANCELLED"}
            assert any("Could not start the video render" in message for _, message in reports)
    finally:
        production._start_animation_render = original


helpers.run(globals())
