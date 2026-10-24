"""Build music reactions on the user's own objects independently of environment effects.

Noctune animates the chosen collection; appearance is a separate action.
It never generates a scene, demo soundtrack or camera. Blender owns scene composition and rendering.
"""
import math

import numpy as np

from . import core, finish, workflow


def _sound_preflight(scene):
    sound = scene.afterglow.sound
    if sound is None:
        raise ValueError("Load a track before creating this visual")
    mono, rate = core.load_mono(sound, "Create Visual")
    if not len(mono) or not math.isfinite(rate) or rate <= 0 or not np.isfinite(mono).all():
        raise ValueError("Choose a track with readable, finite audio samples")


def create_visual(context):
    """Build and calibrate current reactions without applying a look or changing glow.

    Material colors, textures, light styling and authored appearance controls stay
    intact. Environment effects, cameras, world and compositor are untouched.
    """
    scene = context.scene
    s = scene.afterglow
    if not scene.is_editable:
        raise ValueError("Make the scene local before building a reaction")
    if s.collection is None:
        raise ValueError("Choose a collection or use selected emissive objects before creating a visual")
    state = workflow.inspect(scene)
    if not state["ready"]:
        raise ValueError(state["reason"])
    _sound_preflight(scene)
    result = workflow.create_reaction(scene, style_scope="OBJECT")
    scene.frame_set(s.start_frame)
    return {**result, "camera": scene.camera,
            "glow": finish.enabled(scene) and scene.render.use_compositing}
