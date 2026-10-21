"""Independent music-reactive environment generation over existing scene bounds.

This workflow adds owned effect lights/graphics and shared audio playback. It never
prepares source materials, applies object reactions, calibrates or bakes them.
"""
import math

import numpy as np
import bpy

from . import core, effects, lookbook, presets, workflow


def _source_preflight(scene):
    if not scene.is_editable:
        raise ValueError("Make the scene local before generating environment effects")
    s = scene.afterglow
    if s.sound is None:
        raise ValueError("Load music before generating environment effects")
    mono, rate = core.load_mono(s.sound, "Generate Environment")
    if not len(mono) or not math.isfinite(rate) or rate <= 0 or not np.isfinite(mono).all():
        raise ValueError("Choose music with readable, finite audio samples")
    cage = s.effects.emitter
    if cage is not None:
        if cage.name not in scene.objects:
            raise ValueError("Link the emitter cage to this scene before generating effects")
    else:
        collection = effects.bounds_collection(scene)
        if collection is None:
            raise ValueError("Choose environment bounds or a main Objects collection first")
        if any(obj.name not in scene.objects for obj in core.targets(collection)
               if not obj.get(effects.TAG) and not obj.get(effects.EMITTER_TAG)):
            raise ValueError("Link the bounds collection to this scene before generating effects")


def build(scene, look_identifier=None, look_data=None):
    """Generate only the environment and return event counts per enabled layer.

    A built-in or saved look is optional; omitting both retains the current layer
    settings. All input/style/routing checks precede edits. Object reaction state,
    material assignments, camera, world and native render settings stay untouched.
    Playback uses the same scene music and Start Frame as the main workflow.
    """
    if look_identifier is not None and look_data is not None:
        raise ValueError("Choose either a built-in environment look or a saved look")
    _source_preflight(scene)
    s = scene.afterglow
    normalized = None
    if look_identifier is not None:
        presets.preflight(scene, look_identifier, scope="EFFECTS")
        enabled = presets.enabled_layers(look_identifier)
    elif look_data is not None:
        normalized = lookbook.preflight(scene, look_data, scope="EFFECTS")
        enabled = tuple(name for name in effects.LAYERS if normalized["layers"][name]["enabled"])
    else:
        enabled = tuple(name for name in effects.LAYERS
                        if getattr(scene.afterglow.effects, name).enabled)
    workflow._effects_preflight(scene, enabled)
    if (look_identifier is not None or normalized is not None) and not s.environment_advanced:
        s.effects.solo_layer = "ALL"
        s.effects.master_brightness = 1.0
    if look_identifier is not None:
        presets.apply(scene, look_identifier, scope="EFFECTS")
    elif normalized is not None:
        lookbook.apply(scene, normalized, scope="EFFECTS")
    counts = effects.build(scene)
    core.setup_playback(scene)
    if look_identifier is not None:
        # Freeze the environment choice after its first successful generation;
        # legacy fallback must not keep following later main-object look edits.
        scene.afterglow.environment_look = look_identifier
    return counts


def use_selected(context):
    """Group existing bounds without emission preparation or main collection changes."""
    scene = context.scene
    if not scene.is_editable:
        raise ValueError("Make the scene local before choosing environment bounds")
    objects = sorted((obj for obj in context.selected_objects if obj.type in core.SUPPORTED
                      and not obj.get(effects.TAG) and not obj.get(effects.EMITTER_TAG)),
                     key=lambda obj: obj.name)
    if not objects:
        raise ValueError("Select meshes, curves or text to bound the environment")
    current = scene.afterglow.effects.bounds_collection
    if (current and current.is_editable and current.name in scene.collection.children
            and set(current.all_objects) == set(objects)):
        return current
    collection = bpy.data.collections.new("Afterglow Bounds")
    scene.collection.children.link(collection)
    for obj in objects:
        collection.objects.link(obj)
    scene.afterglow.effects.bounds_collection = collection
    return collection
