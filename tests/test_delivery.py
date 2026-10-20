import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import numpy as np
import helpers
from afterglow import delivery


def camera(scene):
    ob = bpy.data.objects.new("Output camera", bpy.data.cameras.new("Output camera"))
    scene.collection.objects.link(ob)
    ob.location = (0, 0, 5)
    scene.camera = ob
    return ob


def preserved(scene):
    r = scene.render
    return (r.fps, r.fps_base, scene.frame_start, scene.frame_end,
            scene.use_preview_range, scene.frame_preview_start, scene.frame_preview_end,
            scene.camera, scene.world, scene.compositing_node_group, r.engine, r.film_transparent)


def output_state(scene):
    r = scene.render
    return (r.filepath, r.image_settings.media_type, r.image_settings.file_format,
            r.image_settings.color_mode, r.image_settings.color_depth,
            r.ffmpeg.format, r.ffmpeg.codec, r.ffmpeg.audio_codec, r.use_overwrite)


def test_all_output_sizes_preserve_scene_and_timing():
    scene = bpy.context.scene
    camera(scene)
    scene.render.fps = 30
    scene.render.fps_base = 1.001
    scene.frame_start, scene.frame_end = 51, 333
    scene.use_preview_range = True
    scene.frame_preview_start, scene.frame_preview_end = 100, 150
    scene.render.film_transparent = True
    before = preserved(scene)
    for identifier, size in delivery.RESOLUTIONS.items():
        assert delivery.apply_format(scene, identifier) == size
        assert (scene.render.resolution_x, scene.render.resolution_y) == size
        assert scene.render.resolution_percentage == 100
        assert scene.render.pixel_aspect_x == scene.render.pixel_aspect_y == 1
        assert preserved(scene) == before


def test_invalid_format_keeps_every_resolution_setting():
    scene = bpy.context.scene
    before = (scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage,
              scene.render.pixel_aspect_x, scene.render.pixel_aspect_y)
    try:
        delivery.apply_format(scene, "UNKNOWN")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown output format must fail")
    assert before == (scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage,
                      scene.render.pixel_aspect_x, scene.render.pixel_aspect_y)


def test_png_output_does_not_create_directories_or_change_scene_appearance():
    scene = bpy.context.scene
    camera(scene)
    scene.render.film_transparent = False
    before = preserved(scene)
    with tempfile.TemporaryDirectory() as folder:
        directory = Path(folder) / "new" / "frames"
        result = delivery.configure_output(scene, "PNG_SEQUENCE", str(directory))
        assert result == str(directory / "frame_")
        assert not directory.exists()
        assert scene.render.image_settings.media_type == "IMAGE"
        assert scene.render.image_settings.file_format == "PNG"
        assert scene.render.image_settings.color_mode == "RGBA"
        assert scene.render.image_settings.color_depth == "8"
        assert not scene.render.use_overwrite
        assert preserved(scene) == before
        assert delivery.status(scene)["ready"]


def test_mp4_and_png_switch_media_type_and_preserve_timing():
    scene = bpy.context.scene
    camera(scene)
    scene.render.fps = 25
    before = preserved(scene)
    with tempfile.TemporaryDirectory() as folder:
        result = delivery.configure_output(scene, "MP4", folder + "/")
        assert result == str(Path(folder) / "afterglow.mp4")
        assert not Path(result).exists()
        assert scene.render.image_settings.media_type == "VIDEO"
        assert scene.render.image_settings.file_format == "FFMPEG"
        assert scene.render.ffmpeg.format == "MPEG4" and scene.render.ffmpeg.codec == "H264"
        assert scene.render.ffmpeg.audio_codec == "AAC"
        delivery.configure_output(scene, "PNG_SEQUENCE", folder)
        assert scene.render.image_settings.media_type == "IMAGE"
        assert scene.render.image_settings.file_format == "PNG"
        assert scene.render.image_settings.color_mode == "RGBA"
        assert preserved(scene) == before


def test_invalid_paths_formats_and_odd_video_size_are_atomic():
    scene = bpy.context.scene
    before = output_state(scene)
    with tempfile.TemporaryDirectory() as folder:
        blocked = Path(folder) / "existing.mp4"
        blocked.write_bytes(b"keep video")
        cases = [("MP4", str(blocked)), ("PNG_SEQUENCE", str(blocked)),
                 ("MP4", str(Path(folder) / "movie.mov")), ("UNKNOWN", folder),
                 ("MP4", "relative/movie.mp4"), ("PNG_SEQUENCE", ""),
                 ("PNG_SEQUENCE", "//unsaved/"), ("MP4", str(Path(folder) / "bad\nname.mp4"))]
        for output_format, path in cases:
            try:
                delivery.configure_output(scene, output_format, path)
            except ValueError:
                pass
            else:
                raise AssertionError((output_format, path))
            assert output_state(scene) == before
        assert blocked.read_bytes() == b"keep video"
        scene.render.resolution_x = 35
        scene.render.resolution_percentage = 100
        try:
            delivery.configure_output(scene, "MP4", str(Path(folder) / "odd.mp4"))
        except ValueError as error:
            assert "even" in str(error)
        else:
            raise AssertionError("odd-sized H.264 must fail before changing output settings")
        assert output_state(scene) == before


def test_saved_blend_relative_output_resolves_against_project():
    scene = bpy.context.scene
    with tempfile.TemporaryDirectory() as folder:
        bpy.ops.wm.save_as_mainfile(filepath=str(Path(folder) / "project.blend"))
        path = delivery.configure_output(scene, "PNG_SEQUENCE", "//exports/frames/")
        assert Path(path) == Path(folder) / "exports" / "frames" / "frame_"
        assert not (Path(folder) / "exports").exists()


def test_status_explains_camera_solo_mute_and_range_without_mutation():
    scene = bpy.context.scene
    scene.afterglow.effects.solo_layer = "strikes"
    scene.afterglow.effects.master_brightness = 0
    scene.use_preview_range = True
    scene.frame_preview_start, scene.frame_preview_end = 5, 8
    before = output_state(scene)
    state = delivery.status(scene)
    assert not state["ready"] and any("camera" in error for error in state["errors"])
    assert any("solo" in warning for warning in state["warnings"])
    assert any("muted" in warning for warning in state["warnings"])
    assert any("preview range" in warning for warning in state["warnings"])
    assert output_state(scene) == before


def render_fixture(scene):
    camera(scene)
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = 1
    scene.render.resolution_x = scene.render.resolution_y = 32
    scene.render.resolution_percentage = 100
    scene.frame_start, scene.frame_end = 1, 2


def test_render_two_png_frames_without_overwriting_existing_frame():
    scene = bpy.context.scene
    render_fixture(scene)
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / "frames"
        delivery.configure_output(scene, "PNG_SEQUENCE", str(output))
        output.mkdir()
        first = output / "frame_0001.png"
        first.write_bytes(b"Existing frame must stay untouched")
        assert bpy.ops.render.render(animation=True) == {"FINISHED"}
        assert first.read_bytes() == b"Existing frame must stay untouched"
        second = output / "frame_0002.png"
        assert second.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        image = bpy.data.images.load(str(second), check_existing=False)
        assert tuple(image.size) == (32, 32)
        bpy.data.images.remove(image)


def test_render_two_frame_h264_video_with_non_silent_aac_soundtrack():
    scene = bpy.context.scene
    render_fixture(scene)
    scene.sequence_editor_create().strips.new_sound("Delivery audio", helpers.tone_wav(), channel=1, frame_start=1)
    with tempfile.TemporaryDirectory() as folder:
        path = delivery.configure_output(scene, "MP4", str(Path(folder) / "music-visual"))
        assert bpy.ops.render.render(animation=True) == {"FINISHED"}
        assert Path(path).is_file() and Path(path).stat().st_size > 1000
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                "stream=codec_name,codec_type,width,height,nb_frames", "-of", "json", path],
                               check=True, capture_output=True, text=True)
        streams = json.loads(probe.stdout)["streams"]
        video = next(stream for stream in streams if stream["codec_type"] == "video")
        audio = next(stream for stream in streams if stream["codec_type"] == "audio")
        assert video["codec_name"] == "h264" and int(video["nb_frames"]) == 2
        assert video["width"] == video["height"] == 32
        assert audio["codec_name"] == "aac"
        decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-f", "f32le",
                                  "-acodec", "pcm_f32le", "-"], check=True, capture_output=True)
        samples = np.frombuffer(decoded.stdout, np.float32)
        assert len(samples) > 0 and abs(samples).max() > 0.1


helpers.run(globals())
