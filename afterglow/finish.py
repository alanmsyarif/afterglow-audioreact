"""Opt-in, reversible compositor glow for the scene's existing final image.

Shared or linked compositor trees are copied before editing. The local copy is
retained after removing glow so any edits made while previewing remain intact.
"""

import bpy

TAG = "afterglow_render_glow"


def _target(tree):
    outputs = [node for node in tree.nodes
               if node.bl_idname == "NodeGroupOutput" and node.is_active_output]
    if len(outputs) != 1:
        raise ValueError("Render Glow needs one active compositor Group Output")
    sockets = [socket for socket in outputs[0].inputs
               if socket.name == "Image" and socket.bl_idname == "NodeSocketColor"]
    if len(sockets) != 1 or len(sockets[0].links) != 1:
        raise ValueError("Connect the final image to the compositor output named Image first")
    owned = [node for node in tree.nodes if node.get(TAG)]
    if len(owned) > 1 or any(node.bl_idname != "CompositorNodeGlare" for node in owned):
        raise ValueError("The compositor has ambiguous Noctune nodes; keep one Render Glow node")
    return sockets[0], owned[0] if owned else None


def _upstream_contains(image, expected):
    seen = set()
    def visit(node):
        if node == expected:
            return True
        if node in seen:
            return False
        seen.add(node)
        return any(visit(link.from_node) for socket in node.inputs for link in socket.links)
    return any(visit(link.from_node) for link in image.links)


def enabled(scene):
    """Whether our glow currently feeds the scene's active final image."""
    tree = scene.compositing_node_group
    if not tree:
        return False
    try:
        image, node = _target(tree)
    except ValueError:
        return False
    return bool(node and _upstream_contains(image, node))


def enable(scene):
    """Insert Fog Glow before the active Image output, preserving upstream work.

    Ambiguous/unconnected custom graphs are rejected before any mutation. A
    shared tree is copied once, isolating this scene without altering other users.
    Returns the owned Glare node for optional manual compositor adjustments.
    """
    if not scene.is_editable:
        raise ValueError("Make the scene local before adding Render Glow")
    if not scene.render.use_compositing:
        raise ValueError("Enable Compositing in Render Post Processing before adding Render Glow")
    tree = scene.compositing_node_group
    if tree:
        _, node = _target(tree)
        if enabled(scene):
            return node
        if node and any(output.is_linked for output in node.outputs):
            raise ValueError("Render Glow was routed elsewhere; remove it before adding a new glow")
        if tree.users > 1 or not tree.is_editable:
            tree = tree.copy()
            tree.name = "Noctune • " + scene.name + " Compositor"
            scene.compositing_node_group = tree
    else:
        tree = bpy.data.node_groups.new("Noctune • " + scene.name + " Compositor", "CompositorNodeTree")
        tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        render = tree.nodes.new("CompositorNodeRLayers")
        render.location = (-360, 0)
        render.scene = scene
        output = tree.nodes.new("NodeGroupOutput")
        output.location = (320, 0)
        tree.links.new(render.outputs["Image"], output.inputs["Image"])
        scene.compositing_node_group = tree

    image, node = _target(tree)
    source = image.links[0].from_socket
    if node is None:
        node = tree.nodes.new("CompositorNodeGlare")
        node[TAG] = True
        node.name = "Noctune Render Glow"
        node.label = "Noctune • Render Glow"
        node.inputs["Type"].default_value = "Fog Glow"
        node.inputs["Quality"].default_value = "High"
        node.inputs["Threshold"].default_value = 1.0
        node.inputs["Strength"].default_value = 1.0
        node.inputs["Size"].default_value = 0.75
        node.location = (image.node.location.x - 220, image.node.location.y)
    tree.links.new(source, node.inputs["Image"])
    tree.links.new(node.outputs["Image"], image)
    return node


def disable(scene):
    """Bypass and remove only owned glow, retaining user edits and all other nodes.

    Reconnect each destination still using our node to its current input source.
    Destinations a user has already relinked are never touched.
    """
    tree = scene.compositing_node_group
    if not tree:
        return False
    owned = [node for node in tree.nodes if node.get(TAG)]
    if not owned:
        return False
    if not scene.is_editable:
        raise ValueError("Make the scene local before removing Render Glow")
    if tree.users > 1 or not tree.is_editable:
        tree = tree.copy()
        tree.name = "Noctune • " + scene.name + " Compositor"
        scene.compositing_node_group = tree
        owned = [node for node in tree.nodes if node.get(TAG)]
    for node in owned:
        image = node.inputs.get("Image")
        source = image.links[0].from_socket if image and image.is_linked else None
        destinations = [link.to_socket for output in node.outputs for link in output.links]
        if source:
            for destination in destinations:
                tree.links.new(source, destination)
        tree.nodes.remove(node)
    return True
