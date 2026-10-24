"""The organized browser stays compact, natively drawable and safe during playback."""
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import bake, core, edition, nodes, ui, workflow
if edition.IS_PRO:
    from afterglow import effects


class Layout:
    def __init__(self, log, parent=None, full_width=True, opened=(), closed=(), scope='root'):
        self.log, self.parent, self.full_width = log, parent, full_width
        self.opened, self.closed, self.scope = opened, closed, scope
        self.enabled = self.active = True
        self.alert = False
    def available(self):
        return self.enabled and self.active and (self.parent is None or self.parent.available())
    def child(self, *args, **kwargs):
        return Layout(self.log,self,False,self.opened,self.closed,self.scope)
    row = column = box = grid_flow = split = child
    def panel(self, ident, default_closed=False):
        assert self.full_width, f'Native panels require full region width: {ident}'
        self.log.append(('panel',ident,default_closed,self.scope))
        header=self.child();header.scope=ident+':header'
        visible=ident not in self.closed and (not default_closed or ident in self.opened)
        body=Layout(self.log,self,True,self.opened,self.closed,ident) if visible else None
        return header,body
    def prop(self,owner,name,**kwargs):
        assert name in owner.bl_rna.properties,name
        self.log.append(('prop',name,kwargs.get('text'),self.available(),self.scope))
    def operator(self,ident,**kwargs):
        self.log.append(('operator',ident,kwargs.get('text'),self.available(),self.scope))
        return SimpleNamespace()
    def label(self,**kwargs):
        self.log.append(('label',kwargs.get('text',''),self.alert,self.scope))
    def template_list(self,*args,**kwargs):self.log.append(('library',self.scope))
    def template_icon_view(self,owner,name,**kwargs):
        assert name in owner.bl_rna.properties,name
        self.log.append(('preview',name,None,self.available(),self.scope))
    def template_icon(self,*args,**kwargs):self.log.append(('image',self.scope))
    def separator(self,*args,**kwargs):pass


def nav(workflow='OBJECT',page='SETUP',effect='strikes'):
    state=bpy.context.window_manager.afterglow_ui
    state.workflow=workflow
    if workflow=='OBJECT':state.object_page=page
    else:state.environment_page=page
    if edition.IS_PRO:state.active_effect=effect
    return state


def draw(context=None,opened=(),closed=()):
    log=[]
    layout=Layout(log,opened=opened,closed=closed)
    ui.AFTERGLOW_PT_panel.draw(SimpleNamespace(layout=layout),context or bpy.context)
    return log


def operators(log,ident):return [item for item in log if item[:2]==('operator',ident)]
def props(log,name):return [item for item in log if item[:2]==('prop',name)]
def pickers(log,name):return [item for item in log if item[:2] in [('prop',name),('preview',name)]]


def build_objects():
    scene,_,objects=helpers.visual_scene()
    bpy.ops.afterglow.create_visual()
    bpy.context.view_layer.objects.active=objects[0]
    return scene,objects


def test_setup_shows_shared_music_and_one_independent_object_build():
    nav()
    log=draw()
    assert len(props(log,'sound'))==1
    assert operators(log,'afterglow.use_selected')[0][2]=='Use Selected'
    assert len(operators(log,'afterglow.create_visual'))==1
    assert not pickers(log,'creative_look')
    assert not operators(log,'afterglow.apply_look')
    assert not operators(log,'afterglow.build_environment')
    assert not props(log,'bounds_collection')


def test_missing_music_disables_build_but_keeps_reload_available():
    helpers.visual_scene();nav()
    with patch.object(core,'sound_ok',return_value=False):log=draw()
    assert not operators(log,'afterglow.create_visual')[0][3]
    assert operators(log,'afterglow.load_sound')[0][3]
    assert 'missing' in ' '.join(x[1].lower() for x in log if x[0]=='label')


def test_style_prioritizes_appearance_and_keeps_response_optional():
    scene,objects=build_objects();nav(page='STYLE')
    captured=[]
    with patch.object(nodes,'draw_input',side_effect=lambda layout,mod,name,text=None:captured.append(name)):
        log=draw()
    assert {'Intensity','Glow','Use Colors','Color A','Color B'} <= set(captured)
    assert not {'Low','High','Gain','Threshold','Decay','Delay'} & set(captured)
    assert not pickers(log,'creative_look')
    assert any(item[:2]==('panel','AFTERGLOW_selected_response') and item[2] for item in log)
    with patch.object(nodes,'draw_input',side_effect=lambda layout,mod,name,text=None:captured.append(name)):
        draw(opened=('AFTERGLOW_selected_response',))
    assert {'Low','High','Gain','Threshold','Decay','Delay'} <= set(captured)


def test_presets_are_explicit_after_build_and_browsing_does_not_style():
    scene,objects=build_objects();nav(page='PRESETS')
    snapshot={obj.name:tuple(nodes.get_input(core.reaction(obj),name) for name in ('Intensity','Glow','Use Colors','Decay')) for obj in objects}
    scene.afterglow.creative_look='AURORA_FLOW'
    log=draw()
    assert pickers(log,'creative_look')
    assert len(operators(log,'afterglow.apply_look'))==1 and operators(log,'afterglow.apply_look')[0][3]
    assert snapshot=={obj.name:tuple(nodes.get_input(core.reaction(obj),name) for name in ('Intensity','Glow','Use Colors','Decay')) for obj in objects}
    assert not operators(log,'afterglow.create_reaction')
    assert any(item[0]=='panel' and item[1]=='AFTERGLOW_library_OBJECT' for item in log)


def test_cleanup_and_playback_tools_remain_available_from_setup():
    scene,_=build_objects();nav();scene.afterglow.show_advanced=True
    log=draw(opened=('AFTERGLOW_tools','AFTERGLOW_expert_tools','AFTERGLOW_object_cleanup'))
    assert operators(log,'afterglow.bake')
    assert operators(log,'afterglow.calibrate')
    assert operators(log,'afterglow.remove')[0][3]
    scene.afterglow.collection=None
    log=draw(opened=('AFTERGLOW_object_cleanup',))
    assert operators(log,'afterglow.remove')[0][3]


def test_every_page_draws_without_mutating_art_or_analyzing_audio_during_playback():
    scene,objects=build_objects();scene.afterglow.show_advanced=True
    ctx=SimpleNamespace(scene=scene,object=objects[0],window_manager=bpy.context.window_manager,
                        selected_objects=objects,screen=SimpleNamespace(is_animation_playing=True),
                        region=SimpleNamespace(width=220),preferences=bpy.context.preferences)
    snapshot={obj.name:helpers.snapshot(obj.active_material) for obj in objects}
    workflows=['OBJECT']
    if edition.IS_PRO:
        workflows.append('ENVIRONMENT');scene.afterglow.effects.blooms.enabled=True
        bpy.ops.afterglow.build_environment()
    with patch.object(workflow,'inspect',side_effect=AssertionError('No preflight during draw')), \
         patch.object(core,'load_mono',side_effect=AssertionError('No audio decoding during draw')), \
         patch.object(bake,'outdated',side_effect=AssertionError('No bake scan during playback')):
        for workflow_name in workflows:
            for page in ('SETUP','STYLE','PRESETS'):
                nav(workflow_name,page)
                log=draw(ctx,opened=('AFTERGLOW_tools','AFTERGLOW_expert_tools','AFTERGLOW_object_cleanup','AFTERGLOW_environment_cleanup'))
                for name in ('load_sound','use_selected','create_visual','apply_look','create_reaction','bake','remove',
                             'build_environment','use_environment_selected','add_environment_cage','apply_environment_preset','remove_effects'):
                    assert all(not item[3] for item in operators(log,'afterglow.'+name)),(workflow_name,page,name)
    assert snapshot=={obj.name:helpers.snapshot(obj.active_material) for obj in objects}


if edition.IS_PRO:
    def test_environment_setup_has_compact_enables_and_no_effect_inspectors():
        helpers.visual_scene();nav('ENVIRONMENT')
        log=draw()
        assert len(props(log,'enabled'))==len(effects.LAYERS)
        assert len(operators(log,'afterglow.build_environment'))==1
        assert not operators(log,'afterglow.create_visual')
        assert not props(log,'laser_shape') and not props(log,'strike_style')
        assert not pickers(log,'environment_look')
        assert not operators(log,'afterglow.build_environment')[0][3]
        bpy.context.scene.afterglow.effects.blooms.enabled=True
        assert operators(draw(),'afterglow.build_environment')[0][3]

    def test_selected_effect_inspector_only_draws_the_current_effect():
        fx=bpy.context.scene.afterglow.effects
        fx.lasers.enabled=fx.strikes.enabled=True
        nav('ENVIRONMENT','STYLE','lasers')
        log=draw()
        assert props(log,'laser_shape') and not props(log,'strike_style')
        nav('ENVIRONMENT','STYLE','strikes')
        fx.strikes.visible_beam=True
        log=draw()
        assert props(log,'strike_style') and not props(log,'laser_shape')
        assert len(props(log,'trigger_band'))==1

    def test_environment_presets_and_live_mix_stay_independent_of_setup():
        scene,_,_=helpers.visual_scene();scene.afterglow.effects.blooms.enabled=True
        bpy.ops.afterglow.build_environment();scene.afterglow.effects.solo_layer='blooms'
        for page in ('SETUP','STYLE','PRESETS'):
            nav('ENVIRONMENT',page)
            log=draw(closed=('AFTERGLOW_live_mix',))
            assert any(item[0]=='label' and 'Solo' in item[1] and item[2] for item in log)
            assert any(item[:2]==('panel','AFTERGLOW_live_mix') for item in log)
        nav('ENVIRONMENT','PRESETS');log=draw()
        assert pickers(log,'environment_look')
        assert operators(log,'afterglow.apply_environment_preset')[0][3]
        assert len(props(log,'master_brightness'))==1 and len(props(log,'solo_layer'))==1

    def test_custom_bounds_buttons_report_active_cage_and_valid_source_selection():
        nav('ENVIRONMENT')
        assert bpy.ops.afterglow.add_environment_cage()=={'FINISHED'}
        log=draw()
        assert operators(log,'afterglow.add_environment_cage')[0][2]=='Select Cage'
        assert len(props(log,'emitter'))==1
        assert not operators(log,'afterglow.use_environment_selected')[0][3]
        geometry=helpers.plane('Custom bounds')
        for obj in bpy.context.selected_objects:obj.select_set(False)
        geometry.select_set(True)
        assert operators(draw(),'afterglow.use_environment_selected')[0][3]
        geometry[effects.TAG]=1
        assert not operators(draw(),'afterglow.use_environment_selected')[0][3]


helpers.run(globals())
