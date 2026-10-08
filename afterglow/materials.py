"""Patch emission inputs to read Afterglow attributes, and undo it exactly."""
import bpy

TAG = "afterglow"
LAYOUT_CLASSIC = 4  # Afterglow Level group, 4 attributes (incl. Level Meter); 1 was plain Attribute nodes
LAYOUT_WAVE = 3     # Through Objects: the Afterglow Wave group, 9 attributes (2 was v0.6's 12-attribute layout)
WAVE_GROUP = "Afterglow Wave"
LEVEL_GROUP = "Afterglow Level"
METER_SOFT = 0.04  # Level Meter fill edge, as a fraction of the object's height
TAPS = 8


def _targets(nt):
    """Yield (node, strength socket, color socket) for every emission input in the tree."""
    for n in nt.nodes:
        if n.get(TAG):
            continue
        if n.bl_idname == "ShaderNodeEmission":
            yield n, n.inputs["Strength"], n.inputs["Color"]
        elif n.bl_idname == "ShaderNodeBsdfPrincipled":
            s = n.inputs["Emission Strength"]
            if s.is_linked or s.default_value > 0:
                yield n, s, n.inputs["Emission Color"]


def has_emission(mat):
    return bool(mat and mat.node_tree and next(_targets(mat.node_tree), None))


def is_patched(mat):
    return bool(mat and mat.node_tree) and any(n.get(TAG) for n in mat.node_tree.nodes)


def _new(nt, idname, near, dx, dy=0, layout=LAYOUT_CLASSIC):
    n = nt.nodes.new(idname)
    n[TAG] = layout
    n.label = "Afterglow"
    n.location = (near.location.x - dx, near.location.y - dy)
    return n


def _route(nt, patch, orig_index, out, node, target):
    """Move target's original link/value into patch.inputs[orig_index], then feed target from out."""
    src = patch.inputs[orig_index]
    if target.is_linked:
        nt.links.new(target.links[0].from_socket, src)
    else:
        src.default_value = target.default_value
    nt.links.new(out, target)
    patch[TAG + "_to"] = node.name + "|" + target.identifier
    patch[TAG + "_from"] = orig_index


def _shader_tools(N, L):
    """Small builders shared by the Afterglow shader groups."""
    def op(operation, a, b=0.0):
        n = N.new("ShaderNodeMath")
        n.operation = operation
        for socket, v in zip(n.inputs, (a, b)):
            if isinstance(v, bpy.types.NodeSocket):
                L.new(v, socket)
            else:
                socket.default_value = v
        return n.outputs[0]

    def clamp01(x):
        return op("MINIMUM", op("MAXIMUM", x, 0.0), 1.0)

    def attr(name, output):
        a = N.new("ShaderNodeAttribute")
        a.attribute_type = "GEOMETRY"
        a.attribute_name = name
        return a.outputs[output]

    def xyz(name):
        s = N.new("ShaderNodeSeparateXYZ")
        L.new(attr(name, "Vector"), s.inputs[0])
        return s.outputs

    def vmath(operation, a, b=None):
        n = N.new("ShaderNodeVectorMath")
        n.operation = operation
        L.new(a, n.inputs[0])
        if b is not None:
            L.new(b, n.inputs[1])
        return n

    def mix(factor, a, b):
        m = N.new("ShaderNodeMix")
        m.data_type = "RGBA"
        L.new(factor, m.inputs[0])
        L.new(a, m.inputs[6])
        L.new(b, m.inputs[7])
        return m.outputs[2]

    return op, clamp01, attr, xyz, vmath, mix


def level_group():
    """Shared shader group for the classic layout: the object level, or a Level Meter fill."""
    return next((g for g in bpy.data.node_groups if g.bl_idname == "ShaderNodeTree"
                 and g.get(TAG) == LAYOUT_CLASSIC), None) or _build_level()


def _build_level():
    ng = bpy.data.node_groups.new(LEVEL_GROUP, "ShaderNodeTree")
    ng[TAG] = LAYOUT_CLASSIC
    for name, kind in (("Level", "NodeSocketFloat"), ("Color", "NodeSocketColor"), ("Tint", "NodeSocketFloat")):
        ng.interface.new_socket(name, in_out="OUTPUT", socket_type=kind)
    N, L = ng.nodes, ng.links
    out = N.new("NodeGroupOutput")
    op, clamp01, attr, xyz, vmath, mix = _shader_tools(N, L)
    level = attr("ar_level", "Fac")
    lo, hi, mode = xyz("ar_meter")
    m = op("ABSOLUTE", mode)
    # 0 when the meter is off, or the object has no extent along the axis (a flat panel would
    # otherwise flash fully lit and speckle from float noise): then it shows the plain level
    on = op("MULTIPLY", op("MINIMUM", m, 1.0), op("GREATER_THAN", op("SUBTRACT", hi, lo), 1e-4))
    local = N.new("ShaderNodeSeparateXYZ")
    L.new(N.new("ShaderNodeTexCoord").outputs["Object"], local.inputs[0])  # object space, per pixel
    comp = None
    for k in range(3):
        eq = op("COMPARE", m, float(k + 1))
        eq.node.inputs[2].default_value = 0.1
        term = op("MULTIPLY", local.outputs[k], eq)
        comp = term if comp is None else op("ADD", comp, term)
    t = clamp01(op("DIVIDE", op("SUBTRACT", comp, lo), op("MAXIMUM", op("SUBTRACT", hi, lo), 1e-6)))
    t = op("ADD", t, op("MULTIPLY", op("LESS_THAN", mode, 0.0), op("SUBTRACT", 1.0, op("MULTIPLY", t, 2.0))))
    # lit below the fill line (loudness 0..1 = empty..full), with a soft edge
    fill = op("MULTIPLY", clamp01(level), 1.0 + METER_SOFT)
    lit = clamp01(op("DIVIDE", op("SUBTRACT", fill, t), METER_SOFT))
    L.new(op("ADD", level, op("MULTIPLY", on, op("SUBTRACT", lit, level))), out.inputs["Level"])
    L.new(attr("ar_color", "Color"), out.inputs["Color"])
    L.new(attr("ar_tint", "Fac"), out.inputs["Tint"])
    for i, n in enumerate(N):  # ponytail: one row so the tree is at least untangled if opened
        n.location = (i * 180, 0)
    return ng


def wave_group():
    """Shared shader group: per-pixel wave level/color for Through Objects, else pass-through."""
    return next((g for g in bpy.data.node_groups if g.bl_idname == "ShaderNodeTree"
                 and g.get(TAG) == LAYOUT_WAVE), None) or _build_wave()


def _build_wave():
    ng = bpy.data.node_groups.new(WAVE_GROUP, "ShaderNodeTree")
    ng[TAG] = LAYOUT_WAVE
    ng.interface.new_socket("Level", in_out="OUTPUT", socket_type="NodeSocketFloat")
    ng.interface.new_socket("Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    ng.interface.new_socket("Tint", in_out="OUTPUT", socket_type="NodeSocketFloat")
    N, L = ng.nodes, ng.links
    out = N.new("NodeGroupOutput")

    op, clamp01, attr, xyz, vmath, mix = _shader_tools(N, L)

    # 9 attributes in total: Eevee caps attributes per material, so everything is packed.
    p = N.new("ShaderNodeNewGeometry").outputs["Position"]  # world space, per pixel
    level, tint, mode = xyz("ar_base")
    through = op("MINIMUM", mode, 1.0)                       # 0 when not Through + baked
    radial = clamp01(op("SUBTRACT", mode, 1.0))
    wave = attr("ar_wave", "Vector")                         # axis, or origin when radial
    t0, t1, t2, t3 = xyz("ar_tap0"), xyz("ar_tap1"), xyz("ar_tap2"), xyz("ar_tap3")
    taps = [t0[0], t0[1], t0[2], t1[0], t1[1], t1[2], t2[0], t2[1]]
    lo, span, tmin, tmax = t2[2], t3[0], t3[1], t3[2]        # span < 0 means Reverse
    along = vmath("DOT_PRODUCT", p, wave).outputs["Value"]
    radius = vmath("LENGTH", vmath("SUBTRACT", p, wave).outputs["Vector"]).outputs["Value"]
    dist = op("ADD", along, op("MULTIPLY", radial, op("SUBTRACT", radius, along)))
    pos = clamp01(op("DIVIDE", op("SUBTRACT", dist, lo), span))
    u = op("MULTIPLY", clamp01(op("DIVIDE", op("SUBTRACT", pos, tmin),
                                  op("MAXIMUM", op("SUBTRACT", tmax, tmin), 1e-6))), TAPS - 1)
    blend = None
    for k, tap in enumerate(taps):  # hat weights = linear interpolation between neighbouring taps
        term = op("MULTIPLY", tap, op("MAXIMUM", op("SUBTRACT", 1.0, op("ABSOLUTE", op("SUBTRACT", u, float(k)))), 0.0))
        blend = term if blend is None else op("ADD", blend, term)
    L.new(op("ADD", level, op("MULTIPLY", through, op("SUBTRACT", blend, level))), out.inputs["Level"])
    wave_color = mix(blend, attr("ar_color_a", "Vector"), attr("ar_color_b", "Vector"))
    L.new(mix(through, attr("ar_color", "Color"), wave_color), out.inputs["Color"])
    L.new(tint, out.inputs["Tint"])
    for i, n in enumerate(N):  # ponytail: one row so the tree is at least untangled if opened
        n.location = (i * 180, 0)
    return ng


def _layout(mat):
    return min(n[TAG] for n in mat.node_tree.nodes if n.get(TAG))


def patch_material(mat, wave=False):
    """Patch every emission input. wave=True (Through Objects) routes it through the Afterglow
    Wave group, otherwise through the Afterglow Level group. Re-patches a different layout."""
    if not has_emission(mat):
        return False
    want = LAYOUT_WAVE if wave else LAYOUT_CLASSIC
    if is_patched(mat):
        if _layout(mat) == want:
            return False
        unpatch_material(mat)
    nt = mat.node_tree
    for node, strength, color in list(_targets(nt)):
        src = _new(nt, "ShaderNodeGroup", node, 500, layout=want)
        src.node_tree = wave_group() if wave else level_group()
        level, tint, col = src.outputs["Level"], src.outputs["Tint"], src.outputs["Color"]
        mul = _new(nt, "ShaderNodeMath", node, 250, layout=want)
        mul.operation = "MULTIPLY"
        nt.links.new(level, mul.inputs[1])
        _route(nt, mul, 0, mul.outputs[0], node, strength)
        mix = _new(nt, "ShaderNodeMix", node, 250, 200, layout=want)
        mix.data_type = "RGBA"
        nt.links.new(tint, mix.inputs[0])
        nt.links.new(col, mix.inputs[7])
        _route(nt, mix, 6, mix.outputs[2], node, color)
    return True


def unpatch_material(mat):
    if not is_patched(mat):
        return False
    nt = mat.node_tree
    tagged = [n for n in nt.nodes if n.get(TAG)]
    for patch in tagged:
        to = patch.get(TAG + "_to")
        if not to:
            continue
        # The patch's own output link survives renames; the stored name is the fallback.
        target = next((l.to_socket for o in patch.outputs for l in o.links if not l.to_node.get(TAG)), None)
        if target is None:
            node_name, ident = to.rsplit("|", 1)
            node = nt.nodes.get(node_name)
            target = node and next((s for s in node.inputs if s.identifier == ident), None)
        if target is None:
            continue  # user deleted the target; nothing to restore
        src = patch.inputs[patch[TAG + "_from"]]
        if src.is_linked:
            nt.links.new(src.links[0].from_socket, target)
        else:
            target.default_value = src.default_value
    for n in tagged:
        nt.nodes.remove(n)
    return True
