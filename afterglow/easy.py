"""Build music reactions on the user's own objects independently of environment effects.

Noctune styles and animates the chosen collection. It never generates a scene,
demo soundtrack or camera. Blender owns scene composition and rendering.
"""
import math

import numpy as np

from . import core, finish, presets, workflow


def _sound_preflight(scene):
    sound = scene.afterglow.sound
    if sound is None:
        raise ValueError("Load a track before creating this visual")
    mono, rate = core.load_mono(sound, "Create Visual")
    if not len(mono) or not math.isfinite(rate) or rate <= 0 or not np.isfinite(mono).all():
        raise ValueError("Choose a track with readable, finite audio samples")


def _glow_preflight(scene, enabled):
    if not scene.is_editable:
        raise ValueError("Make the scene local before creating a visual")
    if not enabled:
        return
    tree = scene.compositing_node_group
    if tree:
        _, node = finish._target(tree)
        if not finish.enabled(scene) and node and any(output.is_linked for output in node.outputs):
            raise ValueError("The Noctune glow was routed elsewhere; remove it in Advanced before creating the visual")


def create_visual(context):
    """Apply a music-reactive object look to the chosen collection.

    A collection and readable audio are required. Every prerequisite is checked
    before styling objects. Environment layers, mixer, routing and generated effects
    remain unchanged. Source geometry, cameras, world and native render/output
    settings stay under user control. Automatic glow respects disabled compositing.
    """
    scene = context.scene
    s = scene.afterglow
    if s.collection is None:
        raise ValueError("Choose a collection or use selected emissive objects before creating a visual")
    state = workflow.inspect(scene)
    if not state["ready"]:
        raise ValueError(state["reason"])
    _sound_preflight(scene)
    presets.preflight(scene, s.creative_look, scope="OBJECT")
    wants_glow = getattr(s, "easy_glow", True)
    apply_glow = wants_glow and scene.render.use_compositing
    _glow_preflight(scene, apply_glow)
    result = workflow.create_reaction(scene, look_identifier=s.creative_look, style_scope="OBJECT")
    if apply_glow:
        finish.enable(scene)
    elif not wants_glow:
        finish.disable(scene)
    scene.frame_set(s.start_frame)
    return {**result, "camera": scene.camera,
            "glow": finish.enabled(scene) and scene.render.use_compositing}
