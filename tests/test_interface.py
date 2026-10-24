"""The everyday interface stays clear, safe during playback and natively drawable."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import bake, core, ui, workflow


class Layout:
    """Record real RNA controls and reject native full-width panel violations."""
    def __init__(self, log, *, enabled=True, full_width=True, opened=(), path="root"):
        self.log, self.enabled = log, enabled
        self.full_width, self.opened, self.path = full_width, opened, path

    def child(self, kind, full_width=False):
        return Layout(self.log, enabled=self.enabled, full_width=full_width,
                      opened=self.opened, path=self.path + "/" + kind)

    def row(self, *args, **kwargs):
        return self.child("row")

    def column(self, *args, **kwargs):
        return self.child("column")

    def box(self, *args, **kwargs):
        return self.child("box")

    def panel(self, name, default_closed=False):
        assert self.full_width, f"Native UILayout.panel requires full width: {self.path}"
        self.log.append(dict(kind="panel", name=name, closed=default_closed, path=self.path))
        opened = self.opened is None or name in self.opened or not default_closed
        return self.child(name + "/head", True), self.child(name + "/body", True) if opened else None

    def prop(self, data, name, **kwargs):
        assert name in data.bl_rna.properties, name
        self.log.append(dict(kind="prop", name=name, text=kwargs.get("text", name),
                             enabled=self.enabled, path=self.path))

    def operator(self, name, **kwargs):
        self.log.append(dict(kind="op", name=name, text=kwargs.get("text", ""),
                             enabled=self.enabled, path=self.path))
        return SimpleNamespace()

    def label(self, **kwargs):
        self.log.append(dict(kind="label", name=kwargs.get("text", ""), path=self.path))

    def template_list(self, *args, **kwargs):
        pass

    def separator(self):
        pass


def context(scene, obj=None, *, playing=False, width=300):
    return SimpleNamespace(scene=scene, object=obj,
                           selected_objects=[obj] if obj else [],
                           screen=SimpleNamespace(is_animation_playing=playing),
                           region=SimpleNamespace(width=width),
                           preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=1.0)))


def draw(ctx, opened=()):
    log = []
    ui.AFTERGLOW_PT_panel.draw(SimpleNamespace(layout=Layout(log, opened=opened)), ctx)
    return log


def controls(log, kind, name):
    return [entry for entry in log if entry["kind"] == kind and entry["name"] == name]


def built_scene():
    scene, _, objects = helpers.visual_scene()
    workflow.create_reaction(scene, "NEON_CLUB")
    return scene, objects[0]


def test_basic_workflow_is_music_objects_look_and_one_build_action():
    log = draw(context(bpy.context.scene))
    labels = [entry["name"] for entry in log if entry["kind"] == "label"]
    assert labels.index("Music") < labels.index("Objects") < labels.index("Look & Build")
    assert controls(log, "prop", "sound")[0]["text"] == "Track"
    assert controls(log, "op", "afterglow.use_selected")[0]["text"] == "Use Selected"
    assert not controls(log, "prop", "start_frame")
    assert not controls(log, "op", "afterglow.create_reaction")
    assert not controls(log, "op", "afterglow.remove")
    main = controls(log, "op", "afterglow.create_visual")
    assert len(main) == 1 and main[0]["text"] == "Build Reaction" and not main[0]["enabled"]
    assert any("Applies the selected Object Look" in label for label in labels)


def test_missing_track_disables_build_but_keeps_audio_reload_available():
    scene, _, _ = helpers.visual_scene()
    with tempfile.TemporaryDirectory() as folder:
        scene.afterglow.sound.filepath = str(Path(folder) / "missing.wav")
        log = draw(context(scene))
    assert not controls(log, "op", "afterglow.create_visual")[0]["enabled"]
    assert controls(log, "op", "afterglow.load_sound")[0]["enabled"]
    assert any("missing" in entry["name"].lower() for entry in log if entry["kind"] == "label")


def test_appearance_precedes_optional_response_and_tools_keep_full_width():
    scene, obj = built_scene()
    scene.afterglow.show_advanced = True
    log = draw(context(scene, obj), opened=None)
    panels = [entry["name"] for entry in log if entry["kind"] == "panel"]
    assert panels.index("AFTERGLOW_selected") < panels.index("AFTERGLOW_ripple")
    assert panels.index("AFTERGLOW_finish") < panels.index("AFTERGLOW_library_OBJECT") < panels.index("AFTERGLOW_tools")
    appearance = next(index for index, entry in enumerate(log) if entry["kind"] == "prop" and entry["text"] == "Intensity")
    response = next(index for index, entry in enumerate(log) if entry["kind"] == "panel" and entry["name"] == "AFTERGLOW_selected_response")
    assert appearance < response
    assert controls(log, "op", "afterglow.create_visual")[0]["text"] == "Update Reaction"
    custom = controls(log, "op", "afterglow.create_reaction")[0]
    assert custom["text"] == "Build with Current Settings"
    remove = controls(log, "op", "afterglow.remove")[0]
    assert "AFTERGLOW_object_cleanup" in remove["path"] and "AFTERGLOW_tools" not in remove["path"]


def test_response_stays_closed_and_glow_and_timing_have_single_homes():
    scene, obj = built_scene()
    scene.afterglow.show_advanced = True
    log = draw(context(scene, obj), opened=("AFTERGLOW_finish",))
    assert len(controls(log, "prop", "start_frame")) == 1
    assert "/box/row" in controls(log, "prop", "start_frame")[0]["path"]
    assert len(controls(log, "prop", "easy_glow")) == 1
    assert controls(log, "prop", "easy_glow")[0]["text"] == "Add Soft Glow on Build"
    assert "AFTERGLOW_finish" in controls(log, "prop", "easy_glow")[0]["path"]
    assert any(entry["kind"] == "prop" and entry["text"] == "Intensity" for entry in log)
    assert not any(entry["kind"] == "prop" and entry["text"] in ("Low", "Gain", "Delay") for entry in log)


def test_playback_draw_never_analyzes_audio_or_scans_stale_playback():
    scene, obj = built_scene()
    scene.afterglow.show_advanced = True
    with patch.object(workflow, "inspect", side_effect=AssertionError("No preflight during draw")), \
            patch.object(core, "load_mono", side_effect=AssertionError("No audio analysis during draw")), \
            patch.object(bake, "outdated", side_effect=AssertionError("No stale scan during playback")):
        log = draw(context(scene, obj, playing=True), opened=None)
    for name in ("afterglow.load_sound", "afterglow.use_selected", "afterglow.create_visual",
                 "afterglow.create_reaction", "afterglow.bake", "afterglow.remove"):
        assert controls(log, "op", name) and not controls(log, "op", name)[0]["enabled"], name
    assert not controls(log, "prop", "creative_look")[0]["enabled"]


def test_cleanup_stays_reachable_after_switching_away_from_reaction_collection():
    scene, _ = built_scene()
    scene.afterglow.collection = None
    assert not scene.afterglow.show_advanced
    log = draw(context(scene), opened=("AFTERGLOW_object_cleanup",))
    assert controls(log, "op", "afterglow.remove")[0]["enabled"]
    assert not controls(log, "op", "afterglow.create_reaction")


helpers.run(globals())
