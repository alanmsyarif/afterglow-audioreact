"""One coordinated action for music-reactive scenes and lighting.

Existing scenes and generated stage objects remain editable. AUTO reuses a chosen
collection; only an empty setup creates a stage automatically. Blender owns the
render range, output settings and rendering workflow.
"""
import math

import numpy as np

from . import core, finish, presets, starter, workflow


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
    """Create the selected complete look, automatically handling stage, reactions and glow.

    AUTO retains any current collection. STAGE adds the chosen template unless the
    current collection already belongs to that template, and CUSTOM requires a chosen
    collection. Repeated creation reuses the same stage and the same effect build.
    A new stage selects its generated camera; existing camera objects are not edited.
    With a reused/custom scene, an active user camera remains selected. Simple
    creation restores the complete lighting mix; Advanced creation retains it.
    Native render/output settings and timeline ranges remain under user control.
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
    presets.preflight(scene, s.creative_look)
    wants_glow = getattr(s, "easy_glow", True)
    apply_glow = wants_glow and scene.render.use_compositing
    _glow_preflight(scene, apply_glow)
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
    if apply_glow:
        finish.enable(scene)
    elif not wants_glow:
        finish.disable(scene)
    scene.frame_set(s.start_frame)
    return {**result, "created_stage": create_stage, "camera": scene.camera,
            "glow": finish.enabled(scene) and scene.render.use_compositing}
