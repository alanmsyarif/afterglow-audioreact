import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import helpers  # noqa: E402
from helpers import at, emission_material, plane, sound  # noqa: E402
from afterglow import core  # noqa: E402


def center_brightness(scene, frame):
    at(frame)
    scene.render.filepath = os.path.join(tempfile.gettempdir(), f"afterglow_render_{frame}.png")
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(scene.render.filepath)
    w, h = img.size
    i = ((h // 2) * w + w // 2) * 4
    value = sum(img.pixels[i:i + 3]) / 3
    bpy.data.images.remove(img)
    return value


def test_bass_plane_glows_on_bass_only():
    scene = bpy.context.scene
    coll = bpy.data.collections.new("Lights")
    scene.collection.children.link(coll)
    for name in ("a_bass", "b_mid", "c_treble"):
        plane(name, coll, emission_material("mat_" + name))
    bpy.data.objects["b_mid"].hide_render = True
    bpy.data.objects["c_treble"].hide_render = True
    scene.afterglow.collection = coll
    scene.afterglow.sound = sound()
    core.apply(scene)

    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    cam.location = (0, 0, 4)
    scene.camera = cam
    scene.world = bpy.data.worlds.new("w")
    scene.world.color = (0, 0, 0)
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = scene.render.resolution_y = 64

    bass = center_brightness(scene, 13)    # 0.5 s, 100 Hz playing
    mid = center_brightness(scene, 37)     # 1.5 s, 1 kHz playing
    # AgX view transform compresses highlights, so compare loosely, not against raw strength.
    assert bass > 0.2 and mid < 0.05, (bass, mid)


def through_quad(scene, mat):
    from afterglow import nodes
    me = bpy.data.meshes.new("quad")
    me.from_pydata([(-2, -0.5, 0), (2, -0.5, 0), (2, 0.5, 0), (-2, 0.5, 0)], [], [(0, 1, 2, 3)])
    me.materials.append(mat)
    ob = bpy.data.objects.new("quad", me)
    scene.collection.objects.link(ob)
    mod = ob.modifiers.new("Afterglow", "NODES")
    mod.node_group = nodes.get_group()
    nodes.set_input(mod, "Sound", sound())
    for name, v in (("Use Baked", True), ("Through", True), ("Wave Lo", -2.0), ("Wave Span", 4.0),
                    ("Tap Min", 0.0), ("Tap Max", 1.0)):
        nodes.set_input(mod, name, v)
    for k in range(nodes.TAPS):
        nodes.set_input(mod, f"Baked Tap {k}", 1.0 if k in (3, 4) else 0.0)  # bright middle only
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.type, cam.data.ortho_scale = "ORTHO", 4.0
    cam.location = (0, 0, 5)
    scene.collection.objects.link(cam)
    scene.camera = cam
    scene.world = bpy.data.worlds.new("w")
    scene.world.color = (0, 0, 0)
    scene.render.resolution_x, scene.render.resolution_y = 64, 16
    scene.view_settings.view_transform = "Standard"
    return ob


def render_row(scene, engine):
    scene.render.engine = engine
    if engine == "CYCLES":
        scene.cycles.samples = 4
    scene.render.filepath = os.path.join(tempfile.gettempdir(), f"afterglow_through_{engine}.png")
    at(5)
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(scene.render.filepath)
    w, h = img.size
    px = img.pixels[:]
    bpy.data.images.remove(img)
    return [tuple(px[((h // 2) * w + x) * 4:((h // 2) * w + x) * 4 + 3]) for x in range(2, w - 2, 6)]


def test_through_blends_per_pixel_on_one_quad():
    from afterglow import materials
    scene = bpy.context.scene
    mat = emission_material("m", strength=1.0, color=(1.0, 1.0, 1.0, 1.0))
    through_quad(scene, mat)
    materials.patch_material(mat, wave=True)
    for engine in ("BLENDER_EEVEE", "CYCLES"):
        row = [p[0] for p in render_row(scene, engine)]
        # 4 corner vertices all read tap 0 or 7 (dark); only per-pixel blending lights the middle
        assert max(row) > 0.8 and row[0] < 0.2 and row[-1] < 0.2, (engine, row)


def test_wave_layout_fits_attribute_heavy_material():
    from afterglow import materials
    scene = bpy.context.scene
    mat = bpy.data.materials.new("textured")  # Principled + UV image + tangent normal map + generated noise
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    bsdf.inputs["Emission Strength"].default_value = 1.0
    bsdf.inputs["Emission Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.new("t", 8, 8)
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    nmap = nt.nodes.new("ShaderNodeNormalMap")
    nt.links.new(tex.outputs["Color"], nmap.inputs["Color"])
    nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    noise = nt.nodes.new("ShaderNodeTexNoise")
    nt.links.new(nt.nodes.new("ShaderNodeTexCoord").outputs["Generated"], noise.inputs["Vector"])
    nt.links.new(noise.outputs["Fac"], bsdf.inputs["Roughness"])
    ob = through_quad(scene, mat)
    ob.data.uv_layers.new(name="UVMap")
    materials.patch_material(mat, wave=True)
    row = render_row(scene, "BLENDER_EEVEE")
    magenta = [p for p in row if p[0] > 0.9 and p[1] < 0.1 and p[2] > 0.9]
    assert not magenta, row


def test_meter_fills_from_bottom():
    from afterglow import materials, nodes
    scene = bpy.context.scene
    mat = emission_material("m", strength=1.0, color=(1.0, 1.0, 1.0, 1.0))
    me = bpy.data.meshes.new("bar")
    me.from_pydata([(-0.5, -2, 0), (0.5, -2, 0), (0.5, 2, 0), (-0.5, 2, 0)], [], [(0, 1, 2, 3)])
    me.materials.append(mat)
    ob = bpy.data.objects.new("bar", me)
    scene.collection.objects.link(ob)
    mod = ob.modifiers.new("Afterglow", "NODES")
    mod.node_group = nodes.get_group()
    nodes.set_input(mod, "Sound", sound())
    for name, v in (("Use Baked", True), ("Baked Peak", 0.5), ("Meter", True), ("Meter Axis", 1)):
        nodes.set_input(mod, name, v)
    materials.patch_material(mat)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.type, cam.data.ortho_scale = "ORTHO", 4.0
    cam.location = (0, 0, 5)
    scene.collection.objects.link(cam)
    scene.camera = cam
    scene.world = bpy.data.worlds.new("w")
    scene.world.color = (0, 0, 0)
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x, scene.render.resolution_y = 16, 64
    scene.view_settings.view_transform = "Standard"

    def column():
        scene.render.filepath = os.path.join(tempfile.gettempdir(), "afterglow_meter.png")
        at(5)
        bpy.ops.render.render(write_still=True)
        img = bpy.data.images.load(scene.render.filepath)
        w, h = img.size
        px = img.pixels[:]
        bpy.data.images.remove(img)
        return [px[(y * w + w // 2) * 4] for y in range(2, h - 2, 6)]  # bottom to top

    half = column()
    assert half[0] > 0.8 and half[-1] < 0.1, half                  # bottom lit, top dark
    assert all(b <= a + 0.01 for a, b in zip(half, half[1:])), half  # one fill edge (8-bit noise ok)
    nodes.set_input(mod, "Baked Peak", 1.0)
    full = column()
    assert min(full) > 0.8, full
    nodes.set_input(mod, "Baked Peak", 0.0)
    assert max(column()) < 0.05  # silence: nothing glows
    nodes.set_input(mod, "Baked Peak", 0.5)
    nodes.set_input(mod, "Meter Invert", True)
    top_down = column()
    assert top_down[0] < 0.1 and top_down[-1] > 0.8, top_down  # fills from the top


def test_meter_on_flat_object_falls_back_to_level():
    from afterglow import materials, nodes
    scene = bpy.context.scene
    mat = emission_material("m", strength=1.0, color=(1.0, 1.0, 1.0, 1.0))
    me = bpy.data.meshes.new("panel")
    me.from_pydata([(-2, -2, 0), (2, -2, 0), (2, 2, 0), (-2, 2, 0)], [], [(0, 1, 2, 3)])  # flat in Z
    me.materials.append(mat)
    ob = bpy.data.objects.new("panel", me)
    scene.collection.objects.link(ob)
    ob.rotation_euler = (0.0, 0.0, 0.3)  # rotation adds float noise to object-space Z
    mod = ob.modifiers.new("Afterglow", "NODES")
    mod.node_group = nodes.get_group()
    nodes.set_input(mod, "Sound", sound())
    for name, v in (("Use Baked", True), ("Baked Peak", 0.5), ("Meter", True), ("Meter Axis", 2)):
        nodes.set_input(mod, name, v)
    materials.patch_material(mat)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.type, cam.data.ortho_scale = "ORTHO", 2.0
    cam.location = (0, 0, 5)
    scene.collection.objects.link(cam)
    scene.camera = cam
    scene.world = bpy.data.worlds.new("w")
    scene.world.color = (0, 0, 0)
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = scene.render.resolution_y = 16
    scene.view_settings.view_transform = "Standard"
    scene.render.filepath = os.path.join(tempfile.gettempdir(), "afterglow_flat.png")
    at(5)
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(scene.render.filepath)
    vals = img.pixels[0::4]
    bpy.data.images.remove(img)
    # no height along Z: plain level 0.5 everywhere (sRGB ~0.74), not a full-bright or speckled flash
    assert max(vals) - min(vals) < 0.02 and 0.6 < min(vals) < 0.85, (min(vals), max(vals))


helpers.run(globals())
