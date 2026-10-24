import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper

from . import bake, core, creator, easy, edition, nodes, presets, tempo, workflow

if edition.IS_PRO:
    from . import effects


def draw_note(layout, text, context=None, icon="NONE"):
    """Wrap guidance to the sidebar width instead of clipping it off-screen."""
    import textwrap
    width = getattr(getattr(context, "region", None), "width", 300)
    scale = getattr(getattr(getattr(context, "preferences", None), "system", None), "ui_scale", 1.0)
    chars = max(12, int(width / (7 * max(1.0, scale))) - 6)
    for i, line in enumerate(textwrap.wrap(text, chars)):
        layout.label(text=line, icon=icon if i == 0 else "NONE")


class AFTERGLOW_OT_create_visual(bpy.types.Operator):
    bl_idname = "afterglow.create_visual"
    bl_label = "Build Reaction"
    bl_description = "Build and calibrate geometry and light reactions while keeping their current appearance"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            result = easy.create_visual(context)
        except (ValueError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, "Reaction ready. Use Rendered view, then press Space")
        return {"FINISHED"}


class AFTERGLOW_OT_build_environment(bpy.types.Operator):
    bl_idname = "afterglow.build_environment"
    bl_label = "Build Environment"
    bl_description = "Generate this environment look from the music and bounds without changing object reactions"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return not getattr(context.screen, "is_animation_playing", False)

    def execute(self, context):
        from . import environment
        try:
            counts = environment.build(context.scene, look_identifier=context.scene.afterglow.environment_look)
        except (ValueError, RuntimeError, OSError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Environment ready: {sum(counts.values())} events. Press Space to preview")
        return {"FINISHED"}


class AFTERGLOW_OT_use_environment_selected(bpy.types.Operator):
    bl_idname = "afterglow.use_environment_selected"
    bl_label = "Use Selected Bounds"
    bl_description = "Fit environment effects around selected objects without changing their materials or object reaction collection"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (not getattr(context.screen, "is_animation_playing", False)
                and bool(context.selected_objects))

    def execute(self, context):
        from . import environment
        try:
            collection = environment.use_selected(context)
        except (ValueError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Environment bounds: {collection.name}")
        return {"FINISHED"}


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
    bl_description = "Use selected meshes, curves, text or lights; prepare materials where needed and preserve original settings"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (not getattr(context.screen, "is_animation_playing", False)
                and bool(context.selected_objects))

    def execute(self, context):
        try:
            collection = workflow.use_selected(context)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Using {collection.name}. Load music, choose a look, then Build Reaction")
        return {"FINISHED"}


class AFTERGLOW_OT_apply_look(bpy.types.Operator):
    bl_idname = "afterglow.apply_look"
    bl_label = "Apply Look"
    bl_description = "Apply the object colors and response without changing environment effects"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        collection = scene.afterglow.collection
        return (scene.is_editable and not getattr(context.screen, "is_animation_playing", False)
                and collection is not None
                and any(core.reaction(obj) for obj in collection.all_objects if core.is_target(obj)))

    def execute(self, context):
        scene = context.scene
        try:
            n = presets.apply(scene, scene.afterglow.creative_look, scope="OBJECT")
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        if scene.afterglow.fast_playback and scene.afterglow.sound:
            try:
                bake.bake_all(scene)
            except ValueError as e:
                self.report({"WARNING"}, f"Look applied; refresh playback: {e}")
        self.report({"INFO"}, f"Object look applied to {n} objects")
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
        core.respread([o for o in core.targets(coll) if core.reaction(o)])
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
    bl_description = "Recompute the Fast Playback bake for every Noctune object"
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
    bl_description = "Remove Noctune modifiers and restore the original materials"
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
    bl_description = "Delete the Noctune Effects collection and every datablock it owns"
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


def draw_lasers(col, fx):
    """Optional adjustments for the ready-made laser fan."""
    col.separator()
    col.prop(fx, "laser_count", text="Rays per Fan")
    col.prop(fx, "laser_spread", text="Spread")
    col.prop(fx, "laser_sweep", text="Sweep")
    col.prop(fx, "laser_glow", text="Glow")


def draw_strike_beam(col, fx):
    """Keep the Data Strike's light and optional visible shaft in one place."""
    col.separator()
    col.prop(fx.strikes, "visible_beam", text="Visible Beam")
    if not fx.strikes.visible_beam:
        return
    col.prop(fx.strikes, "beam_glow", text="Glow")
    col.row(align=True).prop(fx, "strike_style", expand=True)
    row = col.row(align=True)
    row.prop(fx, "strike_width")
    row.prop(fx, "strike_variation")
    row = col.row(align=True)
    row.prop(fx, "strike_companions")
    sub = row.row(align=True)
    sub.active = fx.strike_companions > 0
    sub.prop(fx, "strike_spread")
    col.row(align=True).prop(fx, "beam_walk", expand=True)
    row = col.row(align=True)
    row.active = fx.beam_walk != "OFF"
    row.prop(fx, "beam_walk_time")
    row = col.row(align=True)
    row.prop(fx, "beam_flow")
    row.prop(fx, "beam_direction", expand=True)
    row = col.row(align=True)
    row.prop(fx, "beam_gaps")
    row.prop(fx, "beam_dash")
    col.label(text="Seed in Venue; rebuild after shape changes")


def draw_gun_beam(col, fx):
    """Keep the Gun Burst's light and optional visible streak in one place."""
    col.separator()
    col.prop(fx.gun, "visible_beam", text="Visible Beam")
    if not fx.gun.visible_beam:
        return
    col.prop(fx.gun, "beam_glow", text="Glow")
    col.row(align=True).prop(fx, "gun_style", expand=True)
    row = col.row(align=True)
    row.prop(fx, "gun_length")
    row.prop(fx, "gun_width")
    row = col.row(align=True)
    row.prop(fx, "gun_taper")
    row.prop(fx, "gun_fade")
    col.prop(fx, "gun_variation")
    col.label(text="Seed in Venue; rebuild after shape changes")


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
    if s.effects.emitter is None and effects.bounds_collection(scene) is None:
        return "Choose environment bounds or use selected objects"
    if s.sound is None:
        return "Load music to generate the environment"
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
    "lasers": "Thin laser fans sweep on the beat. Visible rays need no fog.",
}


class AFTERGLOW_PT_effects(bpy.types.Panel):
    bl_label = "Noctune Pro · Environment"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Noctune FX"

    @classmethod
    def poll(cls, context):
        return True

    def draw(self, context):
        scene, layout = context.scene, self.layout
        s, fx = scene.afterglow, scene.afterglow.effects
        playing = getattr(context.screen, "is_animation_playing", False)
        source = layout.box()
        source.label(text="Bounds & Music", icon="LIGHT")
        row = source.row()
        row.enabled = not playing
        row.prop(fx, "bounds_collection", text="Bounds")
        row = source.row()
        row.enabled = not playing and bool(getattr(context, "selected_objects", ()))
        row.operator("afterglow.use_environment_selected", icon="RESTRICT_SELECT_OFF")
        row = source.row(align=True)
        row.enabled = not playing
        row.prop(s, "sound", text="Music")
        row.operator("afterglow.load_sound", text="", icon="FILEBROWSER")
        if fx.bounds_collection is None and s.collection is not None:
            draw_note(source, "Bounds use Objects from Noctune until you choose another collection.", context)
        elif fx.bounds_collection is None and fx.emitter is None:
            draw_note(source, "Choose a bounds collection or use selected objects. Emissive materials are optional.", context)
        draw_note(source, "Music and Start Frame are shared with Noctune.", context)
        layout.prop(s, "environment_look", text="Environment Look")
        action = layout.row()
        action.enabled = not playing and s.sound is not None and bool(fx.emitter or effects.bounds_collection(scene))
        action.scale_y = 1.6
        built = bool(effects._collections(scene))
        action.operator("afterglow.build_environment", text="Update Environment" if built else "Build Environment", icon="LIGHT")
        row = layout.row()
        row.enabled = not playing and built
        row.operator("afterglow.remove_effects", text="Remove Environment", icon="TRASH")
        draw_note(layout, "Generates lights and effect graphics. Object reactions stay unchanged.", context)
        layout.separator()
        layout.prop(s, "environment_advanced", text="Advanced Controls")
        if s.environment_advanced:
            draw_environment_controls(layout, context)
            creator.draw_library(layout, context, scope="EFFECTS")


def draw_environment_controls(layout, context):
    """Manual environment tools are independent of main object controls."""
    scene = context.scene
    fx = scene.afterglow.effects
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
    sub.enabled = not playing and bool(enabled and (fx.emitter or effects.bounds_collection(scene)) and s.sound and core.sound_ok(s.sound))
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
                draw_strike_beam(body, fx)
            elif name == "gun":
                draw_gun_beam(body, fx)
            elif name == "downward":
                draw_cascade(body, fx)
            elif name == "signals":
                draw_signals(body, fx)
            elif name == "lasers":
                draw_lasers(body, fx)

    layout.separator()
    head, body = layout.panel("AFTERGLOW_fx_venue", default_closed=True)
    head.label(text="Venue & Timing", icon="MESH_CIRCLE")
    if body:
        draw_venue(body.column(), fx)
        body.prop(scene.afterglow, "start_frame", text="Music Start")
        body.prop(fx, "timing_offset")


OBJECT_HINTS = {
    "NEON_CLUB": "Cyan & pink · Punchy",
    "CINEMATIC_PULSE": "Blue & gold · Slow",
    "AURORA_FLOW": "Mint & violet · Soft",
    "SIGNAL_GRID": "Cyan & amber · Crisp",
    "LASER_SHOW": "Violet & green · Quick",
}


CALIBRATE = "Press Calibrate so quiet parts go dark"


def next_step(scene):
    """The setup step still missing, as a hint for the panel, or None once set up."""
    s = scene.afterglow
    if s.collection is None:
        return "Pick the collection of glowing objects"
    if s.sound is None:
        return "Pick a sound (folder button loads a file)"
    mods = [m for m in map(core.reaction, core.targets(s.collection)) if m]
    if not mods:
        return "Press Apply"
    # Untouched defaults everywhere mean Calibrate never ran (or the user never tuned by hand).
    gain, threshold = (nodes.RIPPLE_PAIR[k] if s.ripple else k for k in ("Gain", "Threshold"))
    if all(nodes.get_input(m, gain) == 1.0 and nodes.get_input(m, threshold) == 0.0 for m in mods):
        return CALIBRATE


def draw_music(layout, context):
    """The shared track has one visible home, with timing available on demand."""
    s = context.scene.afterglow
    playing = getattr(context.screen, "is_animation_playing", False)
    music = layout.box()
    music.label(text="Music", icon="SOUND")
    row = music.row(align=True)
    row.enabled = not playing
    row.prop(s, "sound", text="Track")
    row.operator("afterglow.load_sound", text="", icon="FILEBROWSER")
    if s.show_advanced:
        row = music.row(align=True)
        row.enabled = not playing
        row.prop(s, "start_frame")
    if playing:
        draw_note(music, "Stop playback to change the track or rebuild.", context, "INFO")
    elif s.sound is None:
        draw_note(music, "Choose a track to begin.", context)
    elif not core.sound_ok(s.sound):
        draw_note(music, "Track file missing. Reload it or pack the sound into this project.", context, "ERROR")


def draw_object_workflow(layout, context):
    """Build first; optional appearance changes are explicit actions afterward."""
    s = context.scene.afterglow
    playing = getattr(context.screen, "is_animation_playing", False)
    source = layout.box()
    source.label(text="Objects", icon="OBJECT_DATA")
    field = source.row()
    field.enabled = not playing
    field.prop(s, "collection", text="Collection")
    selected = source.row()
    selected.enabled = not playing and bool(getattr(context, "selected_objects", ()))
    selected.operator("afterglow.use_selected", text="Use Selected", icon="RESTRICT_SELECT_OFF")
    if s.collection is None:
        draw_note(source, "Select geometry or lights, then Use Selected.", context)
    card = layout.box()
    card.label(text="Reaction", icon="SOUND")
    built = bool(s.collection and any(core.reaction(obj) for obj in s.collection.all_objects if core.is_target(obj)))
    music_ready = bool(s.sound and core.sound_ok(s.sound))
    action = card.column()
    action.enabled = not playing and s.collection is not None and music_ready
    action.scale_y = 1.6
    action.operator("afterglow.create_visual", text="Update Reaction" if built else "Build Reaction", icon="PLAY")
    draw_note(card, "Keeps your materials and current appearance.", context)
    if playing:
        draw_note(card, "Stop playback to change the setup.", context, "INFO")
    elif s.collection is None:
        draw_note(card, "Choose objects to build their reaction.", context, "FORWARD")
    elif not music_ready:
        draw_note(card, "Load a readable track in Music above.", context, "FORWARD")
    else:
        draw_note(card, "Reaction built. Press Space to preview." if built else "Ready to build your reaction.",
                  context, "CHECKMARK")
    if built:
        head, body = layout.panel("AFTERGLOW_object_appearance", default_closed=False)
        head.label(text="Appearance", icon="PRESET")
        if body:
            body.enabled = not playing
            body.prop(s, "creative_look", text="Object Look")
            draw_note(body, OBJECT_HINTS.get(s.creative_look, "Choose an optional look."), context)
            body.operator("afterglow.apply_look", text="Apply Look", icon="BRUSH_DATA")
            draw_note(body, "Optional. Apply a look once; later updates keep your edits.", context)
    else:
        draw_note(layout, "Build first to choose an optional look.", context)
    layout.prop(s, "show_advanced", text="Advanced Controls")
    if s.show_advanced:
        draw_advanced(layout, context)
    draw_cleanup(layout, context)


def draw_cleanup(layout, context):
    """Removal stays reachable without opening the tuning and playback tools."""
    head, body = layout.panel("AFTERGLOW_object_cleanup", default_closed=True)
    head.label(text="Cleanup", icon="TRASH")
    if body:
        body.enabled = not getattr(context.screen, "is_animation_playing", False)
        body.operator("afterglow.remove", text="Remove Object Reaction", icon="TRASH")
        draw_note(body, "Restores prepared materials and original light data. Keeps your objects.", context)


class AFTERGLOW_PT_panel(bpy.types.Panel):
    bl_label = edition.NAME
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Noctune"

    def draw(self, context):
        draw_music(self.layout, context)
        draw_object_workflow(self.layout, context)


def draw_advanced(layout, context):
    """Appearance first; optional motion, saved styles and maintenance follow."""
    s = context.scene.afterglow
    playing = getattr(context.screen, "is_animation_playing", False)
    ob = getattr(context, "object", None)
    mod = core.reaction(ob) if ob else None
    if mod:
        head, body = layout.panel("AFTERGLOW_selected", default_closed=False)
        head.label(text="Selected Object", icon="OBJECT_DATA")
        if body:
            draw_object(body, s, ob, mod, context)

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
    creator.draw_finish(layout, context)
    creator.draw_library(layout, context, scope="OBJECT")

    head, body = layout.panel("AFTERGLOW_tools", default_closed=True)
    head.label(text="Playback & Tools", icon="PREFERENCES")
    if body:
        body.enabled = not playing
        body.operator("afterglow.create_reaction", text="Build with Current Settings", icon="PLAY")
        draw_note(body, "Keeps your style; recalibrates the audio response.", context)
        body.operator("afterglow.bake", text="Refresh Playback", icon="FILE_REFRESH")
        body.prop(s, "fast_playback")
        body.separator()
        body.operator("afterglow.apply", text="Refresh Reaction Setup Only")
        row = body.row(align=True)
        row.operator("afterglow.respread", text="Spread Bands")
        row.operator("afterglow.calibrate", text="Calibrate")
    stale = ([obj for obj in bake.outdated(context.scene) if s.fast_playback or obj.type == "LIGHT"]
             if not playing else [])
    if stale:
        draw_note(layout, f"{len(stale)} objects need a playback refresh.", context, "INFO")


def draw_object(layout, s, ob, mod, context=None):
    """Appearance is immediately available; audio response is an optional panel."""
    draw_note(layout, ob.name, context, "LIGHT" if ob.type == "LIGHT" else "OBJECT_DATA")
    layout.label(text="Appearance")
    if ob.type == "LIGHT":
        nodes.draw_input(layout, mod, "Base Power")
    row = layout.row(align=True)
    nodes.draw_input(row, mod, "Intensity")
    nodes.draw_input(row, mod, "Glow", text="Idle Glow")
    nodes.draw_input(layout, mod, "Use Colors")
    row = layout.row(align=True)
    row.active = bool(nodes.get_input(mod, "Use Colors"))
    nodes.draw_input(row, mod, "Color A")
    nodes.draw_input(row, mod, "Color B")
    if ob.type != "LIGHT":
        row = layout.row(align=True)
        row.prop(ob.afterglow, "meter")
        sub = row.row(align=True)
        sub.active = ob.afterglow.meter and not s.through
        sub.prop(ob.afterglow, "meter_axis", expand=True)
        sub.prop(ob.afterglow, "meter_invert", text="", icon="ARROW_LEFTRIGHT")
        if ob.afterglow.meter and s.through:
            draw_note(layout, "Level Meter is off while Through Objects is on.", context, "INFO")

    head, body = layout.panel("AFTERGLOW_selected_response", default_closed=True)
    head.label(text="Audio Response", icon="SOUND")
    if not body:
        return
    body.label(text="Band (Hz)")
    row = body.row(align=True)
    nodes.draw_input(row, mod, "Low")
    nodes.draw_input(row, mod, "High")
    row.prop(ob.afterglow, "locked", text="", icon="LOCKED" if ob.afterglow.locked else "UNLOCKED")
    body.label(text="Response (Ripple)" if s.ripple else "Response")
    row = body.row(align=True)
    for name in ("Gain", "Threshold"):
        nodes.draw_input(row, mod, nodes.RIPPLE_PAIR[name] if s.ripple else name, text=name)
    row = body.row(align=True)
    nodes.draw_input(row, mod, "Decay")
    nodes.draw_input(row, mod, "Delay")
    draw_note(body, "After editing Band, Decay or Delay, use Playback & Tools → Refresh Playback.", context)


PRO_CLASSES = (AFTERGLOW_OT_build_environment, AFTERGLOW_OT_use_environment_selected,
               AFTERGLOW_OT_build_effects, AFTERGLOW_OT_remove_effects, AFTERGLOW_OT_fit_emitter,
               AFTERGLOW_OT_add_strike_point, AFTERGLOW_OT_add_signal_anchor,
               AFTERGLOW_PT_effects) if edition.IS_PRO else ()
CLASSES = (AFTERGLOW_OT_create_visual, AFTERGLOW_OT_create_reaction, AFTERGLOW_OT_use_selected,
           AFTERGLOW_OT_apply_look, AFTERGLOW_OT_load_sound, AFTERGLOW_OT_apply, AFTERGLOW_OT_respread,
           AFTERGLOW_OT_calibrate, AFTERGLOW_OT_bake, AFTERGLOW_OT_remove, AFTERGLOW_PT_panel,
           *PRO_CLASSES)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
