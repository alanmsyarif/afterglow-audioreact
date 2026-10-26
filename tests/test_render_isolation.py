"""No compositor integration, including old graphs and unrelated file rendering."""
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import bpy
import numpy as np
import helpers
import afterglow
from afterglow import core,easy


def graph(scene):
    tree=bpy.data.node_groups.new('User compositor','CompositorNodeTree')
    tree.interface.new_socket(name='Image',in_out='OUTPUT',socket_type='NodeSocketColor')
    rgb=tree.nodes.new('CompositorNodeRGB');rgb.outputs[0].default_value=(.2,.7,.3,1)
    glare=tree.nodes.new('CompositorNodeGlare');glare['afterglow_render_glow']=True
    output=tree.nodes.new('NodeGroupOutput')
    tree.links.new(rgb.outputs[0],glare.inputs['Image']);tree.links.new(glare.outputs['Image'],output.inputs['Image'])
    scene.compositing_node_group=tree
    return tree


def test_glow_module_and_operators_are_removed_from_both_package_and_runtime():
    root=Path(__file__).resolve().parents[1]
    assert not (root/'afterglow/finish.py').exists()
    assert 'afterglow.finish' not in sys.modules
    for name in ['add_render_glow','remove_render_glow']:
        try:getattr(bpy.ops.afterglow,name).get_rna_type()
        except (AttributeError,RuntimeError,KeyError):pass
        else:raise AssertionError('Removed compositor operator still registered')


def test_build_and_registration_preserve_existing_shared_compositor_and_render_flags():
    scene,_,_=helpers.visual_scene()
    tree=graph(scene)
    other=bpy.data.scenes.new('Untouched scene');other.compositing_node_group=tree
    before=(set(tree.nodes),set(tree.links),scene.render.use_compositing,scene.render.use_sequencer)
    scene.afterglow.easy_glow=True  # Old compatibility data must be inert.
    assert not easy.create_visual(bpy.context)['glow']
    assert scene.compositing_node_group==other.compositing_node_group==tree
    assert (set(tree.nodes),set(tree.links),scene.render.use_compositing,scene.render.use_sequencer)==before
    afterglow.unregister();afterglow.register()
    assert scene.compositing_node_group==tree and (set(tree.nodes),set(tree.links))==before[:2]


def test_cycles_final_image_is_identical_when_enabled_and_after_switching_files():
    scene=bpy.context.scene
    obj=helpers.plane('Unrelated source',material=helpers.emission_material('Plain emission',strength=2,color=(.2,.7,.3,1)))
    cam=bpy.data.objects.new('Camera',bpy.data.cameras.new('Camera'));scene.collection.objects.link(cam)
    cam.location=(0,0,4);cam.data.type='ORTHO';cam.data.ortho_scale=4;scene.camera=cam
    scene.render.engine='CYCLES';scene.cycles.samples=1
    scene.render.threads_mode='FIXED';scene.render.threads=2
    scene.render.resolution_x=scene.render.resolution_y=32;scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG'
    with tempfile.TemporaryDirectory() as folder:
        afterglow.unregister()
        plain=str(Path(folder)/'unrelated.blend');bpy.ops.wm.save_as_mainfile(filepath=plain)
        def pixels(label):
            current=bpy.context.scene;current.render.filepath=str(Path(folder)/(label+'.png'))
            bpy.ops.render.render(write_still=True)
            image=bpy.data.images.load(current.render.filepath,check_existing=False)
            data=np.asarray(image.pixels[:]);bpy.data.images.remove(image);return data
        baseline=pixels('disabled');assert baseline.reshape(-1,4)[:,:3].max()>.1
        afterglow.register()
        assert np.array_equal(baseline,pixels('enabled'))
        bpy.ops.wm.read_factory_settings(use_empty=True)
        used,_,_=helpers.visual_scene();easy.create_visual(bpy.context)
        bpy.ops.wm.save_as_mainfile(filepath=str(Path(folder)/'reaction.blend'))
        bpy.ops.wm.open_mainfile(filepath=plain,use_scripts=False)
        assert bpy.context.scene.compositing_node_group is None
        assert np.array_equal(baseline,pixels('unrelated_after_reaction'))


helpers.run(globals())
