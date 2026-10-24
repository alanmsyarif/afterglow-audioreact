"""Music duration, authored emission and preset silence regression coverage."""
import math
import tempfile
import wave
import numpy as np
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import core, easy, nodes, prepare, presets


def test_timeline_follows_track_offset_rate_and_active_preview_range():
    scene = bpy.context.scene
    scene.frame_end = 10
    scene.use_preview_range = True
    scene.frame_preview_end = 8
    scene.afterglow.sound = helpers.sound()
    assert scene.frame_end == 72 and scene.frame_preview_end == 72
    scene.afterglow.start_frame = 25
    assert scene.frame_end == 96 and scene.frame_preview_end == 96
    scene.render.fps, scene.render.fps_base = 30, 1.001
    core.sync(scene)
    assert scene.frame_end == 114 and scene.frame_preview_end == 114
    scene.frame_end = 200
    core.sync(scene)
    assert scene.frame_end == 200, 'Keep a manual edit until the track configuration changes'
    scene.afterglow.start_frame = 1
    assert scene.frame_end == 90


def test_preset_keeps_authored_emission_and_goes_dark_after_band_release():
    scene, collection, objects = helpers.visual_scene()
    scene.afterglow.easy_glow = False
    # A real silent tail distinguishes idle glow from low spectral leakage.
    audio = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
    audio.close()
    rate = 22050
    t = np.arange(rate * 3) / rate
    pcm = np.rint(np.sin(2 * np.pi * 100 * t) * (t < 0.3) * 20000).astype('<i2')
    with wave.open(audio.name, 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate)
        out.writeframes(pcm.tobytes())
    scene.afterglow.sound = bpy.data.sounds.load(audio.name)
    material = objects[0].active_material
    emission = next(n for n in material.node_tree.nodes if n.type == 'EMISSION')
    emission.inputs['Color'].default_value = (0.2, 0.7, 0.1, 1)
    original = helpers.snapshot(material)
    for obj in objects:
        obj.afterglow.locked = True
    for identifier, _, _ in presets.OBJECT_ITEMS:
        scene.afterglow.creative_look = identifier
        easy.create_visual(bpy.context)
        assert bpy.ops.afterglow.apply_look() == {"FINISHED"}
        for obj in objects:
            mod = core.reaction(obj)
            nodes.set_input(mod, 'Low', 70.0)
            nodes.set_input(mod, 'High', 180.0)
        core.calibrate(scene)
        from afterglow import bake
        bake.bake_all(scene)
        assert objects[0].active_material == material
        assert not nodes.get_input(core.reaction(objects[0]), 'Use Colors')
        assert nodes.get_input(core.reaction(objects[0]), 'Glow') == 0.0
        scene.frame_set(61)
        scene.frame_set(60)
        assert helpers.attr(objects[0], 'ar_level') == 0.0
        assert helpers.attr(objects[0], 'ar_gain')[1] == 0.0
    core.remove(scene)
    assert helpers.snapshot(material) == original


def test_shared_authored_emission_keeps_color_in_its_isolated_copy():
    scene, collection, objects = helpers.visual_scene()
    original = objects[0].active_material
    outside = helpers.plane('Outside reaction', material=original)
    before = helpers.snapshot(original)
    prepare.selected(objects)
    copied = objects[0].active_material
    assert copied != original and outside.active_material == original
    assert prepare.has_authored_emission(objects[0])
    easy.create_visual(bpy.context)
    assert objects[0].active_material == copied
    assert not nodes.get_input(core.reaction(objects[0]), 'Use Colors')
    assert helpers.snapshot(original) == before
    core.remove(scene)
    assert objects[0].active_material == original
    assert helpers.snapshot(original) == before


def test_new_automatic_material_uses_preset_colors_without_an_idle_glow():
    scene = bpy.context.scene
    obj = helpers.plane('No material')
    obj.select_set(True)
    from afterglow import workflow
    workflow.use_selected(bpy.context)
    scene.afterglow.sound = helpers.sound()
    scene.afterglow.easy_glow = False
    easy.create_visual(bpy.context)
    mod = core.reaction(obj)
    assert not nodes.get_input(mod, 'Use Colors')
    assert bpy.ops.afterglow.apply_look() == {"FINISHED"}
    assert nodes.get_input(mod, 'Use Colors') and nodes.get_input(mod, 'Glow') == 0.0
    assert not prepare.has_authored_emission(obj)


helpers.run(globals())
