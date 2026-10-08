"""Shared fixtures and a tiny runner for headless Blender tests."""
import math
import os
import struct
import sys
import tempfile
import traceback
import wave

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import afterglow  # noqa: E402


def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if "afterglow" not in bpy.types.Scene.bl_rna.properties:
        afterglow.register()


def run(namespace):
    tests = [(k, v) for k, v in namespace.items() if k.startswith("test_") and callable(v)]
    failed = []
    for name, fn in tests:
        fresh()
        try:
            fn()
            print("PASS", name)
        except Exception:
            traceback.print_exc()
            print("FAIL", name)
            failed.append(name)
    print(f"{len(tests) - len(failed)}/{len(tests)} passed")
    if failed:
        raise RuntimeError(f"failed: {failed}")


def tone_wav():
    path = os.path.join(tempfile.gettempdir(), "afterglow_tone.wav")
    if not os.path.exists(path):
        rate = 44100
        frames = bytearray()
        for i in range(rate * 3):
            t = i / rate
            freq = 100 if t < 1 else (1000 if t < 2 else 8000)
            frames += struct.pack("<h", int(20000 * math.sin(2 * math.pi * freq * t)))
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(frames))
    return path


def sound():
    return bpy.data.sounds.load(tone_wav(), check_existing=True)


def plane(name, collection=None, material=None):
    me = bpy.data.meshes.new(name)
    me.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
    ob = bpy.data.objects.new(name, me)
    (collection or bpy.context.scene.collection).objects.link(ob)
    if material:
        me.materials.append(material)
    return ob


def emission_material(name, strength=5.0, color=(1.0, 0.5, 0.2, 1.0)):
    mat = bpy.data.materials.new(name)
    nt = mat.node_tree
    nt.nodes.clear()
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = strength
    em.inputs["Color"].default_value = color
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    return mat


def at(frame):
    # Blender 5.2: the first evaluation after a depsgraph rebuild (new objects/modifiers)
    # occasionally returns amplitude 0 for every Sample Sound node; the next frame change
    # always recovers (measured 2026-10-08). Step through a neighbor frame so tests
    # measure Afterglow, not that glitch.
    scene = bpy.context.scene
    scene.frame_set(frame + 1)
    scene.frame_set(frame)


def attr(ob, name="ar_level"):
    """Value of a point attribute on the first vertex of ob's evaluated mesh."""
    ev = ob.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    try:
        item = me.attributes[name].data[0]
        return tuple(item.color) if hasattr(item, "color") else (tuple(item.vector) if hasattr(item, "vector") else item.value)
    finally:
        ev.to_mesh_clear()


def snapshot(mat):
    """Node names, links, and unlinked input values; equal snapshots mean identical trees."""
    nt = mat.node_tree
    links = sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier)
                   for l in nt.links)
    values = {}
    for n in nt.nodes:
        for s in n.inputs:
            if s.is_linked or not hasattr(s, "default_value"):
                continue
            v = s.default_value
            if hasattr(v, "__len__") and not isinstance(v, str):
                v = tuple(round(x, 5) for x in v)
            elif isinstance(v, float):
                v = round(v, 5)
            values[(n.name, s.identifier)] = v
    return sorted(n.name for n in nt.nodes), links, values


def v1_group():
    """Turn the current group into what Afterglow v0.2 saved: 11 inputs, tag 1."""
    from afterglow import nodes
    g = nodes.get_group()
    newer = {n for n, _, _ in nodes.INPUTS[11:]}
    for item in [i for i in g.interface.items_tree if getattr(i, "in_out", None) == "INPUT" and i.name in newer]:
        g.interface.remove(item)
    g[nodes.TAG] = 1
    return g


def fcurves(idblock):
    """F-curves of an ID's active action slot (5.x layered actions)."""
    from bpy_extras import anim_utils
    ad = idblock.animation_data
    if not (ad and ad.action):
        return []
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    return list(bag.fcurves) if bag else []


def has_attr(ob, name):
    ev = ob.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    try:
        return name in me.attributes
    finally:
        ev.to_mesh_clear()
