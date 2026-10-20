import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import helpers
from helpers import plane, emission_material
from afterglow import finish


def compositor(scene):
    tree = bpy.data.node_groups.new("User compositor", "CompositorNodeTree")
    tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    source = tree.nodes.new("CompositorNodeRGB")
    source.outputs[0].default_value = (0.1, 0.2, 0.3, 1.0)
    output = tree.nodes.new("NodeGroupOutput")
    tree.links.new(source.outputs[0], output.inputs["Image"])
    scene.compositing_node_group = tree
    return tree, source, output


def links(tree):
    return {(link.from_node.name, link.from_socket.identifier,
             link.to_node.name, link.to_socket.identifier) for link in tree.links}


def test_default_glow_is_idempotent_and_removable():
    scene = bpy.context.scene
    assert scene.compositing_node_group is None
    node = finish.enable(scene)
    assert node.inputs["Type"].default_value == "Fog Glow"
    assert finish.enabled(scene)
    assert finish.enable(scene) == node
    assert len(scene.compositing_node_group.nodes) == 3
    assert finish.disable(scene)
    assert not finish.enabled(scene)
    assert not finish.disable(scene)
    assert len(scene.compositing_node_group.nodes) == 2


def test_custom_nodes_values_and_original_links_are_preserved():
    scene = bpy.context.scene
    tree, source, output = compositor(scene)
    before = links(tree)
    node = finish.enable(scene)
    assert scene.compositing_node_group == tree
    assert source.outputs[0].default_value[:] == (0.10000000149011612, 0.20000000298023224, 0.30000001192092896, 1.0)
    assert output.inputs["Image"].links[0].from_node == node
    finish.disable(scene)
    assert links(tree) == before and len(tree.nodes) == 2


def test_shared_trees_are_isolated_for_enable_and_disable():
    scene = bpy.context.scene
    tree, source, output = compositor(scene)
    other = bpy.data.scenes.new("Another user")
    other.compositing_node_group = tree
    before = links(tree)
    finish.enable(scene)
    assert scene.compositing_node_group != tree
    assert other.compositing_node_group == tree
    assert links(tree) == before and len(tree.nodes) == 2
    # Removing from a shared glow tree must likewise leave its other user alone.
    other.compositing_node_group = scene.compositing_node_group
    assert finish.enabled(other)
    finish.disable(scene)
    assert finish.enabled(other) and not finish.enabled(scene)


def test_disable_preserves_user_relinks_and_added_nodes():
    scene = bpy.context.scene
    tree, source, output = compositor(scene)
    finish.enable(scene)
    replacement = tree.nodes.new("CompositorNodeRGB")
    tree.links.new(replacement.outputs[0], output.inputs["Image"])
    assert not finish.enabled(scene)
    finish.disable(scene)
    assert output.inputs["Image"].links[0].from_node == replacement
    assert source in tree.nodes[:] and replacement in tree.nodes[:]


def test_new_grading_after_glow_does_not_create_a_cycle_on_reenable():
    scene = bpy.context.scene
    tree, source, output = compositor(scene)
    node = finish.enable(scene)
    grade = tree.nodes.new("CompositorNodeExposure")
    tree.links.new(node.outputs["Image"], grade.inputs["Image"])
    tree.links.new(grade.outputs["Image"], output.inputs["Image"])
    assert finish.enabled(scene)
    before = links(tree)
    assert finish.enable(scene) == node
    assert links(tree) == before
    finish.disable(scene)
    assert grade.inputs["Image"].links[0].from_node == source
    assert output.inputs["Image"].links[0].from_node == grade


def test_invalid_custom_graph_is_rejected_without_edits():
    scene = bpy.context.scene
    tree, source, output = compositor(scene)
    tree.links.remove(output.inputs["Image"].links[0])
    before = links(tree)
    try:
        finish.enable(scene)
    except ValueError as error:
        assert "Connect the final image" in str(error)
    else:
        raise AssertionError("expected a prerequisite error")
    assert scene.compositing_node_group == tree and len(tree.nodes) == 2 and links(tree) == before
    scene.render.use_compositing = False
    try:
        finish.enable(scene)
    except ValueError as error:
        assert "Enable Compositing" in str(error)
    else:
        raise AssertionError("expected compositing-disabled error")


def test_glow_survives_save_and_reopen():
    scene = bpy.context.scene
    compositor(scene)
    finish.enable(scene)
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "glow.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        assert finish.enabled(bpy.context.scene)
        assert finish.disable(bpy.context.scene)
        tree = bpy.context.scene.compositing_node_group
        assert len(tree.nodes) == 2 and len(tree.links) == 1


def test_glow_brightens_pixels_outside_an_emissive_surface():
    import numpy as np
    scene = bpy.context.scene
    surface = plane("Bright source", material=emission_material("White", strength=20, color=(1, 1, 1, 1)))
    surface.scale = (0.07, 0.07, 1)
    camera = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    scene.collection.objects.link(camera)
    camera.location = (0, 0, 4)
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 4
    scene.camera = camera
    scene.world = bpy.data.worlds.new("Black")
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0
    scene.render.engine = "CYCLES"
    scene.render.compositor_device = "CPU"
    scene.cycles.samples = 1
    scene.render.resolution_x = scene.render.resolution_y = 96
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    with tempfile.TemporaryDirectory() as directory:
        def pixels(name):
            scene.render.filepath = os.path.join(directory, name + ".png")
            bpy.ops.render.render(write_still=True)
            image = bpy.data.images.load(scene.render.filepath, check_existing=False)
            result = np.asarray(image.pixels[:]).reshape(96, 96, 4)[..., :3].copy()
            bpy.data.images.remove(image)
            return result
        plain = pixels("plain")
        finish.enable(scene)
        glowing = pixels("glow")
    y, x = np.indices((96, 96))
    radius = np.maximum(abs(x - 47.5), abs(y - 47.5))
    # The surface covers <2 pixels from center. Measure its immediate outer halo;
    # Fog Glow size is relative to output resolution, so distant pixels at96px
    # are outside its kernel even though the same setting is broad at full HD.
    halo = (radius >= 2.5) & (radius <= 5.5)
    assert glowing[halo].mean() > plain[halo].mean() + 0.005, (glowing[halo].mean(), plain[halo].mean())


helpers.run(globals())
