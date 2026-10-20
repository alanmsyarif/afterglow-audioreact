import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper

from . import bake, core, creator, effects, nodes, presets, starter, tempo, workflow


def draw_note(layout, text, context=None, icon="NONE"):
    """Wrap guidance to the sidebar width instead of clipping it off-screen."""
    import textwrap
    width = getattr(getattr(context, "region", None), "width", 300)
    scale = getattr(getattr(getattr(context, "preferences", None), "system", None), "ui_scale", 1.0)
    chars = max(12, int(width / (7 * max(1.0, scale))) - 6)
    for i, line in enumerate(textwrap.wrap(text, chars)):
        layout.label(text=line, icon=icon if i == 0 else "NONE")


class AFTERGLOW_OT_create_reaction(bpy.types.Operator):
    bl_idname = "afterglow.create_reaction"
    bl_label = "Create Reaction"
    bl_description = "Apply audio reaction, auto-calibrate brightness and prepare smooth playback in one step"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            result = workflow.create_reaction(context.scene)
        except (ValueError, RuntimeError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, f"{result['applied']} objects ready: calibrated with smooth playback")
        return {"FINISHED"}


class AFTERGLOW_OT_use_selected(bpy.types.Operator):
    bl_idname = "afterglow.use_selected"
    bl_label = "Use Selected"
    bl_description = "Link selected emissive objects into a new reaction collection; keep their existing collections"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            collection = workflow.use_selected(context)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Using {collection.name}. Pick a sound, then Create Reaction")
        return {"FINISHED"}


class AFTERGLOW_OT_create_starter(bpy.types.Operator):
    bl_idname = "afterglow.create_starter"
    bl_label = "Add Starter Stage"
    bl_description = "Add a ready-to-light stage and camera, plus packed sample audio if no sound is selected"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        starter.create(context, layout=context.scene.afterglow.stage_layout)
        self.report({"INFO"}, "Starter stage added. Press Create Reaction to animate it")
        return {"FINISHED"}


class AFTERGLOW_OT_preview_stage(bpy.types.Operator):
    bl_idname = "afterglow.preview_stage"
    bl_label = "Preview Stage"
    bl_description = "Switch to the starter camera and Material Preview with scene lighting, then press Space to play"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return starter.camera_for(context.scene.afterglow.collection) is not None

    def execute(self, context):
        context.scene.camera = starter.camera_for(context.scene.afterglow.collection)
        if context.area and context.area.type == "VIEW_3D":
            space = context.space_data
            space.region_3d.view_perspective = "CAMERA"
            space.shading.type = "MATERIAL"
            space.shading.use_scene_lights = True
            space.shading.use_scene_world = True
        self.report({"INFO"}, "Starter camera selected. Press Space to play the timeline")
        return {"FINISHED"}


class AFTERGLOW_OT_apply_look(bpy.types.Operator):
    bl_idname = "afterglow.apply_look"
    bl_label = "Apply Look"
    bl_description = "Apply curated colors, response and effect layers. Build Effects to generate the atmosphere"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        try:
            n = presets.apply(scene, scene.afterglow.creative_look)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        if scene.afterglow.fast_playback and scene.afterglow.sound:
            try:
                bake.bake_all(scene)
            except ValueError as e:
                self.report({"WARNING"}, f"Look applied; refresh playback: {e}")
        self.report({"INFO"}, f"Look applied with {n} layers. Press Build Effects to update the atmosphere")
        return {"FINISHED"}


class AFTERGLOW_OT_load_sound(bpy.types.Operator, ImportHelper):
    bl_idname = "afterglow.load_sound"
    bl_label = "Load Sound"
    bl_description = "Load an audio file to drive the collection"

    filter_glob: StringProperty(default="*.wav;*.mp3;*.flac;*.ogg;*.m4a;*.aac", options={"HIDDEN"})

    def execute(self, context):
        try:
            context.scene.afterglow.sound = bpy.data.sounds.load(self.filepath, check_existing=True)
        except RuntimeError as e:
            self.report({"ERROR"}, f"Could not load sound: {e}")
            return {"CANCELLED"}
        return {"FINISHED"}


class AFTERGLOW_OT_apply(bpy.types.Operator):
    bl_idname = "afterglow.apply"
    bl_label = "Apply"
    bl_description = "Make every emissive object in the collection react to the sound"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            applied, skipped, outside, linked = core.apply(context.scene)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        msg = f"{len(applied)} objects react"
        if skipped:
            msg += f", {len(skipped)} skipped: no local emissive material"
        try:
            bpms = [b for _, b in tempo.bake(context.scene)]
            msg += f", tempo {min(bpms):.0f}-{max(bpms):.0f} BPM"
        except ValueError as e:
            self.report({"WARNING"}, f"Tempo analysis skipped: {e}")
        if context.scene.afterglow.fast_playback:
            try:
                bake.bake_all(context.scene)
                msg += ", baked"
            except ValueError as e:
                self.report({"WARNING"}, f"Bake skipped: {e}")
        self.report({"INFO"}, msg)
        if outside:
            self.report({"WARNING"}, "Shared material also used outside the collection, these render dark: "
                        + ", ".join(o.name for o in outside))
        if linked:
            self.report({"WARNING"}, "Linked materials can't react, make them local: "
                        + ", ".join(m.name for m in linked))
        return {"FINISHED"}


class AFTERGLOW_OT_respread(bpy.types.Operator):
    bl_idname = "afterglow.respread"
    bl_label = "Re-spread"
    bl_description = "Spread frequency bands bass to treble across unlocked objects"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.scene.afterglow.collection is not None

    def execute(self, context):
        coll = context.scene.afterglow.collection
        core.respread([o for o in core.targets(coll) if core.find_modifier(o)])
        core.replace(context.scene)  # picks up a moved Origin or moved objects
        if context.scene.afterglow.fast_playback:
            try:
                bake.bake_all(context.scene)
            except ValueError as e:
                self.report({"WARNING"}, f"Bake skipped: {e}")
        return {"FINISHED"}


class AFTERGLOW_OT_calibrate(bpy.types.Operator):
    bl_idname = "afterglow.calibrate"
    bl_label = "Calibrate"
    bl_description = ("Measure the sound and set each object's Threshold and Gain so quiet parts "
                      "go dark and peaks reach full brightness")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            done = core.calibrate(context.scene)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Calibrated {len(done)} objects")
        return {"FINISHED"}


class AFTERGLOW_OT_bake(bpy.types.Operator):
    bl_idname = "afterglow.bake"
    bl_label = "Re-bake"
    bl_description = "Recompute the Fast Playback bake for every Afterglow object"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        import time
        t0 = time.perf_counter()
        try:
            n = bake.bake_all(context.scene)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Baked {n} objects in {time.perf_counter() - t0:.1f} s")
        return {"FINISHED"}


class AFTERGLOW_OT_remove(bpy.types.Operator):
    bl_idname = "afterglow.remove"
    bl_label = "Remove"
    bl_description = "Remove Afterglow modifiers and restore the original materials"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        core.remove(context.scene)
        return {"FINISHED"}


class AFTERGLOW_OT_build_effects(bpy.types.Operator):
    bl_idname = "afterglow.build_effects"
    bl_label = "Build Effects"
    bl_description = ("Analyse the sound once and key the enabled environment-effect layers "
                      "over the venue as pooled real lights, plus glowing shafts when Visible Beams is on. "
                      "Weight, Intensity and Timing are keyframeable: Build samples them per event")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        import time
        t0 = time.perf_counter()
        try:
            counts = effects.build(context.scene)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        total = sum(counts.values())
        self.report({"INFO"}, f"{total} events on {len(counts)} layers "
                              f"in {time.perf_counter() - t0:.1f} s")
        return {"FINISHED"}


class AFTERGLOW_OT_remove_effects(bpy.types.Operator):
    bl_idname = "afterglow.remove_effects"
    bl_label = "Remove Effects"
    bl_description = "Delete the Afterglow Effects collection and every datablock it owns"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        n = effects.remove(context.scene)
        self.report({"INFO"}, f"Removed {n} effect objects")
        return {"FINISHED"}


class AFTERGLOW_OT_fit_emitter(bpy.types.Operator):
    bl_idname = "afterglow.fit_emitter"
    bl_label = "Fit Emitter Cage"
    bl_description = ("Add the emitter cage, or snap it back, to the venue fitted to the collection. "
                      "Then move, turn and scale it in the viewport: effects spawn inside it")
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        if context.scene.afterglow.effects.emitter:  # never throw away a shaped cage by accident
            return context.window_manager.invoke_confirm(
                self, event, title="Snap the cage back?",
                message="Your cage's place, turn and size are reset to the collection fit")
        return self.execute(context)

    def execute(self, context):
        try:
            cage = effects.fit_cage(context.scene)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, f"{cage.name} fitted to the collection")
        return {"FINISHED"}


def draw_venue(col, fx):
    """Venue section: where effects spawn (shape, seed, and the cage or the fitted size)."""
    row = col.row(align=True)
    row.prop(fx, "venue_shape", text="Shape")
    row.prop(fx, "seed")
    row = col.row(align=True)
    row.prop(fx, "emitter", text="Cage")
    row.operator("afterglow.fit_emitter", text="" if fx.emitter else "Add Cage",
                 icon="FILE_REFRESH" if fx.emitter else "MESH_CUBE")
    row = col.row(align=True)
    row.active = fx.emitter is None  # the cage sets the venue's size instead
    row.prop(fx, "height")
    row.prop(fx, "margin")


def draw_strike_points(col, fx):
    """Data Strikes placement: random spots, or strike points placed by hand."""
    col.separator()
    col.row(align=True).prop(fx, "strike_placement", expand=True)
    if fx.strike_placement == "POINTS":
        row = col.row(align=True)
        row.prop(fx, "strike_points", text="")
        row.operator("afterglow.add_strike_point", text="Add Point", icon="ADD")
        col.row(align=True).prop(fx, "strike_order", expand=True)


def draw_cascade(col, fx):
    """Cascade Trails: which line the trail travels, and its shape."""
    col.separator()
    col.prop(fx, "cascade_direction", text="")
    row = col.row(align=True)
    row.active = fx.cascade_direction not in effects.CAMERA_WAYS  # the camera picks the line
    row.prop(fx, "cascade_placement", expand=True)
    row = col.row(align=True)
    row.prop(fx, "cascade_count")
    row.prop(fx, "cascade_spacing")
    col.prop(fx, "cascade_size")


def draw_signals(col, fx):
    """Connected routing first; optional visible graphics stay behind an explicit toggle."""
    col.separator()
    row = col.row(align=True)
    row.prop(fx, "signal_anchors", text="Anchors")
    row.operator("afterglow.add_signal_anchor", text="", icon="ADD")
    col.label(text="Name order; includes anchor height" if fx.signal_anchors else "No anchors: uses the venue outline")
    col.prop(fx, "signal_route", expand=True)
    col.prop(fx, "signal_direction", text="Direction")
    col.prop(fx, "signal_speed")
    col.prop(fx, "signal_surface")
    col.separator()
    col.prop(fx, "signal_graphics")
    if fx.signal_graphics:
        row = col.row(align=True)
        row.prop(fx, "signal_width", text="Width")
        row.prop(fx, "signal_tail", text="Tail")
        row = col.row(align=True)
        row.prop(fx, "signal_frame_size", text="Frame")
        row.prop(fx, "signal_glow", text="Glow")


def draw_beams(col, fx):
    """Separate beam silhouettes and seeded appearance variation from timing controls."""
    col.active = fx.beams
    col.prop(fx, "beam_glow")
    col.label(text="Strike Look")
    col.row(align=True).prop(fx, "strike_style", expand=True)
    row = col.row(align=True)
    row.prop(fx, "strike_width")
    row.prop(fx, "strike_variation")
    row = col.row(align=True)
    row.prop(fx, "strike_companions")
    sub = row.row(align=True)
    sub.active = fx.strike_companions > 0
    sub.prop(fx, "strike_spread")
    row = col.row(align=True)
    row.prop(fx, "beam_walk", expand=True)
    sub = col.row(align=True)
    sub.active = fx.beam_walk != "OFF"
    sub.prop(fx, "beam_walk_time")
    col.separator()
    row = col.row(align=True)
    row.prop(fx, "beam_flow")
    row.prop(fx, "beam_direction", expand=True)
    row = col.row(align=True)
    row.prop(fx, "beam_gaps")
    row.prop(fx, "beam_dash")
    col.separator()
    col.label(text="Gun Look")
    col.row(align=True).prop(fx, "gun_style", expand=True)
    row = col.row(align=True)
    row.prop(fx, "gun_length")
    row.prop(fx, "gun_width")
    row = col.row(align=True)
    row.prop(fx, "gun_taper")
    row.prop(fx, "gun_fade")
    col.prop(fx, "gun_variation")
    col.label(text="Seed in Venue; Build to apply")


class AFTERGLOW_OT_add_strike_point(bpy.types.Operator):
    bl_idname = "afterglow.add_strike_point"
    bl_label = "Add Strike Point"
    bl_description = ("Add a strike point at the 3D cursor: Data Strikes land on your points instead of at "
                      "random. Move, duplicate or delete points like any object, then Build Effects")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            ob = effects.add_strike_point(context.scene, context.scene.cursor.location)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        for other in context.selected_objects:
            other.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob  # ready to grab (G)
        self.report({"INFO"}, f"{ob.name} added at the 3D cursor")
        return {"FINISHED"}


class AFTERGLOW_OT_add_signal_anchor(bpy.types.Operator):
    bl_idname = "afterglow.add_signal_anchor"
    bl_label = "Add Signal Anchor"
    bl_description = ("Add an anchor at the 3D cursor, including its height. Place at least two; "
                      "rename for route order, move or duplicate them, then Build Effects")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from . import network
        try:
            ob = network.add_anchor(context.scene, context.scene.cursor.location)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        context.view_layer.update()
        for other in context.selected_objects:
            other.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        self.report({"INFO"}, f"{ob.name} added; routes use name order")
        return {"FINISHED"}


def fx_next_step(scene):
    """The next action needed to build environment effects."""
    s = scene.afterglow
    if s.collection is None:
        return "Pick a collection in Sources first"
    if s.sound is None:
        return "Load a sound in Sources first"
    if not core.sound_ok(s.sound):
        return "Sound file missing: reload or pack the audio"
    fx = s.effects
    if not any(getattr(fx, name).enabled for name in effects.LAYERS):
        return "Enable a layer, then Build Effects"
    if not effects._collections(scene):
        return "Press Build Effects"


LAYER_NOTES = {
    "strikes": "Vertical accents that land with the bass.",
    "blooms": "Soft washes for depth and breathing room.",
    "flashes": "Short full-stage accents for strong hits.",
    "trails": "Moving lights around the venue outline.",
    "downward": "Cascading lights along one direction.",
    "gun": "Fast directed bursts from the venue rim.",
    "signals": "Travelling pulses with connected arrivals.",
}


class AFTERGLOW_PT_effects(bpy.types.Panel):
    bl_order = 1
    bl_label = "Environment Effects"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Afterglow"

    def draw(self, context):
        scene = context.scene
        fx = scene.afterglow.effects
        layout = self.layout
        playing = getattr(context.screen, "is_animation_playing", False)
        counts = effects.built_counts(scene)
        enabled = sum(getattr(fx, name).enabled for name in effects.LAYERS)
        status = layout.box()
        status.label(text=f"{enabled} layers enabled", icon="OUTLINER_OB_LIGHT")
        hint = fx_next_step(scene)
        stale = effects.outdated(scene) if not playing and not hint else False
        if hint:
            draw_note(status, hint, context, "FORWARD")
        elif stale:
            note = status.column()
            note.alert = True
            draw_note(note, "Settings changed. Rebuild to update the show.", context, "FILE_REFRESH")
        else:
            status.label(text=f"{sum(counts.values())} events built", icon="CHECKMARK")
        row = status.row(align=True)
        sub = row.row(align=True)
        s = scene.afterglow
        sub.enabled = not playing and bool(enabled and s.collection and s.sound and core.sound_ok(s.sound))
        sub.scale_y = 1.35
        sub.operator("afterglow.build_effects", text="Rebuild Effects" if counts else "Build Effects", icon="LIGHT")
        sub = row.row(align=True)
        sub.enabled = not playing and bool(effects._collections(scene))
        sub.operator("afterglow.remove_effects", text="", icon="TRASH")

        mixer = layout.box()
        mixer.label(text="Live Mix", icon="SOUND")
        mixer.prop(fx, "master_brightness", slider=True)
        mixer.prop(fx, "solo_layer", text="Preview")
        if fx.solo_layer != "ALL":
            draw_note(mixer, "Solo preview is active. Choose All Layers to restore the full show before rendering.", context, "INFO")
        draw_note(mixer, "Affects built effect lights and graphics live. Object emission stays independent.", context)
        layout.separator()
        for name in effects.LAYERS:
            layer = getattr(fx, name)
            head, body = layout.panel(f"AFTERGLOW_layer_{name}", default_closed=True)
            head.prop(layer, "enabled", text=fx.bl_rna.properties[name].name)
            if layer.enabled and name in counts:
                badge = head.row()
                badge.alert = counts[name] == 0
                badge.label(text=f"{counts[name]} hits")
            if body and layer.enabled:
                draw_note(body, LAYER_NOTES[name], context)
                body.prop(layer, "trigger_band", text="Listen to")
                body.prop(layer, "color", text="Color")
                r = body.row(align=True)
                r.prop(layer, "weight", text="Density")
                r.prop(layer, "intensity", text="Brightness")
                body.prop(layer, "timing", text="Duration")
                if name in counts and not counts[name]:
                    draw_note(body, "No hits in this band. Try another Listen to band, then rebuild.", context, "INFO")
                if name == "strikes":
                    draw_strike_points(body, fx)
                elif name == "downward":
                    draw_cascade(body, fx)
                elif name == "signals":
                    draw_signals(body, fx)

        layout.separator()
        head, body = layout.panel("AFTERGLOW_fx_venue", default_closed=True)
        head.label(text="Venue & Timing", icon="MESH_CIRCLE")
        if body:
            draw_venue(body.column(), fx)
            body.prop(fx, "timing_offset")
        head, body = layout.panel("AFTERGLOW_fx_beams", default_closed=True)
        head.prop(fx, "beams", text="Visible Beams")
        if body:
            draw_beams(body.column(), fx)
        creator.draw_finish(layout, context)


CALIBRATE = "Press Calibrate so quiet parts go dark"


def next_step(scene):
    """The setup step still missing, as a hint for the panel, or None once set up."""
    s = scene.afterglow
    if s.collection is None:
        return "Pick the collection of glowing objects"
    if s.sound is None:
        return "Pick a sound (folder button loads a file)"
    mods = [m for m in map(core.find_modifier, core.targets(s.collection)) if m]
    if not mods:
        return "Press Apply"
    # Untouched defaults everywhere mean Calibrate never ran (or the user never tuned by hand).
    gain, threshold = (nodes.RIPPLE_PAIR[k] if s.ripple else k for k in ("Gain", "Threshold"))
    if all(nodes.get_input(m, gain) == 1.0 and nodes.get_input(m, threshold) == 0.0 for m in mods):
        return CALIBRATE


class AFTERGLOW_PT_panel(bpy.types.Panel):
    bl_label = "Afterglow"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Afterglow"

    def draw(self, context):
        s = context.scene.afterglow
        layout = self.layout
        layout.label(text="Turn sound into light", icon="SOUND")
        playing = getattr(context.screen, "is_animation_playing", False)

        head, body = layout.panel("AFTERGLOW_sources", default_closed=False)
        head.label(text="1  Sources", icon="COLLECTION_NEW")
        if body:
            row = body.row(align=True)
            row.enabled = not playing
            row.operator("afterglow.use_selected", icon="RESTRICT_SELECT_OFF")
            body.prop(s, "stage_layout", text="Starter")
            row = body.row()
            row.enabled = not playing
            row.operator("afterglow.create_starter", icon="ADD")
            body.prop(s, "collection")
            row = body.row(align=True)
            row.prop(s, "sound", text="Track")
            row.operator("afterglow.load_sound", text="", icon="FILEBROWSER")
            body.prop(s, "start_frame")
            if starter.camera_for(s.collection):
                body.operator("afterglow.preview_stage", icon="VIEW_CAMERA")
            if s.collection is None:
                draw_note(body, "Use your emissive objects or try a starter stage with sample audio.", context, "INFO")

        status = None if playing else workflow.inspect(context.scene)
        box = layout.box()
        box.label(text="2  Audio Reaction", icon="MOD_WAVE")
        if status:
            if status["reason"]:
                draw_note(box, status["reason"], context, "INFO")
            else:
                label = (f"{len(status['applied'])} reacting objects" if status["applied"]
                         else f"{len(status['eligible'])} objects ready to react")
                box.label(text=label, icon="CHECKMARK")
            for warning in status["warnings"]:
                draw_note(box, warning, context, "ERROR")
        action = box.column()
        action.enabled = not playing and bool(status and status["ready"])
        action.scale_y = 1.5
        action.operator("afterglow.create_reaction", icon="PLAY",
                        text="Refresh Reaction" if status and status["applied"] else "Create Reaction")
        draw_note(box, "Auto-calibrates brightness and prepares smooth playback.", context)
        if status and status["applied"]:
            draw_note(box, "Press Space to play. Use Material Preview or Rendered shading to see the lights.", context, "INFO")

        head, body = layout.panel("AFTERGLOW_looks", default_closed=False)
        head.label(text="3  Creative Look", icon="COLOR")
        if body:
            body.prop(s, "creative_look", text="")
            draw_note(body, presets.DESCRIPTIONS[s.creative_look], context)
            row = body.row()
            row.scale_y = 1.25
            row.enabled = not playing
            row.operator("afterglow.apply_look", text="Apply Only", icon="BRUSH_DATA")
            row = body.row()
            row.enabled = not playing and bool(status and status["ready"])
            row.scale_y = 1.4
            row.operator("afterglow.build_show", icon="PLAY")
            draw_note(body, "Apply & Build creates a calibrated reaction and atmosphere using this look. Apply Only keeps building manual.", context, "INFO")
            creator.draw_library(body, context)

        head, body = layout.panel("AFTERGLOW_ripple", default_closed=True)
        head.prop(s, "ripple", text="Ripple Motion")
        if body:
            body.active = s.ripple
            body.prop(s, "beats_per_sweep")
            row = body.row(align=True)
            row.prop(s, "ripple_from", text="From")
            row.prop(s, "ripple_reverse", text="", icon="ARROW_LEFTRIGHT")
            if s.ripple_from == "EMPTY":
                body.prop(s, "ripple_origin")
            body.prop(s, "through")
            if s.through and not s.fast_playback:
                draw_note(body, "Through Objects needs Fast Playback.", context, "INFO")

        head, body = layout.panel("AFTERGLOW_tools", default_closed=True)
        head.label(text="Fine Tune & Maintenance", icon="PREFERENCES")
        if body:
            body.enabled = not playing
            row = body.row(align=True)
            row.operator("afterglow.apply", text="Apply Only")
            sub = row.row(align=True)
            sub.enabled = bool(status and status["applied"])
            sub.operator("afterglow.respread")
            row = body.row(align=True)
            row.enabled = not playing and any(core.find_modifier(ob) for ob in context.scene.objects)
            row.operator("afterglow.calibrate", icon="SPEAKER")
            row.operator("afterglow.remove", icon="TRASH")
            body.prop(s, "fast_playback")
        stale = bake.outdated(context.scene) if s.fast_playback and not playing else []
        if stale:
            warn = layout.box()
            warn.alert = True
            draw_note(warn, f"Playback needs a refresh: {len(stale)} objects changed.", context, "FILE_REFRESH")
            warn.operator("afterglow.bake", text="Refresh Playback")

        ob = context.object
        mod = core.find_modifier(ob) if ob else None
        head, body = layout.panel("AFTERGLOW_selected", default_closed=True)
        head.label(text="Selected Object", icon="OBJECT_DATA")
        if body:
            if mod:
                draw_object(body, s, ob, mod)
            else:
                draw_note(body, "Select a reacting object to adjust its band, response and color.", context)


def draw_object(box, s, ob, mod):
    """The selected object's reaction: what it hears (band), how it responds, how it looks."""
    box.label(text=ob.name, icon="OBJECT_DATA")
    box.label(text="Band (Hz)")
    row = box.row(align=True)
    nodes.draw_input(row, mod, "Low")
    nodes.draw_input(row, mod, "High")
    row.prop(ob.afterglow, "locked", text="", icon="LOCKED" if ob.afterglow.locked else "UNLOCKED")
    box.label(text="Response (Ripple)" if s.ripple else "Response")
    col = box.column(align=True)
    row = col.row(align=True)  # while rippling, the gain pair actually in use
    for name in ("Gain", "Threshold"):
        nodes.draw_input(row, mod, nodes.RIPPLE_PAIR[name] if s.ripple else name, text=name)
    row = col.row(align=True)
    nodes.draw_input(row, mod, "Decay")
    nodes.draw_input(row, mod, "Delay")
    box.label(text="Look")
    row = box.row(align=True)
    nodes.draw_input(row, mod, "Intensity")
    nodes.draw_input(row, mod, "Glow")
    nodes.draw_input(box, mod, "Use Colors")
    row = box.row(align=True)
    row.active = bool(nodes.get_input(mod, "Use Colors"))
    nodes.draw_input(row, mod, "Color A")
    nodes.draw_input(row, mod, "Color B")
    row = box.row(align=True)
    row.prop(ob.afterglow, "meter")
    sub = row.row(align=True)
    sub.active = ob.afterglow.meter and not s.through
    sub.prop(ob.afterglow, "meter_axis", expand=True)
    sub.prop(ob.afterglow, "meter_invert", text="", icon="ARROW_LEFTRIGHT")
    if ob.afterglow.meter and s.through:
        hint = box.row()
        hint.alert = True
        hint.label(text="Level Meter is off while Through Objects is on", icon="INFO")


CLASSES = (AFTERGLOW_OT_create_reaction, AFTERGLOW_OT_use_selected,
           AFTERGLOW_OT_create_starter, AFTERGLOW_OT_preview_stage, AFTERGLOW_OT_apply_look, AFTERGLOW_OT_load_sound, AFTERGLOW_OT_apply, AFTERGLOW_OT_respread,
           AFTERGLOW_OT_calibrate, AFTERGLOW_OT_bake, AFTERGLOW_OT_remove, AFTERGLOW_PT_panel,
           AFTERGLOW_OT_build_effects, AFTERGLOW_OT_remove_effects, AFTERGLOW_OT_fit_emitter,
           AFTERGLOW_OT_add_strike_point, AFTERGLOW_OT_add_signal_anchor,
           AFTERGLOW_PT_effects)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
