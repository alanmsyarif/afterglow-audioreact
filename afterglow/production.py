"""Native Blender controls for auditioning a track and preparing creator exports."""
import bpy

from . import audition, delivery, starter


def _playing(context):
    return getattr(context.screen, "is_animation_playing", False)


class AFTERGLOW_OT_preview_excerpt(bpy.types.Operator):
    bl_idname = "afterglow.preview_excerpt"
    bl_label = "Loop This Excerpt"
    bl_description = "Set a playback-only loop within the track and jump to its first frame"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.afterglow
        try:
            start, end = audition.set_preview(context.scene, s.preview_offset, s.preview_duration)
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        context.scene.frame_set(start)
        self.report({"INFO"}, f"Playback loop: frames {start}–{end}. Press Space to play")
        return {"FINISHED"}


class AFTERGLOW_OT_restore_preview(bpy.types.Operator):
    bl_idname = "afterglow.restore_preview"
    bl_label = "Restore Previous Loop"
    bl_description = "Restore the playback range from before Afterglow auditioning"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        audition.reset_preview(context.scene)
        return {"FINISHED"}


class AFTERGLOW_OT_song_range(bpy.types.Operator):
    bl_idname = "afterglow.song_range"
    bl_label = "Use Track Length"
    bl_description = "Set the animation render range to the chosen track, optionally keeping the effect fade-out"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            start, end = audition.set_song_range(context.scene, context.scene.afterglow.output_include_tail)
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Render range set to frames {start}–{end}")
        return {"FINISHED"}


class AFTERGLOW_OT_set_output_format(bpy.types.Operator):
    bl_idname = "afterglow.set_output_format"
    bl_label = "Apply Size"
    bl_description = "Apply this output size with square pixels; retain camera framing and frame rate"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            width, height = delivery.apply_format(context.scene, context.scene.afterglow.delivery_format)
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Output size: {width} × {height}. Fit the stage camera if the new aspect needs it")
        return {"FINISHED"}


class AFTERGLOW_OT_fit_stage_camera(bpy.types.Operator):
    bl_idname = "afterglow.fit_stage_camera"
    bl_label = "Fit Stage Camera"
    bl_description = "Reframe and select the generated stage camera for the current output size and effect height"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return starter.camera_for(context.scene.afterglow.collection) is not None

    def execute(self, context):
        try:
            camera = starter.fit_camera(context.scene, context.scene.afterglow.collection)
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        context.scene.camera = camera
        self.report({"INFO"}, "Stage camera fitted and selected")
        return {"FINISHED"}


class AFTERGLOW_OT_prepare_output(bpy.types.Operator):
    bl_idname = "afterglow.prepare_output"
    bl_label = "Prepare Output"
    bl_description = "Apply this path and file format to Blender's render output settings without rendering"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.afterglow
        try:
            path = delivery.configure_output(context.scene, s.output_format, s.output_path)
        except (ValueError, OSError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Output prepared: {path}")
        return {"FINISHED"}


class AFTERGLOW_PT_delivery(bpy.types.Panel):
    bl_label = "Preview & Export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Afterglow"
    bl_order = 2

    def draw(self, context):
        from .ui import draw_note
        scene, layout = context.scene, self.layout
        s = scene.afterglow
        playing = _playing(context)
        head, body = layout.panel("AFTERGLOW_audition", default_closed=True)
        head.label(text="Audition a Section", icon="PREVIEW_RANGE")
        if body:
            body.enabled = not playing
            body.prop(s, "preview_offset", text="From (s)")
            body.prop(s, "preview_duration", text="Length (s)")
            row = body.row()
            row.enabled = not playing and s.sound is not None
            row.operator("afterglow.preview_excerpt", icon="LOOP_BACK")
            body.operator("afterglow.restore_preview", icon="LOOP_FORWARDS")
            draw_note(body, "Loops playback only. Animation renders still use the render range below.", context)

        head, body = layout.panel("AFTERGLOW_output_size", default_closed=False)
        head.label(text="Frame the Output", icon="FULLSCREEN_ENTER")
        if body:
            body.enabled = not playing
            body.prop(s, "delivery_format", text="Size")
            body.operator("afterglow.set_output_format", icon="CHECKMARK")
            if starter.camera_for(s.collection):
                body.operator("afterglow.fit_stage_camera", icon="VIEW_CAMERA")
            draw_note(body, "Size presets keep the frame rate and camera transform. Fit Stage Camera is an explicit reframe.", context)

        head, body = layout.panel("AFTERGLOW_render_range", default_closed=True)
        head.label(text="Render Range", icon="TIME")
        if body:
            body.enabled = not playing
            row = body.row(align=True)
            row.prop(scene, "frame_start", text="Start")
            row.prop(scene, "frame_end", text="End")
            body.prop(s, "output_include_tail", text="Include Fade-out")
            row = body.row()
            row.enabled = not playing and s.sound is not None
            row.operator("afterglow.song_range", icon="SOUND")

        box = layout.box()
        box.label(text="Prepare & Render", icon="RENDER_ANIMATION")
        col = box.column()
        col.enabled = not playing
        col.prop(s, "output_format", text="Format")
        col.prop(s, "output_path", text="Path")
        if s.output_format == "PNG_SEQUENCE":
            col.prop(scene.render, "film_transparent", text="Transparent Background")
        col.operator("afterglow.prepare_output", icon="FILE_TICK")
        draw_note(box, "Save the .blend before using a // relative path, or choose an absolute output path.", context)

        status = delivery.status(scene)
        width, height = status["resolution"]
        start, end = status["frames"]
        layout.label(text=f"Active: {width} × {height} · {status['fps']:.2f} fps")
        layout.label(text=f"{end - start + 1} frames · {status['format'].replace('_', ' ').title()}")
        if status["path"]:
            draw_note(layout, "Output: " + status["path"], context)
        for note in status["errors"]:
            draw_note(layout, note, context, "ERROR")
        for note in status["warnings"]:
            draw_note(layout, note, context, "INFO")
        row = layout.row()
        row.enabled = not playing and status["ready"]
        row.scale_y = 1.4
        row.operator_context = "INVOKE_DEFAULT"
        row.operator("render.render", text="Render Animation", icon="RENDER_ANIMATION").animation = True
        draw_note(layout, "Renders the active Blender settings shown above. MP4 audio depends on your Blender build; PNG exports contain images only.", context)


CLASSES = (AFTERGLOW_OT_preview_excerpt, AFTERGLOW_OT_restore_preview, AFTERGLOW_OT_song_range,
           AFTERGLOW_OT_set_output_format, AFTERGLOW_OT_fit_stage_camera,
           AFTERGLOW_OT_prepare_output, AFTERGLOW_PT_delivery)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
