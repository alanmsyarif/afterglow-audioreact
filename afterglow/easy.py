"""One coordinated creation action and one export preparation for the simple UI.

Existing scenes and generated stage objects remain editable. AUTO reuses a chosen
collection; only an empty setup creates a stage automatically. Export prepares a
whole-track H.264 movie without starting a render or overwriting an existing file.
"""
import math

import numpy as np
import bpy

from . import audition, bake, core, delivery, effects, finish, presets, starter, workflow


def _sound_preflight(scene, required):
    sound = scene.afterglow.sound
    if sound is None:
        if required:
            raise ValueError("Load a track before creating this visual")
        return
    mono, rate = core.load_mono(sound, "Create Visual")
    if not len(mono) or not math.isfinite(rate) or rate <= 0 or not np.isfinite(mono).all():
        raise ValueError("Choose a track with readable, finite audio samples")


def _glow_preflight(scene, enabled):
    if not scene.is_editable:
        raise ValueError("Make the scene local before creating a visual")
    if not enabled:
        return
    if not scene.render.use_compositing:
        raise ValueError("Enable Compositing in Render Post Processing, or turn off Soft Glow")
    tree = scene.compositing_node_group
    if tree:
        _, node = finish._target(tree)
        if not finish.enabled(scene) and node and any(output.is_linked for output in node.outputs):
            raise ValueError("The Afterglow glow was routed elsewhere; remove it in Advanced before creating the visual")


def _stage_matches(collection, layout):
    if not collection or not starter.camera_for(collection):
        return False
    camera = starter.camera_for(collection)
    stage = next((parent for parent in camera.users_collection if parent.get(starter.TAG)), None)
    return bool(stage and stage.get(starter.LAYOUT_KEY, "RING") == layout)


def create_visual(context):
    """Create the selected complete look, automatically handling stage, glow and timing.

    AUTO retains any current collection. STAGE adds the chosen template unless the
    current collection already belongs to that template, and CUSTOM requires a chosen
    collection. Repeated creation reuses the same stage and the same effect build.
    A new stage selects its generated camera; existing camera objects are not edited.
    With a reused/custom scene, an active user camera remains selected. Simple
    creation restores the complete lighting mix; Advanced creation retains it.
    """
    scene = context.scene
    s = scene.afterglow
    source = getattr(s, "easy_source", "AUTO")
    if source not in {"AUTO", "STAGE", "CUSTOM"}:
        raise ValueError("Choose Automatic, Starter Stage or My Objects")
    if source == "CUSTOM" and s.collection is None:
        raise ValueError("Choose your emissive collection before creating a visual")
    if s.stage_layout not in {item[0] for item in starter.LAYOUT_ITEMS}:
        raise ValueError("Choose an available stage design")
    create_stage = ((source == "AUTO" and s.collection is None)
                    or (source == "STAGE" and not _stage_matches(s.collection, s.stage_layout)))
    _sound_preflight(scene, required=not create_stage)
    if s.sound is not None:
        duration, fps, start = audition._track(scene)
    else:
        duration, fps, start = starter.DURATION, scene.render.fps / scene.render.fps_base, s.start_frame
    audition._bounds(scene, start, start + math.ceil((duration + bake.TAIL_S) * fps) - 1)
    presets.preflight(scene, s.creative_look)
    _glow_preflight(scene, getattr(s, "easy_glow", True))
    if not create_stage:
        state = workflow.inspect(scene)
        if not state["ready"]:
            raise ValueError(state["reason"])
        workflow._effects_preflight(scene, presets.enabled_layers(s.creative_look))

    if create_stage:
        starter.create(context, layout=s.stage_layout)
    camera = starter.camera_for(s.collection)
    # Fit before effects are keyed: camera-directed effects sample the final camera.
    if camera is not None and (create_stage or scene.camera is None or scene.camera == camera):
        starter.fit_camera(scene, s.collection)
        scene.camera = camera
    # A newly generated source may introduce routing prerequisites; check them
    # before resetting any hidden preview mixer state.
    workflow._effects_preflight(scene, presets.enabled_layers(s.creative_look))
    if not getattr(s, "show_advanced", False):
        s.effects.solo_layer = "ALL"
        s.effects.master_brightness = 1.0
    result = workflow.create_show(scene, look_identifier=s.creative_look)
    if getattr(s, "easy_glow", True):
        finish.enable(scene)
    else:
        finish.disable(scene)
    audition.set_song_range(scene, include_tail=True)
    scene.frame_set(s.start_frame)
    return {**result, "created_stage": create_stage, "camera": scene.camera,
            "glow": finish.enabled(scene), "warnings": delivery.status(scene)["warnings"]}


def _show_preflight(scene):
    objects = [obj for obj in scene.objects if core.find_modifier(obj)]
    if not objects or not effects._collections(scene):
        raise ValueError("Create Visual first, then export the finished show")
    if (scene.afterglow.fast_playback and bake.outdated(scene)) or effects.outdated(scene):
        raise ValueError("Update Visual before exporting; the built show no longer matches its settings")
    if scene.camera is None or scene.camera.name not in scene.objects:
        raise ValueError("Choose an active camera before exporting the visual")


def _export_state(scene):
    render = scene.render
    return {
        "render": {name: getattr(render, name) for name in
                   ("resolution_x", "resolution_y", "resolution_percentage", "pixel_aspect_x", "pixel_aspect_y",
                    "filepath", "use_file_extension", "use_overwrite")},
        "image": {name: getattr(render.image_settings, name) for name in
                  ("media_type", "file_format", "color_mode", "color_depth")},
        "ffmpeg": {name: getattr(render.ffmpeg, name) for name in
                   ("format", "codec", "audio_codec", "constant_rate_factor", "ffmpeg_preset")},
        "range": (scene.frame_start, scene.frame_end),
        "camera": scene.camera,
        "matrix": scene.camera.matrix_world.copy(),
    }


def _restore_export(scene, state):
    render = scene.render
    for name, value in state["render"].items():
        setattr(render, name, value)
    for name, value in state["image"].items():
        setattr(render.image_settings, name, value)
    for name, value in state["ffmpeg"].items():
        setattr(render.ffmpeg, name, value)
    scene.frame_start, scene.frame_end = state["range"]
    if state["camera"].matrix_world != state["matrix"]:
        state["camera"].matrix_world = state["matrix"]
    bpy.context.view_layer.update()


def _movie_target(path):
    """Folder exports get a free filename; explicit filenames remain protected."""
    resolved, trailing_separator = delivery._absolute(path)
    if not trailing_separator and not resolved.is_dir():
        return path
    candidate = resolved / "afterglow.mp4"
    index = 0
    while candidate.exists() or candidate.is_symlink():
        index += 1
        candidate = resolved / f"afterglow-{index:03d}.mp4"
    return str(candidate)


def prepare_export(scene):
    """Prepare a whole-song MP4 in one step, using the chosen aspect and output path.

    Folder outputs automatically choose afterglow.mp4, afterglow-001.mp4, and so on;
    explicit existing filenames are rejected. A generated active stage camera is
    fitted to the selected aspect; custom active cameras retain their transform.
    Camera-directed effect animation is refreshed after a camera fit when needed.
    Format, path, range and camera changes are restored if preparation fails.
    """
    if not scene.is_editable:
        raise ValueError("Make the scene local before exporting a visual")
    _show_preflight(scene)
    s = scene.afterglow
    if not getattr(s, "show_advanced", False) and (s.effects.solo_layer != "ALL" or s.effects.master_brightness == 0):
        raise ValueError("Update Visual to restore full lighting, or use Advanced Controls before exporting this preview mix")
    if s.delivery_format not in delivery.RESOLUTIONS:
        raise ValueError("Choose Landscape, Portrait or Square output")
    # Reject invalid/missing paths and unavailable encoders before changing the scene.
    movie_target = _movie_target(s.output_path)
    delivery._output_path("MP4", movie_target)
    if not bpy.app.build_options.codec_ffmpeg:
        raise ValueError("This Blender build cannot export MP4; use PNG Sequence in Advanced")
    duration, fps, start = audition._track(scene)
    audition._bounds(scene, start, start + math.ceil((duration + bake.TAIL_S) * fps) - 1)
    state = _export_state(scene)
    try:
        delivery.apply_format(scene, s.delivery_format)
        generated = starter.camera_for(s.collection)
        if generated is not None and scene.camera == generated:
            starter.fit_camera(scene, s.collection)
        audition.set_song_range(scene, include_tail=True)
        target = delivery.configure_output(scene, "MP4", movie_target)
        if effects.outdated(scene):
            effects.build(scene)
        return target
    except Exception:
        _restore_export(scene, state)
        raise
