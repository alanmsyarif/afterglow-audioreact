"""Preflight and a short path from an emissive collection to smooth audio playback."""

import bpy
import numpy as np

from . import bake, core, edition, light_reaction, materials, nodes, prepare, presets, tempo

if edition.IS_PRO:
    from . import effects


def inspect(scene):
    """Return cheap, read-only setup data for the sidebar; do not decode audio on redraw.

    ``targets``, ``eligible``, ``applied``, ``skipped`` and ``shared`` contain objects;
    ``linked`` contains materials. ``reason`` explains the first blocking prerequisite.
    A ready preflight still needs the audio decoder check performed by create_reaction.
    """
    s = scene.afterglow
    targets = core.targets(s.collection) if s.collection else []
    eligible, linked, patched = [], set(), set()
    readonly, light_errors = [], []
    for ob in targets:
        if not ob.is_editable:
            readonly.append(ob)
            continue
        if ob.type == "LIGHT":
            try:
                light_reaction.preflight([ob])
            except ValueError as error:
                light_errors.append(str(error))
            else:
                eligible.append(ob)
            continue
        emissive = {m for m in core._materials(ob) if materials.has_emission(m)}
        linked.update(m for m in emissive if not m.is_editable)
        local = {m for m in emissive if m.is_editable}
        if local:
            eligible.append(ob)
            patched.update(local)
    eligible_set = set(eligible)
    # Materials can be shared with objects in another scene or an unlinked collection.
    # A patch would darken those users too, so check the whole file before modifying it.
    shared = sorted((ob for ob in bpy.data.objects
                     if ob not in eligible_set and core._materials(ob) & patched),
                    key=lambda ob: ob.name)
    skipped = [ob for ob in targets if ob not in eligible_set]
    applied = [ob for ob in targets if core.reaction(ob)]
    warnings = []
    if skipped:
        warnings.append(f"{len(skipped)} objects have no editable emissive material")
    if linked:
        warnings.append(f"{len(linked)} linked materials will stay unchanged")
    if shared:
        warnings.append(f"{len(shared)} other objects share these emissive materials")
    reason = None
    if not s.collection:
        reason = "Choose a collection or use selected objects"
    elif not s.sound:
        reason = "Load a sound file to create the reaction"
    elif not core.sound_ok(s.sound):
        reason = "Sound file is missing; relink it or use a packed sound"
    elif any(ob.name not in scene.objects for ob in targets):
        reason = "Link the chosen collection to this scene first"
    elif readonly:
        reason = "Make linked objects local before creating a reaction"
    elif light_errors:
        reason = light_errors[0]
    elif not eligible:
        reason = "Select geometry or lights and press Use Selected to prepare the reaction"
    elif shared:
        reason = ("Make shared emissive materials single-user, or include all their objects "
                  "in the chosen collection")
    elif s.ripple and s.ripple_from == "EMPTY" and s.ripple_origin is None:
        reason = "Choose a Ripple Origin object or another ripple direction"
    return {"collection": s.collection, "sound": s.sound, "targets": targets,
            "eligible": eligible, "applied": applied, "skipped": skipped,
            "linked": sorted(linked, key=lambda mat: mat.name), "shared": shared,
            "ready": reason is None, "reason": reason, "warnings": warnings}


def _style_preflight(scene, look_identifier, look_data, scope="ALL"):
    """Check one optional style source without applying any of its controls."""
    scope = presets._scope(scope)
    if look_identifier is not None and look_data is not None:
        raise ValueError("Choose either a built-in look or a saved custom look")
    if look_identifier is not None:
        presets.preflight(scene, look_identifier, scope=scope)
    if look_data is not None:
        from . import lookbook
        return lookbook.preflight(scene, look_data, scope=scope)
    return None


def calibration_preflight(scene):
    """Protect authored calibration controls before a build changes scene data.

    The final calibration includes existing reactions outside the selected collection.
    A first light build also writes canonical object properties, so an authored curve
    on those inputs must be guarded before its native light data is copied.
    """
    names = (("Ripple Gain", "Ripple Threshold") if scene.afterglow.ripple
             else ("Gain", "Threshold"))
    for ob in sorted(core._managed(scene), key=lambda obj: obj.name):
        mod = core.reaction(ob)
        if mod is None and ob.type == "LIGHT" and core.is_target(ob):
            mod = light_reaction.LightReaction(ob)
        if mod is None:
            continue
        paths = {}
        for name in names:
            try:
                paths[nodes._socket(mod, name).path_from_id("value")] = name
            except (KeyError, AttributeError):
                # Older geometry groups gain the Ripple pair during Apply's
                # migration. Missing inputs cannot have authored curves yet.
                continue
        conflicts = presets._animation_paths(ob).intersection(paths)
        if conflicts:
            control = paths[sorted(conflicts)[0]]
            raise ValueError(f"Calibration would replace animated {control} on {ob.name}. "
                             "Remove its keyframes, NLA animation or driver before calibrating")


def create_reaction(scene, look_identifier=None, look_data=None, *, style_scope="ALL"):
    """Validate prerequisites, then apply, calibrate and bake the reaction.

    Like the existing Calibrate and Fast Playback controls, the final calibration/bake
    refreshes all Noctune object and light reactions in this scene. A chosen built-in look is applied
    after creating modifiers and before the single final bake. style_scope selects
    the controls the look may change; OBJECT keeps environment styling intact.
    This operation never builds effects.
    """
    style_scope = presets._scope(style_scope)
    state = inspect(scene)
    if not state["ready"]:
        raise ValueError(state["reason"])
    calibration_preflight(scene)
    normalized = _style_preflight(scene, look_identifier, look_data, style_scope)
    mono, rate = core.load_mono(scene.afterglow.sound, "Create Reaction")
    if not len(mono) or rate <= 0 or not np.isfinite(mono).all():
        raise ValueError("Choose a sound file with readable, finite audio samples")

    # A previous slider edit may still have a deferred bake. This operation replaces it
    # with one final bake after tempo and calibration, rather than running it twice.
    from . import props
    props._pending.pop(scene.name, None)
    applied, skipped, _, linked = core.apply(scene)
    if look_identifier is not None:
        presets.apply(scene, look_identifier, scope=style_scope)
    elif normalized is not None:
        from . import lookbook
        lookbook.apply(scene, normalized, scope=style_scope)
    curve = tempo.bake(scene)
    calibrated = core.calibrate(scene)
    if scene.afterglow.fast_playback:
        baked = bake.bake_all(scene)
    else:
        # The property callback synchronizes Use Baked and performs exactly one bake.
        scene.afterglow.fast_playback = True
        if bake.outdated(scene):
            raise ValueError("Fast Playback could not finish; use Re-bake to retry")
        baked = len([ob for ob in core._managed(scene) if core.reaction(ob)])
    bpms = [bpm for _, bpm in curve]
    return {"applied": len(applied), "skipped": len(skipped), "linked": len(linked),
            "calibrated": len(calibrated), "baked": baked,
            "bpm": (min(bpms), max(bpms)) if bpms else None}


def _effects_preflight(scene, enabled):
    """Validate routing for the layers a complete-show build is about to enable."""
    edition.require_pro()
    if not enabled:
        raise ValueError("Enable an effect layer or choose a Creative Look first")
    fx = scene.afterglow.effects
    venue = effects.make_venue(scene)
    if "strikes" in enabled and fx.strike_placement == "POINTS" and not effects.strike_points(scene):
        raise ValueError("Add a strike point, or set Data Strikes Placement to Random")
    if "downward" in enabled and fx.cascade_direction in effects.CAMERA_WAYS and scene.camera is None:
        raise ValueError("Set an active camera, or set Cascade Trails Direction to Down or Up")
    if "signals" in enabled:
        from . import network
        network.validate(scene, venue)


def create_show(scene, look_identifier=None, look_data=None):
    """Create a calibrated, keyed reaction and atmosphere in one operation.

    Pass a built-in look identifier to style the objects and layers before baking;
    pass look_data to use a portable custom-look snapshot, or omit both to retain
    the current styling and enabled layers. Audio, unsafe material
    sharing, animated look controls and required routing are checked before objects
    are changed. Like Create Reaction, this refreshes scene Noctune playback.
    Live mixer controls, audio placement, venue and routing remain unchanged.
    """
    edition.require_pro()
    state = inspect(scene)
    if not state["ready"]:
        raise ValueError(state["reason"])
    calibration_preflight(scene)
    normalized = _style_preflight(scene, look_identifier, look_data)
    if look_identifier is not None:
        enabled = presets.enabled_layers(look_identifier)
    elif normalized is not None and "layers" in normalized:
        enabled = tuple(name for name in effects.LAYERS if normalized["layers"][name]["enabled"])
    else:
        enabled = tuple(name for name in effects.LAYERS
                        if getattr(scene.afterglow.effects, name).enabled)
    _effects_preflight(scene, enabled)
    result = create_reaction(scene, look_identifier, normalized)
    result["effects"] = effects.build(scene)
    return result


def use_selected(context):
    """Prepare selected objects and link them, keeping their original collections."""
    if not context.scene.is_editable:
        raise ValueError("Make the scene local before using selected objects")
    if any(ob.type in core.SUPPORTED and (ob.get(core.effect_schema.TAG)
           or ob.get(core.effect_schema.EMITTER_TAG)) for ob in context.selected_objects):
        raise ValueError("Use your own objects and lights; generated environment effects have separate controls")
    objects = sorted((ob for ob in context.selected_objects
                      if core.is_target(ob) and ob.is_editable), key=lambda ob: ob.name)
    if not objects:
        raise ValueError("Select editable meshes, curves, text or lights first")
    light_reaction.preflight(objects)
    prepare.selected([ob for ob in objects if ob.type in core.GEOMETRY_TYPES])
    current = context.scene.afterglow.collection
    if (current and current.is_editable and current.name in context.scene.collection.children
            and set(current.all_objects) == set(objects)):
        return current
    collection = bpy.data.collections.new("Noctune Selection")
    context.scene.collection.children.link(collection)
    for ob in objects:
        collection.objects.link(ob)
    context.scene.afterglow.collection = collection
    return collection
