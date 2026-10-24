"""Creator actions: reusable project looks, complete shows, and creative glow."""
import json
from pathlib import Path

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import bake, edition, finish, lookbook, workflow

MAX_LOOKS = 64
SCOPE_ITEMS = (
    ("ALL", "Complete Look", "Apply object and environment styling"),
    ("OBJECT", "Object Reaction", "Apply object styling only"),
    ("EFFECTS", "Environment", "Apply generated environment styling only"),
)


def _scope_property():
    return EnumProperty(name="Apply To", items=SCOPE_ITEMS,
                        default="ALL" if edition.IS_PRO else "OBJECT", options={"HIDDEN"})


def selected_look(scene):
    s = scene.afterglow
    return s.saved_looks[s.saved_look_index] if 0 <= s.saved_look_index < len(s.saved_looks) else None


def store_look(scene, name, data):
    """Validate before adding a uniquely named, portable snapshot to this project."""
    data = lookbook.validate(data)
    s = scene.afterglow
    if len(s.saved_looks) >= MAX_LOOKS:
        raise ValueError("This project already has 64 saved looks; remove one before adding another")
    name = (name.strip() or "My Look")[:64]
    names = {item.name for item in s.saved_looks}
    unique, number = name, 2
    while unique in names:
        unique = f"{name[:55]} ({number})"
        number += 1
    payload = json.dumps(data, indent=2, allow_nan=False)
    entry = s.saved_looks.add()
    entry.name, entry.payload = unique, payload
    s.saved_look_index = len(s.saved_looks) - 1
    return entry


def _refresh_playback(operator, scene):
    if scene.afterglow.fast_playback and scene.afterglow.sound:
        try:
            bake.bake_all(scene)
        except ValueError as error:
            operator.report({"WARNING"}, f"Look applied; refresh playback: {error}")


class AFTERGLOW_OT_build_show(bpy.types.Operator):
    bl_idname = "afterglow.build_show"
    bl_label = "Apply & Build Show"
    bl_description = "Create the reaction, apply this built-in look, calibrate, prepare playback and build effects"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    def execute(self, context):
        try:
            edition.require_pro()
            result = workflow.create_show(context.scene, look_identifier=context.scene.afterglow.creative_look)
        except (ValueError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Show ready: {result['applied']} reacting objects. Press Space to play")
        return {"FINISHED"}


class AFTERGLOW_OT_save_look(bpy.types.Operator):
    bl_idname = "afterglow.save_look"
    bl_label = "Save Current Look"
    bl_description = ("Save current layers and the first reacting object's shared style inside this .blend"
                      if edition.IS_PRO else "Save the first reacting object's shared style inside this .blend")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            entry = store_look(context.scene, context.scene.afterglow.new_look_name, lookbook.capture(context.scene))
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Saved {entry.name} in this project")
        return {"FINISHED"}


class AFTERGLOW_OT_apply_saved_look(bpy.types.Operator):
    bl_idname = "afterglow.apply_saved_look"
    bl_label = "Apply Saved Look"
    bl_description = ("Apply the saved style to the chosen object or environment workflow"
                      if edition.IS_PRO else "Apply the saved object colors and response")
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    scope: _scope_property()

    @classmethod
    def poll(cls, context):
        return selected_look(context.scene) is not None

    def execute(self, context):
        entry = selected_look(context.scene)
        try:
            scope = edition.style_scope(self.scope)
            lookbook.apply(context.scene, lookbook.loads(entry.payload), scope=scope)
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        if scope in {"ALL", "OBJECT"}:
            _refresh_playback(self, context.scene)
        message = (f"Applied {entry.name} to object reactions" if scope == "OBJECT" else
                   f"Applied {entry.name}. Rebuild Effects to generate this environment style")
        self.report({"INFO"}, message)
        return {"FINISHED"}


class AFTERGLOW_OT_build_saved_look(bpy.types.Operator):
    bl_idname = "afterglow.build_saved_look"
    bl_label = "Apply & Build Saved Look"
    bl_description = ("Apply and build the saved style in the chosen object or environment workflow"
                      if edition.IS_PRO else "Apply the saved style and build its music reaction")
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    scope: _scope_property()

    @classmethod
    def poll(cls, context):
        return selected_look(context.scene) is not None

    def execute(self, context):
        entry = selected_look(context.scene)
        try:
            scope = edition.style_scope(self.scope)
            data = lookbook.loads(entry.payload)
            if scope == "OBJECT":
                workflow.create_reaction(context.scene, look_data=data, style_scope="OBJECT")
            elif scope == "EFFECTS":
                from . import environment
                environment.build(context.scene, look_data=data)
            else:
                workflow.create_show(context.scene, look_data=data)
        except (ValueError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        target = {"OBJECT": "Object reactions", "EFFECTS": "Environment", "ALL": "Show"}[scope]
        self.report({"INFO"}, f"{target} ready with {entry.name}. Press Space to play")
        return {"FINISHED"}


class AFTERGLOW_OT_remove_saved_look(bpy.types.Operator):
    bl_idname = "afterglow.remove_saved_look"
    bl_label = "Remove Saved Look"
    bl_description = "Remove this snapshot from the project library; the scene's current style stays as it is"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return selected_look(context.scene) is not None

    def execute(self, context):
        s = context.scene.afterglow
        s.saved_looks.remove(s.saved_look_index)
        s.saved_look_index = max(0, min(s.saved_look_index, len(s.saved_looks) - 1))
        return {"FINISHED"}


class AFTERGLOW_OT_import_look(bpy.types.Operator, ImportHelper):
    bl_idname = "afterglow.import_look"
    bl_label = "Import Look"
    bl_description = "Import a portable Noctune look JSON file into this project's library"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        try:
            with open(self.filepath, "rb") as source:
                raw = source.read(lookbook.MAX_BYTES + 1)
            if len(raw) > lookbook.MAX_BYTES:
                raise ValueError("Look file is too large (maximum 64 KiB)")
            data = lookbook.loads(raw.decode("utf-8"))
            entry = store_look(context.scene, Path(self.filepath).stem, data)
        except (OSError, ValueError) as error:
            self.report({"ERROR"}, f"Could not import look: {error}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Imported {entry.name}; the current scene was not changed")
        return {"FINISHED"}


class AFTERGLOW_OT_export_look(bpy.types.Operator, ExportHelper):
    bl_idname = "afterglow.export_look"
    bl_label = "Export Look"
    bl_description = "Export the selected saved look as JSON for another project"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return selected_look(context.scene) is not None

    def invoke(self, context, event):
        entry = selected_look(context.scene)
        if not self.filepath:
            self.filepath = bpy.path.clean_name(entry.name) + ".json"
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        try:
            data = lookbook.loads(selected_look(context.scene).payload)
            Path(self.filepath).write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        except (OSError, ValueError) as error:
            self.report({"ERROR"}, f"Could not export look: {error}")
            return {"CANCELLED"}
        self.report({"INFO"}, "Portable look exported")
        return {"FINISHED"}


class AFTERGLOW_UL_saved_looks(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        layout.prop(item, "name", text="", emboss=False, icon="PRESET")


class AFTERGLOW_OT_add_render_glow(bpy.types.Operator):
    bl_idname = "afterglow.add_render_glow"
    bl_label = "Add Glow"
    bl_description = "Add reversible compositor fog glow to this scene's render output"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            finish.enable(context.scene)
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, "Glow added. The compositor applies it to the final image")
        return {"FINISHED"}


class AFTERGLOW_OT_remove_render_glow(bpy.types.Operator):
    bl_idname = "afterglow.remove_render_glow"
    bl_label = "Remove Glow"
    bl_description = "Remove Noctune's glow node while preserving the compositor's other nodes"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            finish.disable(context.scene)
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


def draw_library(layout, context, scope="ALL"):
    from .ui import draw_note
    s = context.scene.afterglow
    playing = getattr(context.screen, "is_animation_playing", False)
    scope = edition.style_scope(scope)
    suffix = "" if scope == "ALL" else "_" + scope
    title = {"ALL": "My Looks", "OBJECT": "My Object Looks", "EFFECTS": "My Environment Looks"}[scope]
    head, body = layout.panel("AFTERGLOW_library" + suffix, default_closed=True)
    head.label(text=f"{title} ({len(s.saved_looks)})", icon="PRESET")
    if not body:
        return
    body.prop(s, "new_look_name", text="Name")
    row = body.row()
    row.enabled = not playing
    row.operator("afterglow.save_look", icon="ADD")
    if s.saved_looks:
        body.template_list("AFTERGLOW_UL_saved_looks", "", s, "saved_looks", s, "saved_look_index", rows=3)
        row = body.row(align=True)
        row.enabled = not playing and selected_look(context.scene) is not None
        row.operator("afterglow.apply_saved_look", text="Apply Only", icon="BRUSH_DATA").scope = scope
        row.operator("afterglow.remove_saved_look", text="", icon="TRASH")
        row = body.row()
        row.enabled = not playing and selected_look(context.scene) is not None
        row.scale_y = 1.25
        row.operator("afterglow.build_saved_look", text="Apply & Build", icon="PLAY").scope = scope
    row = body.row(align=True)
    row.enabled = not playing
    row.operator("afterglow.import_look", icon="IMPORT")
    sub = row.row()
    sub.enabled = selected_look(context.scene) is not None
    sub.operator("afterglow.export_look", icon="EXPORT")
    note = {"OBJECT": "Applies object styling. Stored in this .blend; export to reuse elsewhere.",
            "EFFECTS": "Applies environment styling. Stored in this .blend; export to reuse elsewhere.",
            "ALL": "Stored in this .blend. Export to reuse elsewhere. Object styling is captured from the first reacting object by name."}[scope]
    draw_note(body, note, context)


def draw_finish(layout, context):
    from .ui import draw_note
    head, body = layout.panel("AFTERGLOW_finish", default_closed=True)
    head.label(text="Glow", icon="LIGHT")
    if body:
        body.enabled = not getattr(context.screen, "is_animation_playing", False)
        body.prop(context.scene.afterglow, "easy_glow", text="Add Soft Glow on Build")
        tree = context.scene.compositing_node_group
        owned = [node for node in tree.nodes if node.get(finish.TAG)] if tree else []
        if owned:
            body.operator("afterglow.remove_render_glow", icon="X")
            if finish.enabled(context.scene):
                glare = owned[0]
                body.prop(glare.inputs["Strength"], "default_value", text="Glow Strength", slider=True)
                body.prop(glare.inputs["Threshold"], "default_value", text="Highlight Threshold")
                if context.scene.render.use_compositing:
                    draw_note(body, "Glow is active in the final render. Other compositor nodes remain editable.", context, "CHECKMARK")
                else:
                    draw_note(body, "Glow is connected. Enable Compositing in Blender's Render Properties to see it.", context, "INFO")
            else:
                draw_note(body, "The glow node is bypassed. Remove it here to clear the old node before adding another.", context, "INFO")
        else:
            body.operator("afterglow.add_render_glow", icon="SHADING_RENDERED")
            draw_note(body, "Optional glow for the final image. Keeps the existing output chain and render engine.", context)


PRO_CLASSES = (AFTERGLOW_OT_build_show,) if edition.IS_PRO else ()
CLASSES = (*PRO_CLASSES, AFTERGLOW_OT_save_look, AFTERGLOW_OT_apply_saved_look,
           AFTERGLOW_OT_build_saved_look, AFTERGLOW_OT_remove_saved_look,
           AFTERGLOW_OT_import_look, AFTERGLOW_OT_export_look, AFTERGLOW_UL_saved_looks,
           AFTERGLOW_OT_add_render_glow, AFTERGLOW_OT_remove_render_glow)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
