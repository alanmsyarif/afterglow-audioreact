"""Musical pulses, connected routes, native rendering and persistent playback."""
import os
from pathlib import Path
import sys
import tempfile
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from afterglow import bake, core, curve_routes, materials, nodes, ripples, workflow


def poly(name, points, collection=None, closed=False):
    data = bpy.data.curves.new(name, 'CURVE')
    data.dimensions, data.bevel_depth, data.bevel_resolution = '3D', .07, 1
    spline = data.splines.new('POLY')
    spline.points.add(len(points)-1)
    for p, co in zip(spline.points, points):
        p.co = (*co, 1)
    spline.use_cyclic_u = closed
    obj = bpy.data.objects.new(name, data)
    (collection or bpy.context.scene.collection).objects.link(obj)
    data.materials.append(helpers.emission_material(name+' emission', 1, (1,1,1,1)))
    return obj


def configure(objects, style='FLOW'):
    scene = bpy.context.scene
    collection = bpy.data.collections.new('Music objects')
    scene.collection.children.link(collection)
    for obj in objects:
        collection.objects.link(obj)
    s = scene.afterglow
    s.collection, s.sound = collection, helpers.sound()
    s['ripple_style'], s['ripple'], s['through'], s['fast_playback'] = ripples.STYLES[style], True, True, True
    core.apply(scene)
    return scene


def set_inputs(obj, **values):
    mod = core.reaction(obj)
    for name, value in values.items():
        nodes.set_input(mod, name.replace('_',' '), value)
    return mod


def test_profiles_are_distinct_finite_directional_and_beat_sensitive():
    pos = np.linspace(0, 1, 1001)
    profiles = [ripples.shape(1.28 if style == 3 else .72, pos, .12, style) for style in range(1,6)]
    assert all(np.isfinite(p).all() and p.min() >= 0 and p.max() > .7 for p in profiles)
    assert all(not np.allclose(a,b) for i,a in enumerate(profiles) for b in profiles[i+1:])
    returning = ripples.shape(1.6, pos, .12, 3)
    assert abs(pos[returning.argmax()] - .4) < .015
    collision = ripples.shape(1.05, pos, .12, 5)
    assert abs(pos[collision.argmax()] - .5) < .015
    for style in range(1,6):
        assert not ripples.shape(-.1, pos, .12, style).any()
        assert not ripples.shape(10, pos, .12, style).any()
    times = np.linspace(0, 2, 201)
    phases = times*2
    assert ripples.detect(times,np.zeros(len(times)),times,phases) == []
    events = ripples.detect(times, np.exp(-((times-.4)/.04)**2), times, phases)
    assert events and abs(events[0][0]-.4) < .06
    # Dynamics increases spatial width for strong beats without depending on intensity.
    phase = np.full(len(pos), .6)
    weak = ripples.object_levels([(0,0,.25)], np.ones(len(pos)), phase, pos, 1,.16,1,1,0,1,1)
    strong = ripples.object_levels([(0,0,1)], np.ones(len(pos)), phase, pos, 1,.16,1,1,0,1,1)
    assert np.count_nonzero(strong > .2) > np.count_nonzero(weak > .05)


def test_integrated_tempo_is_continuous_and_loop_seam_has_no_reset():
    frames = np.arange(101.)
    beats = np.where(frames < 50, .6, .3)
    phase = ripples.phase_curve(frames, beats, 25, 1)
    assert np.all(np.diff(phase)>0) and abs(phase[1]) < 1e-9
    assert np.diff(phase).max() < .14
    for pos in (0., .9999):
        before = ripples.shape(.9999, pos,.12,1,True)
        after = ripples.shape(1.0001,pos,.12,1,True)
        assert abs(before-after) < .03
    assert not ripples.shape(9, np.linspace(0,1,100), .12,1,True).any()


def test_connected_chain_reverses_authored_edges_and_scales_by_length():
    a=poly('A',[(-4,0,0),(-2,0,0)])
    b=poly('B',[(0,0,0),(-2,0,0)])
    c=poly('C',[(0,0,0),(4,0,0)])
    scene=configure([a,b,c])
    before=set(scene.objects)
    for obj, expected in ((a,(0,.25)),(b,(.5,.25)),(c,(.5,1))):
        m=core.reaction(obj)
        actual=nodes.get_input(m,'Curve Start'),nodes.get_input(m,'Curve End')
        assert np.allclose(actual,expected,atol=.005),(obj.name,actual)
    scene.afterglow.ripple_reverse=True
    assert set(scene.objects)==before
    assert nodes.get_input(core.reaction(b),'Wave Reverse')
    scene.afterglow.ripple_connect=False
    assert all(nodes.get_input(core.reaction(o),'Curve Start')==0 and
               nodes.get_input(core.reaction(o),'Curve End')==1 for o in (a,b,c))


def test_route_branches_split_and_closed_chains_wrap_with_adjustable_tolerance():
    objects=[poly('A',[(0,0,0),(2,0,0)]),poly('B',[(2,0,0),(4,0,0)]),
             poly('C',[(2,0,0),(2,2,0)])]
    scene=configure(objects)
    assert abs(nodes.get_input(core.reaction(objects[1]),'Curve Start')-.5)<.01
    assert abs(nodes.get_input(core.reaction(objects[2]),'Curve Start')-.5)<.01
    # Four open objects also form one cyclic route, rather than four independent loops.
    helpers.fresh()
    corners=((0,0,0),(2,0,0),(2,2,0),(0,2,0))
    objects=[poly(chr(65+i),[corners[i],corners[(i+1)%4]]) for i in range(4)]
    scene=configure(objects)
    assert all(nodes.get_input(core.reaction(o),'Pulse Loop') for o in objects)
    spans=[(nodes.get_input(core.reaction(o),'Curve Start'),nodes.get_input(core.reaction(o),'Curve End')) for o in objects]
    assert np.allclose(spans,[(0,.25),(.25,.5),(.5,.75),(.75,1)],atol=.005),spans
    objects[-1].location.x=.1
    scene.afterglow.ripple_join_distance=.01
    assert not any(nodes.get_input(core.reaction(o),'Pulse Loop') for o in objects)
    scene.afterglow.ripple_join_distance=.2
    assert all(nodes.get_input(core.reaction(o),'Pulse Loop') for o in objects)


def strip():
    mesh=bpy.data.meshes.new('Strip')
    mesh.from_pydata([(-2,-.3,0),(2,-.3,0),(2,.3,0),(-2,.3,0)],[],[(0,1,2,3)])
    obj=bpy.data.objects.new('Strip',mesh);bpy.context.scene.collection.objects.link(obj)
    mat=helpers.emission_material('Authored white',1,(1,1,1,1));mesh.materials.append(mat)
    scene=configure([obj])
    mod=set_inputs(obj, Ripple_Gain=1.,Ripple_Threshold=0.,Pulse_Ready=True,Pulse_Dynamics=0.,Pulse_Phase=.72,
                   Pulse_Amplitude_0=1.,Wave_Lo=-2.,Wave_Span=4.)
    cam=bpy.data.objects.new('Camera',bpy.data.cameras.new('Camera'))
    scene.collection.objects.link(cam);cam.location=(0,0,4);cam.data.type='ORTHO';cam.data.ortho_scale=4.
    scene.camera=cam;scene.world=bpy.data.worlds.new('Black');scene.world.color=(0,0,0)
    scene.render.resolution_x,scene.render.resolution_y,scene.render.resolution_percentage=96,24,100
    scene.view_settings.view_transform='Standard'
    scene.cycles.samples=2;scene.cycles.device='CPU'
    return scene,obj,mod


def row(scene, engine='CYCLES'):
    scene.render.engine=engine
    path=str(Path(tempfile.gettempdir())/'noctune-ripple-profiles.png')
    scene.render.filepath=path
    bpy.ops.render.render(write_still=True)
    image=bpy.data.images.load(path,check_existing=False)
    try:
        w,h=image.size
        return np.array([tuple(image.pixels[((h//2)*w+x)*4:((h//2)*w+x)*4+3]) for x in range(w)])
    finally:bpy.data.images.remove(image)


def encoded(value):
    return np.where(value<=.0031308,12.92*value,1.055*np.maximum(value,0)**(1/2.4)-.055)


def test_all_presets_match_native_shader_in_cycles_and_eevee_with_silence_and_seams():
    scene,obj,mod=strip()
    pos=(np.arange(96)+.5)/96
    for engine in ('CYCLES','BLENDER_EEVEE'):
        for style in range(1,6):
            nodes.set_input(mod,'Pulse Style',style)
            age = 1.28 if style == 3 else .72
            nodes.set_input(mod,'Pulse Phase',age)
            pixels=row(scene,engine)[:,0]
            expected=encoded(ripples.shape(age,pos,.16,style))
            assert np.mean(abs(pixels-expected))<.045,(engine,style,float(np.mean(abs(pixels-expected))))
        nodes.set_input(mod,'Pulse Loop',True)
        nodes.set_input(mod,'Pulse Style',1)
        nodes.set_input(mod,'Pulse Phase',1.02)
        pixels=row(scene,engine)[:,0]
        assert pixels[0]>.65 and pixels[-1]>.4,(engine,pixels[0],pixels[-1])
        nodes.set_input(mod,'Pulse Amplitude 0',0.)
        assert row(scene,engine).max()<.01
        nodes.set_input(mod,'Pulse Amplitude 0',1.)
        nodes.set_input(mod,'Pulse Loop',False)
        nodes.set_input(mod,'Pulse Phase',.72)


def test_optional_trail_colors_preserve_authored_color_until_enabled_and_zero_intensity_is_dark():
    scene,obj,mod=strip()
    emission=obj.data.materials[0].node_tree.nodes['Emission']
    # The original color is retained in Noctune's reversible mix input.
    patch=next(n for n in obj.data.materials[0].node_tree.nodes if n.get(materials.TAG+'_to','').endswith('|Color'))
    patch.inputs[6].default_value=(1,.1,.02,1)
    nodes.set_input(mod,'Color A',(0,1,1,1));nodes.set_input(mod,'Color B',(1,0,1,1))
    original=row(scene)
    assert original[:,0].max()>original[:,1].max()
    nodes.set_input(mod,'Pulse Color',True)
    colored=row(scene)
    assert colored[:,1].max()>.7 and np.max(abs(original-colored))>.4
    nodes.set_input(mod,'Pulse Style',2)
    chase=row(scene)
    assert chase[51,1]>.6 and chase[51,0]<.4, chase[51]
    nodes.set_input(mod,'Pulse Intensity',0.)
    assert row(scene).max()<.01


def test_new_build_defaults_to_flow_but_old_reactions_keep_original_and_cleanup_is_exact():
    scene,collection,objects=helpers.visual_scene()
    before={o:helpers.snapshot(o.data.materials[0]) for o in objects}
    workflow.create_reaction(scene)
    assert scene.afterglow.ripple_style=='FLOW'
    scene.afterglow.ripple=True
    assert scene.afterglow.through and scene.afterglow.fast_playback
    assert not bake.outdated(scene)
    for o in objects:
        m=core.reaction(o)
        assert nodes.get_input(m,'Pulse Ready')
        assert all(fc.is_valid for fc in helpers.fcurves(o))
    core.remove(scene)
    assert all(helpers.snapshot(o.data.materials[0])==before[o] for o in objects)
    assert all(not o.animation_data or not helpers.fcurves(o) for o in objects)
    helpers.fresh()
    scene,_,objects=helpers.visual_scene()
    core.apply(scene)
    workflow.create_reaction(scene)
    assert scene.afterglow.ripple_style=='CLASSIC'


def test_native_pulse_playback_reopens_without_scripts_and_respects_start_delay_and_tail():
    obj=poly('Route',[(-2,-1,0),(2,-1,0),(2,1,0),(-2,1,0)])
    scene=configure([obj],'BOUNCE')
    scene.afterglow.start_frame=25
    m=core.reaction(obj);nodes.set_input(m,'Delay',.25)
    bake.bake_all(scene)
    birth=nodes._socket(m,'Pulse Birth 0').path_from_id('value')
    birth_curve=next(fc for fc in helpers.fcurves(obj) if fc.data_path==birth)
    assert all(p.interpolation=='CONSTANT' for p in birth_curve.keyframe_points)
    phase_path=nodes._socket(m,'Pulse Phase').path_from_id('value')
    assert all(p.interpolation=='LINEAR' for fc in helpers.fcurves(obj) if fc.data_path==phase_path for p in fc.keyframe_points)
    # No transient duplicate geometry is used for routes or launches.
    assert len([o for o in scene.objects if o.type=='CURVE'])==1
    path=str(Path(tempfile.gettempdir())/'noctune-ripple-presets.blend')
    helpers.at(45);before=helpers.attr(obj,'ar_tap0')
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path,use_scripts=False)
    obj=bpy.data.objects['Route'];helpers.at(45)
    assert np.allclose(helpers.attr(obj,'ar_tap0'),before)
    helpers.at(1)
    assert helpers.attr(obj,'ar_tap0')[1]==0
    assert not bake.outdated(bpy.context.scene)


def test_native_lights_use_all_five_presets_and_optional_colors_without_script_callbacks():
    data=bpy.data.lights.new('My Point','POINT');data.energy=800;data.color=(.8,.3,.1)
    obj=bpy.data.objects.new('My Point',data);bpy.context.scene.collection.objects.link(obj)
    scene=configure([obj])
    original=tuple(data.color)
    for style in ('FLOW','CHASE','BOUNCE','ECHO','COLLISION'):
        scene.afterglow.ripple_style=style
        bake.bake_all(scene)
        assert not obj.modifiers and not bake.outdated(scene)
        powers=[]
        for frame in range(1,45):
            helpers.at(frame)
            powers.append(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.energy)
        assert max(powers)>1,style
        assert np.allclose(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.color,original)
        helpers.at(400)
        assert obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.energy<1e-6
    scene.afterglow.ripple_color_trails=True
    helpers.at(4)
    assert not np.allclose(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.color,original)
    scene.afterglow.ripple=False
    helpers.at(4)
    assert np.allclose(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.color,original)
    assert all(fc.driver.is_simple_expression for fc in obj.data.animation_data.drivers)
    core.remove(scene)
    assert obj.data==data and np.allclose(data.color,original)


def test_advanced_surface_choice_survives_music_timing_changes_and_presets_stay_one_click():
    scene,_,objects=helpers.visual_scene()
    workflow.create_reaction(scene)
    scene.afterglow.ripple=True
    assert scene.afterglow.through and scene.afterglow.fast_playback
    scene.afterglow.through=False
    scene.afterglow.start_frame=9
    assert not scene.afterglow.through and not bake.outdated(scene)
    scene.afterglow.ripple_style='COLLISION'
    assert scene.afterglow.through and scene.afterglow.fast_playback
    assert all(nodes.get_input(core.reaction(o),'Pulse Ready') for o in objects)
    assert not bake.outdated(scene)


helpers.run(globals())
