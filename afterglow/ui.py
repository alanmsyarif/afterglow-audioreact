import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper

from . import bake, core, nodes, tempo


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
        wm = context.window_manager
        samples = 96
        wm.progress_begin(0, samples)
        try:
            done = core.calibrate(context.scene, samples, progress=wm.progress_update)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        finally:
            wm.progress_end()
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
        row = col.row(align=True)
        row.operator("afterglow.apply")
        row.operator("afterglow.respread")
        row.operator("afterglow.remove")
        col.operator("afterglow.calibrate", icon="SPEAKER")

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
           AFTERGLOW_OT_calibrate, AFTERGLOW_OT_bake, AFTERGLOW_OT_remove, AFTERGLOW_PT_panel)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
