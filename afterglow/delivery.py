"""Explicit render formats and output preparation without rendering or writing files."""
import os
from pathlib import Path

import bpy

FORMAT_ITEMS = (
    ("LANDSCAPE", "Landscape · 16:9", "1920 × 1080 for widescreen music videos"),
    ("PORTRAIT", "Portrait · 9:16", "1080 × 1920 for vertical music visuals"),
    ("SQUARE", "Square · 1:1", "1080 × 1080 for square posts and loops"),
)
RESOLUTIONS = {"LANDSCAPE": (1920, 1080), "PORTRAIT": (1080, 1920), "SQUARE": (1080, 1080)}
OUTPUT_ITEMS = (
    ("PNG_SEQUENCE", "PNG Sequence", "RGBA image frames; existing frames are protected"),
    ("MP4", "MP4 · H.264", "H.264 video with AAC audio when available"),
)


def _editable(scene):
    if not scene.is_editable:
        raise ValueError("Make the scene local before changing its output settings")


def _enum(data, property_name, value):
    prop = data.bl_rna.properties[property_name]
    if value not in {item.identifier for item in prop.enum_items}:
        raise ValueError(f"This Blender build does not support {value} for {property_name}")
    if data.is_property_readonly(property_name):
        raise ValueError(f"The scene's {property_name} setting is read-only")


def _writable_directory(directory):
    """Check the nearest existing parent, without creating test files or directories."""
    parent = directory
    while not parent.exists() and parent.parent != parent:
        parent = parent.parent
    if not parent.is_dir():
        raise ValueError("An output parent is a file; choose another output folder")
    if not os.access(parent, os.W_OK | os.X_OK):
        raise ValueError("The output folder or its nearest existing parent is not writable")


def _absolute(path):
    if not isinstance(path, (str, os.PathLike)):
        raise ValueError("Choose an output folder or an MP4 filename")
    text = os.fspath(path).strip()
    if not text or any(ord(char) < 32 for char in text):
        raise ValueError("Choose a non-empty output path without control characters")
    if text.startswith("//"):
        if not bpy.data.filepath:
            raise ValueError("Save the .blend first to use a // relative output path, or choose an absolute path")
        return Path(bpy.path.abspath(text)), text.endswith(("/", "\\"))
    expanded = Path(text).expanduser()
    if not expanded.is_absolute():
        raise ValueError("Use an absolute output path, or a // path relative to a saved .blend")
    return expanded, text.endswith(("/", "\\"))


def _output_path(output_format, path):
    resolved, trailing_separator = _absolute(path)
    if output_format == "PNG_SEQUENCE":
        if resolved.suffix.lower() in {".png", ".jpg", ".jpeg", ".mp4", ".mov"} and not trailing_separator:
            raise ValueError("PNG Sequence needs an output folder, not a media filename")
        if resolved.exists() and not resolved.is_dir():
            raise ValueError("PNG Sequence needs an output folder, not an existing file")
        _writable_directory(resolved)
        return resolved / "frame_"
    if trailing_separator or resolved.is_dir():
        resolved = resolved / "afterglow.mp4"
    elif not resolved.suffix:
        resolved = resolved.with_suffix(".mp4")
    elif resolved.suffix.lower() != ".mp4":
        raise ValueError("Choose an .mp4 filename or an output folder for H.264 video")
    if resolved.exists():
        raise ValueError("That MP4 already exists; choose a new filename to protect the existing video")
    _writable_directory(resolved.parent)
    return resolved


def apply_format(scene, identifier):
    """Apply a square-pixel size preset, retaining timing, camera and scene appearance."""
    _editable(scene)
    if identifier not in RESOLUTIONS:
        raise ValueError("Choose Landscape, Portrait or Square output")
    render = scene.render
    for name in ("resolution_x", "resolution_y", "resolution_percentage", "pixel_aspect_x", "pixel_aspect_y"):
        if render.is_property_readonly(name):
            raise ValueError(f"The scene's {name} setting is read-only")
    width, height = RESOLUTIONS[identifier]
    render.resolution_x, render.resolution_y = width, height
    render.resolution_percentage = 100
    render.pixel_aspect_x = render.pixel_aspect_y = 1.0
    return width, height


def configure_output(scene, output_format, path):
    """Prepare PNG frames or H.264/AAC MP4, returning the absolute render path.

    PNG uses <folder>/frame_0001.png and disables frame overwrites. MP4 accepts a
    filename (adding .mp4 when absent) or an existing/trailing-slash directory,
    where it uses afterglow.mp4. No output directory or media file is created here.
    """
    _editable(scene)
    if output_format not in {"PNG_SEQUENCE", "MP4"}:
        raise ValueError("Choose PNG Sequence or MP4 output")
    render, image = scene.render, scene.render.image_settings
    target = _output_path(output_format, path)
    if render.is_property_readonly("filepath"):
        raise ValueError("The scene's output path is read-only")
    _enum(image, "media_type", "IMAGE" if output_format == "PNG_SEQUENCE" else "VIDEO")
    _enum(image, "file_format", "PNG" if output_format == "PNG_SEQUENCE" else "FFMPEG")
    _enum(image, "color_mode", "RGBA" if output_format == "PNG_SEQUENCE" else "RGB")
    _enum(image, "color_depth", "8")
    if output_format == "MP4":
        if not bpy.app.build_options.codec_ffmpeg:
            raise ValueError("This Blender build has no FFmpeg video support; choose PNG Sequence instead")
        width = render.resolution_x * render.resolution_percentage // 100
        height = render.resolution_y * render.resolution_percentage // 100
        if width < 2 or height < 2 or width % 2 or height % 2:
            raise ValueError("H.264 needs even output width and height; apply a format preset or adjust resolution")
        _enum(render.ffmpeg, "format", "MPEG4")
        _enum(render.ffmpeg, "codec", "H264")
        _enum(render.ffmpeg, "constant_rate_factor", "HIGH")
        _enum(render.ffmpeg, "ffmpeg_preset", "GOOD")
        audio_choices = {item.identifier for item in render.ffmpeg.bl_rna.properties["audio_codec"].enum_items}
        audio = "AAC" if "AAC" in audio_choices else "NONE"
        _enum(render.ffmpeg, "audio_codec", audio)
    image.media_type = "IMAGE" if output_format == "PNG_SEQUENCE" else "VIDEO"
    image.file_format = "PNG" if output_format == "PNG_SEQUENCE" else "FFMPEG"
    image.color_mode, image.color_depth = ("RGBA" if output_format == "PNG_SEQUENCE" else "RGB"), "8"
    render.use_file_extension = True
    if output_format == "PNG_SEQUENCE":
        render.use_overwrite = False
    else:
        render.ffmpeg.format = "MPEG4"
        render.ffmpeg.codec = "H264"
        render.ffmpeg.audio_codec = audio
        render.ffmpeg.constant_rate_factor = "HIGH"
        render.ffmpeg.ffmpeg_preset = "GOOD"
    render.filepath = str(target)
    return render.filepath


def status(scene):
    """Cheap, read-only readiness for actual Blender render settings, not UI draft fields.

    Full scene/build validation belongs to the existing reaction and effects workflows;
    this helper deliberately avoids per-object scans and audio decoding on redraw.
    """
    render = scene.render
    errors, warnings = [], []
    if scene.camera is None or scene.camera.name not in scene.objects:
        errors.append("Choose an active camera in this scene before rendering")
    output_format = ("MP4" if render.image_settings.file_format == "FFMPEG"
                     else "PNG_SEQUENCE" if render.image_settings.file_format == "PNG" else "OTHER")
    target = directory = None
    try:
        target, _ = _absolute(render.filepath)
        directory = target if target.is_dir() else target.parent
        _writable_directory(directory)
        if output_format == "MP4" and target.is_file():
            errors.append("The output video already exists; prepare a new filename before rendering")
    except (ValueError, OSError) as error:
        errors.append(str(error))
    width = render.resolution_x * render.resolution_percentage // 100
    height = render.resolution_y * render.resolution_percentage // 100
    if output_format == "MP4" and (width < 2 or height < 2 or width % 2 or height % 2):
        errors.append("H.264 needs an even output width and height")
    if output_format == "OTHER":
        warnings.append("Current render output uses a custom Blender format")
    s = getattr(scene, "afterglow", None)
    if s:
        if s.effects.solo_layer != "ALL":
            warnings.append("Layer solo is active and will affect the rendered show")
        if s.effects.master_brightness == 0:
            warnings.append("Effect lights and graphics are muted by Master Brightness")
    if scene.use_preview_range and (scene.frame_preview_start, scene.frame_preview_end) != (scene.frame_start, scene.frame_end):
        warnings.append("The playback preview range differs from the animation render range")
    return {"ready": not errors, "errors": errors, "warnings": warnings,
            "path": str(target) if target else None,
            "directory": str(directory) if directory else None,
            "format": output_format, "resolution": (width, height),
            "frames": (scene.frame_start, scene.frame_end),
            "fps": render.fps / render.fps_base}
