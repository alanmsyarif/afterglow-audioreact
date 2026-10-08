import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import helpers  # noqa: E402
from helpers import emission_material, snapshot  # noqa: E402
from afterglow import materials  # noqa: E402


def tagged(mat):
    return [n for n in mat.node_tree.nodes if n.get(materials.TAG)]


def test_emission_patch_and_restore():
    mat = emission_material("m", strength=5.0)
    before = snapshot(mat)
    assert materials.patch_material(mat)
    em = mat.node_tree.nodes["Emission"]
    mul = em.inputs["Strength"].links[0].from_node
    assert mul.bl_idname == "ShaderNodeMath" and mul.inputs[0].default_value == 5.0
    assert em.inputs["Color"].links[0].from_node.bl_idname == "ShaderNodeMix"
    groups = [n for n in tagged(mat) if n.bl_idname == "ShaderNodeGroup"]
    assert len(groups) == 1 and groups[0].node_tree == materials.level_group(), groups  # classic: Level group
    assert mul.inputs[1].links[0].from_node == groups[0]
    assert materials.unpatch_material(mat)
    assert snapshot(mat) == before
    assert not tagged(mat)


def test_principled_linked_strength_restored():
    mat = bpy.data.materials.new("p")
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    val = nt.nodes.new("ShaderNodeValue")
    val.outputs[0].default_value = 3.0
    nt.links.new(val.outputs[0], bsdf.inputs["Emission Strength"])
    bsdf.inputs["Emission Color"].default_value = (0.2, 0.4, 1.0, 1.0)
    before = snapshot(mat)
    assert materials.has_emission(mat)
    assert materials.patch_material(mat)
    assert bsdf.inputs["Emission Strength"].links[0].from_node.bl_idname == "ShaderNodeMath"
    assert materials.unpatch_material(mat)
    assert snapshot(mat) == before


def test_principled_without_emission_ignored():
    mat = bpy.data.materials.new("plain")
    before = snapshot(mat)
    assert not materials.has_emission(mat)
    assert not materials.patch_material(mat)
    assert snapshot(mat) == before


def test_idempotent():
    mat = emission_material("m")
    assert materials.patch_material(mat)
    count = len(mat.node_tree.nodes)
    assert not materials.patch_material(mat)
    assert len(mat.node_tree.nodes) == count
    assert materials.unpatch_material(mat)
    assert not materials.unpatch_material(mat)


def test_multiple_emitters_all_patched():
    mat = emission_material("m")
    nt = mat.node_tree
    em2 = nt.nodes.new("ShaderNodeEmission")
    add = nt.nodes.new("ShaderNodeAddShader")
    out = nt.nodes["Material Output"]
    nt.links.new(nt.nodes["Emission"].outputs[0], add.inputs[0])
    nt.links.new(em2.outputs[0], add.inputs[1])
    nt.links.new(add.outputs[0], out.inputs["Surface"])
    before = snapshot(mat)
    assert materials.patch_material(mat)
    assert len([n for n in tagged(mat) if n.bl_idname == "ShaderNodeMath"]) == 2
    assert materials.unpatch_material(mat)
    assert snapshot(mat) == before


def test_unpatch_survives_deleted_target():
    mat = emission_material("m")
    materials.patch_material(mat)
    mat.node_tree.nodes.remove(mat.node_tree.nodes["Emission"])
    assert materials.unpatch_material(mat)
    assert not tagged(mat)


def test_unpatch_follows_renamed_target():
    mat = emission_material("m")
    nt = mat.node_tree
    val = nt.nodes.new("ShaderNodeValue")
    val.outputs[0].default_value = 7.0
    nt.links.new(val.outputs[0], nt.nodes["Emission"].inputs["Strength"])
    materials.patch_material(mat)
    nt.nodes["Emission"].name = "Glow"
    assert materials.unpatch_material(mat)
    strength = nt.nodes["Glow"].inputs["Strength"]
    assert strength.is_linked and strength.links[0].from_node == val
    assert not tagged(mat)


def test_layout_switch_repatches():
    mat = emission_material("m")
    before = snapshot(mat)
    assert materials.patch_material(mat)
    assert materials.patch_material(mat, wave=True)  # Through turned on: re-patch with the Wave group
    assert all(n[materials.TAG] == materials.LAYOUT_WAVE for n in tagged(mat))
    groups = [n for n in tagged(mat) if n.bl_idname == "ShaderNodeGroup"]
    assert len(groups) == 1 and groups[0].node_tree == materials.wave_group()
    assert not materials.patch_material(mat, wave=True)
    assert materials.patch_material(mat)  # and back to classic
    assert all(n[materials.TAG] == materials.LAYOUT_CLASSIC for n in tagged(mat))
    assert materials.unpatch_material(mat)
    assert snapshot(mat) == before


def test_old_v06_patch_is_repatched():
    mat = emission_material("m")
    materials.patch_material(mat, wave=True)
    for n in tagged(mat):
        n[materials.TAG] = 2  # the v0.6 layout (12 attributes)
    assert materials.patch_material(mat, wave=True)
    assert all(n[materials.TAG] == materials.LAYOUT_WAVE for n in tagged(mat))


def test_shader_group_survives_migration():
    from afterglow import nodes
    wave = materials.wave_group()
    old = nodes.get_group()
    old[nodes.TAG] = nodes.VERSION - 1
    nodes.get_group()  # migrates the geometry group
    assert materials.WAVE_GROUP in bpy.data.node_groups and bpy.data.node_groups[materials.WAVE_GROUP] == wave


def test_old_classic_patch_is_repatched():
    mat = emission_material("m")
    before = snapshot(mat)
    materials.patch_material(mat)
    for n in tagged(mat):
        n[materials.TAG] = 1  # v0.6 classic layout (plain Attribute nodes, no meter)
    assert materials.patch_material(mat)
    assert all(n[materials.TAG] == materials.LAYOUT_CLASSIC for n in tagged(mat))
    assert materials.unpatch_material(mat)
    assert snapshot(mat) == before


helpers.run(globals())
