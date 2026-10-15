import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper

from . import bake, core, effects, nodes, tempo


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
                      "over the venue as pooled real lights (lights only — no geometry is added)")
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


def draw_beams(col, fx):
    """Visible Beams section: the strike filaments' look and motion."""
    col.active = fx.beams
    col.prop(fx, "beam_glow")
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


def fx_next_step(scene):
    """The Environment Effects step still missing, as a panel hint, or None once built."""
    fx = scene.afterglow.effects
    if not any(getattr(fx, name).enabled for name in effects.LAYERS):
        return "Enable a layer, then Build Effects"
    if not effects._collections(scene):
        return "Press Build Effects"


class AFTERGLOW_PT_effects(bpy.types.Panel):
    bl_label = "Afterglow Environment Effects"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Afterglow"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        fx = context.scene.afterglow.effects
        layout = self.layout
        head, body = layout.panel("AFTERGLOW_fx_venue", default_closed=False)
        head.label(text="Venue", icon="MESH_CIRCLE")
        if body:
            draw_venue(body.column(), fx)
        head, body = layout.panel("AFTERGLOW_fx_beams", default_closed=True)
        head.prop(fx, "beams", text="Visible Beams")
        if body:
            draw_beams(body.column(), fx)
        col = layout.column()
        counts = effects.built_counts(context.scene)
        for name in effects.LAYERS:
            layer = getattr(fx, name)
            box = col.box()
            head = box.row(align=True)
            head.prop(layer, "enabled", text="")
            head.label(text=fx.bl_rna.properties[name].name, icon="OUTLINER_OB_LIGHT")
            if layer.enabled and name in counts:  # 0 = its trigger band never fired
                n = head.row()
                n.alert = counts[name] == 0
                n.label(text=f"{counts[name]} events")
            if layer.enabled:
                body = box.column(align=True)
                body.prop(layer, "trigger_band")
                r = body.row(align=True)
                r.prop(layer, "weight")
                r.prop(layer, "intensity")
                r = body.row(align=True)
                r.prop(layer, "timing")
                r.prop(layer, "color", text="")
                if name == "strikes":
                    draw_strike_points(body, fx)
        hint = fx_next_step(context.scene)
        if hint:
            col.label(text=hint, icon="FORWARD")
        elif effects.outdated(context.scene):
            warn = col.row(align=True)
            warn.alert = True
            warn.label(text="Build outdated: settings changed", icon="ERROR")
        row = col.row(align=True)
        sub = row.row(align=True)
        sub.enabled = any(getattr(fx, name).enabled for name in effects.LAYERS)
        sub.operator("afterglow.build_effects")
        row.operator("afterglow.remove_effects")
        col.label(text="Effects are real lights; Visible Beams adds glowing shafts", icon="LIGHT")
        col.label(text="Weight/Intensity/Timing are keyframeable:", icon="INFO")
        col.label(text="Build samples their animation per event")


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
        col = self.layout.column()
        col.prop(s, "collection")
        row = col.row(align=True)
        row.prop(s, "sound")
        row.operator("afterglow.load_sound", text="", icon="FILEBROWSER")
        col.prop(s, "start_frame")
        row = col.row(align=True)
        row.prop(s, "ripple")
        sub = row.row(align=True)
        sub.active = s.ripple
        sub.prop(s, "beats_per_sweep", text="Beats")
        if s.ripple:
            row = col.row(align=True)
            row.prop(s, "ripple_from", text="From")
            row.prop(s, "ripple_reverse", text="", icon="ARROW_LEFTRIGHT")
            if s.ripple_from == "EMPTY":
                col.prop(s, "ripple_origin")
            col.prop(s, "through")
            if s.through and not s.fast_playback:
                hint = col.row()
                hint.alert = True
                hint.label(text="Through Objects needs Fast Playback", icon="INFO")
        row = col.row(align=True)
        row.prop(s, "fast_playback")
        # Skipped during playback: the sidebar redraws every frame and the scan is O(objects).
        playing = getattr(context.screen, "is_animation_playing", False)
        stale = bake.outdated(context.scene) if s.fast_playback and not playing else []
        if stale:
            warn = col.row(align=True)
            warn.alert = True
            warn.label(text=f"Bake outdated ({len(stale)} objects)", icon="ERROR")
            warn.operator("afterglow.bake", text="", icon="FILE_REFRESH")
        hint = next_step(context.scene) if not playing else None
        if hint:
            col.label(text=hint, icon="FORWARD")
        row = col.row(align=True)
        row.operator("afterglow.apply")
        sub = row.row(align=True)
        sub.enabled = hint in (None, CALIBRATE)  # nothing to re-spread or remove before Apply
        sub.operator("afterglow.respread")
        sub.operator("afterglow.remove")
        sub = col.row()
        sub.enabled = hint in (None, CALIBRATE)
        sub.operator("afterglow.calibrate", icon="SPEAKER")

        ob = context.object
        mod = core.find_modifier(ob) if ob else None
        if mod:
            box = self.layout.box()
            box.label(text=ob.name, icon="OBJECT_DATA")
            box.prop(ob.afterglow, "locked")
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
            for name in nodes.OBJECT_INPUTS:  # while rippling, show the gain pair actually in use
                nodes.draw_input(box, mod, nodes.RIPPLE_PAIR.get(name, name) if s.ripple else name)


CLASSES = (AFTERGLOW_OT_load_sound, AFTERGLOW_OT_apply, AFTERGLOW_OT_respread,
           AFTERGLOW_OT_calibrate, AFTERGLOW_OT_bake, AFTERGLOW_OT_remove, AFTERGLOW_PT_panel,
           AFTERGLOW_OT_build_effects, AFTERGLOW_OT_remove_effects, AFTERGLOW_OT_fit_emitter,
           AFTERGLOW_OT_add_strike_point,
           AFTERGLOW_PT_effects)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
