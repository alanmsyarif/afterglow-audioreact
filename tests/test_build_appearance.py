"""Build/update never applies a look; appearance changes require an explicit action."""
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import core, easy, finish, nodes, presets, ui


class Layout:
    def __init__(self, log, full=True):
        self.log, self.full = log, full
    def child(self, *args, **kwargs): return Layout(self.log, False)
    row = column = box = child
    def panel(self, name, **kwargs):
        assert self.full, name
        self.log.append(('panel', name))
        return self.child(), Layout(self.log)
    def prop(self, owner, name, **kwargs): self.log.append(('prop', name))
    def operator(self, name, **kwargs):
        self.log.append(('op', name));return SimpleNamespace()
    def label(self, **kwargs): pass
    def separator(self): pass
    def template_list(self, *args, **kwargs): pass


def draw():
    log=[]
    ui.draw_object_workflow(Layout(log), bpy.context)
    return log


def test_build_precedes_optional_appearance_and_look_is_never_implicit():
    scene, _, objects = helpers.visual_scene()
    assert not ui.AFTERGLOW_OT_apply_look.poll(bpy.context)
    assert ('prop','creative_look') not in draw()
    assert ('op','afterglow.apply_look') not in draw()
    original = objects[0].active_material
    snapshot = helpers.snapshot(original)
    scene.afterglow.creative_look = 'AURORA_FLOW'
    with patch.object(presets, 'preflight', side_effect=AssertionError('No preset preflight during build')), \
         patch.object(presets, 'apply', side_effect=AssertionError('No preset application during build')), \
         patch.object(finish, 'enable', side_effect=AssertionError('Glow is an explicit appearance action')):
        assert bpy.ops.afterglow.create_visual() == {'FINISHED'}
    mod = core.reaction(objects[0])
    assert not nodes.get_input(mod,'Use Colors')
    assert nodes.get_input(mod,'Intensity') == 1.0 and nodes.get_input(mod,'Glow') == 0.0
    assert objects[0].active_material == original and scene.compositing_node_group is None
    assert ui.AFTERGLOW_OT_apply_look.poll(bpy.context)
    log=draw()
    assert log.index(('op','afterglow.create_visual')) < log.index(('panel','AFTERGLOW_object_appearance'))
    assert log.count(('op','afterglow.apply_look')) == 1
    assert bpy.ops.afterglow.apply_look() == {'FINISHED'}
    assert abs(nodes.get_input(mod,'Decay')-0.5) < 1e-6
    core.remove(scene)
    assert objects[0].active_material == original and helpers.snapshot(original) == snapshot


def test_update_keeps_custom_appearance_even_after_browsing_another_look():
    scene, _, objects = helpers.visual_scene()
    easy.create_visual(bpy.context)
    expected = {'Use Colors': True, 'Color A': (0.2,0.3,0.4,1.0), 'Color B': (0.7,0.8,0.9,1.0),
                'Intensity': 2.5, 'Glow': 0.13, 'Decay': 0.23, 'Delay': 0.09}
    for obj in objects:
        for name,value in expected.items(): nodes.set_input(core.reaction(obj),name,value)
    scene.afterglow.creative_look='SIGNAL_GRID'
    assert bpy.ops.afterglow.create_visual() == {'FINISHED'}
    for obj in objects:
        mod=core.reaction(obj)
        for name,value in expected.items():
            actual=nodes.get_input(mod,name)
            if isinstance(value,tuple): assert all(abs(a-b)<1e-6 for a,b in zip(actual,value)),name
            else: assert abs(actual-value)<1e-6,name
    context=SimpleNamespace(scene=scene,screen=SimpleNamespace(is_animation_playing=True))
    assert not ui.AFTERGLOW_OT_apply_look.poll(context)


helpers.run(globals())
