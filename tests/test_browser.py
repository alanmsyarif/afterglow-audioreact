"""Preview callbacks preserve stable look IDs and never load files while drawing."""
import os
import sys
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import browser, edition, presets


class Layout:
    def __init__(self):self.calls=[]
    def template_icon_view(self, owner, name, **kwargs):self.calls.append(('preview',owner,name,kwargs))
    def prop(self,owner,name,**kwargs):self.calls.append(('prop',owner,name,kwargs))


def test_preview_enums_keep_legacy_values_and_live_string_references():
    state=bpy.context.scene.afterglow
    items=browser.object_items(None,None)
    assert browser.object_items(None,None) is items
    assert tuple(item[0] for item in items)==tuple(item[0] for item in presets.OBJECT_ITEMS)
    assert tuple(item[4] for item in items)==tuple(range(5))
    for index,(identifier,_,_) in enumerate(presets.OBJECT_ITEMS):
        state['creative_look']=index
        assert state.creative_look==identifier
    if edition.IS_PRO:
        env=browser.environment_items(None,None)
        assert tuple(item[0] for item in env)==tuple(item[0] for item in presets.ITEMS)
        assert tuple(item[4] for item in env)==tuple(range(5))
        for identifier,_,_ in presets.ITEMS:
            state['environment_look']=identifier
            assert state.environment_look==identifier


def test_browser_uses_cached_thumbnail_popup_without_disk_access_or_look_application():
    state=bpy.context.scene.afterglow
    state.creative_look='AURORA_FLOW'
    original=state.creative_look
    cached=tuple((identifier,label,description,100+index,index)
                 for index,(identifier,label,description) in enumerate(presets.OBJECT_ITEMS))
    layout=Layout()
    with patch.object(browser,'_object_items',cached), \
         patch.object(browser.Path,'is_file',side_effect=AssertionError('No disk scan in draw')), \
         patch.object(browser.Path,'read_bytes',side_effect=AssertionError('No image load in draw')), \
         patch.object(presets,'apply',side_effect=AssertionError('Browsing must not apply styling')):
        assert browser.object_items(None,None) is cached
        browser.draw(layout,state,'creative_look')
    assert layout.calls[0][:3]==('preview',state,'creative_look')
    assert layout.calls[0][3]['show_labels']
    assert state.creative_look==original


def test_missing_previews_keep_the_same_preset_picker_usable():
    state=bpy.context.scene.afterglow
    cached=tuple((identifier,label,description,0,index)
                 for index,(identifier,label,description) in enumerate(presets.OBJECT_ITEMS))
    layout=Layout()
    with patch.object(browser,'_object_items',cached):browser.draw(layout,state,'creative_look')
    assert layout.calls[0][:3]==('prop',state,'creative_look')
    state.creative_look='SIGNAL_GRID'
    assert state.creative_look=='SIGNAL_GRID'


def test_navigation_is_transient_and_does_not_change_scene_look_or_effect_settings():
    state=bpy.context.window_manager.afterglow_ui
    assert bpy.types.WindowManager.bl_rna.properties['afterglow_ui'].is_skip_save
    names=['workflow','object_page']
    if edition.IS_PRO:names+=['environment_page','active_effect']
    for name in names:
        prop=state.bl_rna.properties[name]
        assert prop.is_skip_save and not prop.is_animatable,name
    scene=bpy.context.scene.afterglow
    original=scene.creative_look
    for page in ('SETUP','STYLE','PRESETS'):
        state.object_page=page
        assert scene.creative_look==original
    workflows={item.identifier for item in state.bl_rna.properties['workflow'].enum_items}
    assert workflows==({'OBJECT','ENVIRONMENT'} if edition.IS_PRO else {'OBJECT'})


helpers.run(globals())
