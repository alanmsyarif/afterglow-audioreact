"""The shared Afterglow geometry node group and modifier input helpers."""
import bpy

GROUP_NAME = "Afterglow AudioReact"
TAG = "afterglow"
VERSION = 7  # bump when the interface changes; get_group() migrates older groups
SAMPLES = 8  # ponytail: fixed smoothing sample count; expose it if long tails get steppy
BEAT_NODE = "Beat Length"
TEMPO_TAGS = ("afterglow_tempo_path", "afterglow_tempo_start")  # set by tempo.bake on the group
KICK = (40.0, 150.0)
TAPS = 8

# (name, socket type, default). Every float input is clamped to >= 0. Append only:
# socket identifiers follow creation order and migration relies on them.
INPUTS = (
    ("Sound", "NodeSocketSound", None),
    ("Start Frame", "NodeSocketInt", 1),
    ("Low", "NodeSocketFloat", 20.0),
    ("High", "NodeSocketFloat", 200.0),
    ("Gain", "NodeSocketFloat", 1.0),
    ("Threshold", "NodeSocketFloat", 0.0),
    ("Decay", "NodeSocketFloat", 0.15),
    ("Delay", "NodeSocketFloat", 0.0),
    ("Use Colors", "NodeSocketBool", False),
    ("Color A", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0)),
    ("Color B", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0)),
    ("Ripple", "NodeSocketBool", False),
    ("Beats per Sweep", "NodeSocketFloat", 1.0),
    ("Ripple Position", "NodeSocketFloat", 0.0),
    ("Ripple Gain", "NodeSocketFloat", 1.0),       # used instead of Gain/Threshold while
    ("Ripple Threshold", "NodeSocketFloat", 0.0),  # rippling: the kick band has its own scale
    ("Use Baked", "NodeSocketBool", False),    # Fast Playback: read Baked Peak, skip the sound node
    ("Baked Peak", "NodeSocketFloat", 0.0),    # keyed per frame by bake.py
    ("Through", "NodeSocketBool", False),          # Through Objects: the wave crosses each surface
    ("Wave Radial", "NodeSocketBool", False),      # wave coordinate, written by core.wave()
    ("Wave Axis", "NodeSocketVector", (1.0, 0.0, 0.0)),
    ("Wave Origin", "NodeSocketVector", (0.0, 0.0, 0.0)),
    ("Wave Lo", "NodeSocketFloat", 0.0),
    ("Wave Span", "NodeSocketFloat", 1.0),
    ("Wave Reverse", "NodeSocketBool", False),
    ("Tap Min", "NodeSocketFloat", 0.0),           # this object's range along the wave
    ("Tap Max", "NodeSocketFloat", 1.0),
) + tuple((f"Baked Tap {k}", "NodeSocketFloat", 0.0) for k in range(8)) + (  # taps keyed by bake.py
    ("Meter", "NodeSocketBool", False),      # Level Meter: fill the object along an axis by loudness
    ("Meter Axis", "NodeSocketInt", 2),      # 0 X, 1 Y, 2 Z (object space)
    ("Meter Invert", "NodeSocketBool", False),
)
METER_INPUTS = ("Meter", "Meter Axis", "Meter Invert")  # driven by the object's afterglow props
BAKE_INPUTS = ("Baked Peak",) + tuple(f"Baked Tap {k}" for k in range(TAPS))
WAVE_INPUTS = ("Wave Radial", "Wave Axis", "Wave Origin", "Wave Lo", "Wave Span", "Wave Reverse")
SCENE_INPUTS = {"Sound", "Start Frame", "Ripple", "Beats per Sweep", "Use Baked", "Through", *WAVE_INPUTS}
RIPPLE_PAIR = {"Gain": "Ripple Gain", "Threshold": "Ripple Threshold"}
OBJECT_INPUTS = [name for name, _, _ in INPUTS
                 if name not in SCENE_INPUTS | {"Ripple Position", "Tap Min", "Tap Max", *BAKE_INPUTS, *METER_INPUTS}
                 | set(RIPPLE_PAIR.values())]


def _current():
    return next((g for g in bpy.data.node_groups
                 if g.bl_idname == "GeometryNodeTree" and g.get(TAG, 0) >= VERSION), None)


def migrate():
    """Move modifiers off groups built by an older VERSION, keeping their input values.
    A cheap no-op when there are none, so anything touching modifier inputs can call it."""
    old = [g for g in bpy.data.node_groups if g.bl_idname == "GeometryNodeTree" and 0 < g.get(TAG, 0) < VERSION]
    if not old:
        return
    group = _current() or _build()
    for g in old:
        _carry_tempo(g, group)
        for ob in bpy.data.objects:
            for m in ob.modifiers:
                if m.type == "NODES" and m.node_group == g:
                    m.node_group = group
                    ob.update_tag()
        bpy.data.node_groups.remove(g)
    group.name = GROUP_NAME


def _carry_tempo(old, new):
    """Keep the old group's Beat Length curve (and the user's hand edits) on the new group."""
    oad = old.animation_data
    if not (oad and oad.action) or (new.animation_data and new.animation_data.action):
        return
    ident = oad.action_slot.identifier if oad.action_slot else None
    nad = new.animation_data_create()
    nad.action = oad.action
    slot = next((sl for sl in nad.action.slots if sl.identifier == ident), None)
    if slot:
        nad.action_slot = slot
    for key in TEMPO_TAGS:
        if key in old:
            new[key] = old[key]


def get_group():
    """The current Afterglow group, found by tag so renamed copies are reused."""
    migrate()
    return _current() or _build()


def _socket(mod, name):
    return getattr(mod.properties.inputs, mod.node_group.interface.items_tree[name].identifier)


def set_input(mod, name, value):
    _socket(mod, name).value = value
    mod.id_data.update_tag()  # 5.2: setting properties.inputs from Python doesn't tag the depsgraph


def get_input(mod, name):
    return _socket(mod, name).value


def draw_input(layout, mod, name):
    layout.prop(_socket(mod, name), "value", text=name)


def _build():
    ng = bpy.data.node_groups.new(GROUP_NAME, "GeometryNodeTree")
    ng.is_modifier = True
    ng[TAG] = VERSION
    it = ng.interface
    it.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    it.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    for name, socket_type, default in INPUTS:
        s = it.new_socket(name, in_out="INPUT", socket_type=socket_type)
        if default is not None:
            s.default_value = default
        if socket_type == "NodeSocketFloat" and name != "Wave Lo":
            s.min_value = 0.0
        if name in {"Ripple Position", "Tap Min", "Tap Max"}:
            s.max_value = 1.0
        if name == "Meter Axis":
            s.min_value, s.max_value = 0, 2

    N, L = ng.nodes, ng.links

    def op(operation, a, b=0.0):
        n = N.new("ShaderNodeMath")
        n.operation = operation
        for socket, v in zip(n.inputs, (a, b)):
            if isinstance(v, bpy.types.NodeSocket):
                L.new(v, socket)
            else:
                socket.default_value = v
        return n.outputs[0]

    gi, go = N.new("NodeGroupInput"), N.new("NodeGroupOutput")
    g = gi.outputs
    beat = N.new("ShaderNodeValue")
    beat.name = beat.label = BEAT_NODE
    beat.outputs[0].default_value = 0.5  # seconds per beat (120 BPM) until tempo.bake keys it

    # Ripple: everyone listens to the kick band and lags by position x sweep length.
    low = op("ADD", g["Low"], op("MULTIPLY", g["Ripple"], op("SUBTRACT", KICK[0], g["Low"])))
    high = op("ADD", g["High"], op("MULTIPLY", g["Ripple"], op("SUBTRACT", KICK[1], g["High"])))
    lag = op("MULTIPLY", op("MULTIPLY", g["Ripple"], g["Ripple Position"]),
             op("MULTIPLY", g["Beats per Sweep"], beat.outputs[0]))
    delay = op("ADD", g["Delay"], lag)

    st = N.new("GeometryNodeInputSceneTime")
    # Geometry nodes can't read fps; Seconds / Frame recovers it.
    # At frame 0 this is 0/0 = 0; the frame gate below keeps frame 0 dark when Start Frame >= 1.
    spf = op("DIVIDE", st.outputs["Seconds"], st.outputs["Frame"])
    t = op("SUBTRACT", op("MULTIPLY", op("SUBTRACT", st.outputs["Frame"], g["Start Frame"]), spf), delay)
    step = op("MULTIPLY", g["Decay"], 0.25)
    decay = op("MAXIMUM", g["Decay"], 1e-4)

    # Peak-hold with exponential release: max over i of amp(t - i*step) * exp(-i*step / decay)
    ri, ro = N.new("GeometryNodeRepeatInput"), N.new("GeometryNodeRepeatOutput")
    ri.pair_with_output(ro)
    ro.repeat_items.new("FLOAT", "Peak")
    ri.inputs["Iterations"].default_value = SAMPLES
    age = op("MULTIPLY", ri.outputs["Iteration"], step)
    sample_t = op("SUBTRACT", t, age)
    ss = N.new("GeometryNodeSampleSoundFrequencies")
    L.new(g["Sound"], ss.inputs["Sound"])
    L.new(sample_t, ss.inputs["Time"])
    L.new(low, ss.inputs["Low"])
    L.new(high, ss.inputs["High"])
    weight = op("EXPONENT", op("DIVIDE", age, op("MULTIPLY", decay, -1.0)))
    # 0 before the audio starts: by sample time, and by frame (covers frame 0's missing fps)
    gate = op("MULTIPLY", op("SUBTRACT", 1.0, op("LESS_THAN", sample_t, 0.0)),
              op("SUBTRACT", 1.0, op("LESS_THAN", st.outputs["Frame"], g["Start Frame"])))
    weighted = op("MULTIPLY", op("MULTIPLY", ss.outputs["Amplitude"], weight), gate)
    L.new(op("MAXIMUM", ri.outputs["Peak"], weighted), ro.inputs["Peak"])

    gain = op("ADD", g["Gain"], op("MULTIPLY", g["Ripple"], op("SUBTRACT", g["Ripple Gain"], g["Gain"])))
    threshold = op("ADD", g["Threshold"],
                   op("MULTIPLY", g["Ripple"], op("SUBTRACT", g["Ripple Threshold"], g["Threshold"])))
    # Fast Playback: a lazy Switch, so the Sample Sound branch isn't evaluated when baked.
    baked = N.new("GeometryNodeSwitch")
    baked.input_type = "FLOAT"
    L.new(g["Use Baked"], baked.inputs["Switch"])
    L.new(ro.outputs["Peak"], baked.inputs["False"])
    L.new(g["Baked Peak"], baked.inputs["True"])
    level = op("MULTIPLY", op("MAXIMUM", op("SUBTRACT", baked.outputs[0], threshold), 0.0), gain)
    mix = N.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    L.new(level, mix.inputs[0])         # Factor (clamped to 0..1)
    L.new(g["Color A"], mix.inputs[6])  # A_Color
    L.new(g["Color B"], mix.inputs[7])  # B_Color

    def vec(a, b, c):
        n = N.new("ShaderNodeCombineXYZ")
        for socket, v in zip(n.inputs, (a, b, c)):
            if isinstance(v, bpy.types.NodeSocket):
                L.new(v, socket)
            else:
                socket.default_value = v
        return n.outputs[0]

    # mode: 0 = plain, 1 = Through along an axis, 2 = Through radial (only while baked)
    on = N.new("FunctionNodeBooleanMath")
    on.operation = "AND"
    L.new(g["Through"], on.inputs[0])
    L.new(g["Use Baked"], on.inputs[1])
    mode = op("MULTIPLY", on.outputs[0], op("ADD", g["Wave Radial"], 1.0))

    realize = N.new("GeometryNodeRealizeInstances")
    L.new(g["Geometry"], realize.inputs[0])
    out = realize.outputs[0]

    # Level Meter: the object's own extent along Meter Axis (object space) for the shader.
    box = N.new("GeometryNodeBoundBox")
    L.new(out, box.inputs[0])

    def along_axis(vector):
        sep = N.new("ShaderNodeSeparateXYZ")
        L.new(vector, sep.inputs[0])
        total = None
        for k in range(3):
            eq = op("COMPARE", g["Meter Axis"], float(k))
            eq.node.inputs[2].default_value = 0.1
            term = op("MULTIPLY", sep.outputs[k], eq)
            total = term if total is None else op("ADD", total, term)
        return total

    # mode: 0 off, 1/2/3 = X/Y/Z, negative = fill from the far end
    meter_mode = op("MULTIPLY", op("MULTIPLY", g["Meter"], op("ADD", g["Meter Axis"], 1.0)),
                    op("SUBTRACT", 1.0, op("MULTIPLY", g["Meter Invert"], 2.0)))
    for name, data_type, value in (
        ("ar_meter", "FLOAT_VECTOR", vec(along_axis(box.outputs["Min"]), along_axis(box.outputs["Max"]), meter_mode)),
        ("ar_level", "FLOAT", level),                  # classic material layout reads these 3
        ("ar_color", "FLOAT_COLOR", mix.outputs[2]),
        ("ar_tint", "FLOAT", g["Use Colors"]),
        ("ar_base", "FLOAT_VECTOR", vec(level, g["Use Colors"], mode)),  # wave layout's fallback
    ):
        store = N.new("GeometryNodeStoreNamedAttribute")
        store.data_type = data_type
        store.domain = "POINT"
        store.inputs["Name"].default_value = name
        L.new(out, store.inputs["Geometry"])
        L.new(value, store.inputs["Value"])
        out = store.outputs["Geometry"]

    # Through Objects: constant per-object vectors for the Afterglow Wave shader group, packed
    # tight because Eevee caps attributes per material. Reverse is folded into Lo/Span and the
    # axis/origin share one vector (mode says which). Skipped unless Through and baked.
    lv = [op("MULTIPLY", op("MAXIMUM", op("SUBTRACT", g[f"Baked Tap {k}"], threshold), 0.0), gain)
          for k in range(TAPS)]
    lo = op("ADD", g["Wave Lo"], op("MULTIPLY", g["Wave Reverse"], g["Wave Span"]))
    span = op("MULTIPLY", g["Wave Span"], op("SUBTRACT", 1.0, op("MULTIPLY", g["Wave Reverse"], 2.0)))
    toward = N.new("ShaderNodeVectorMath")
    toward.operation = "SUBTRACT"
    L.new(g["Wave Origin"], toward.inputs[0])
    L.new(g["Wave Axis"], toward.inputs[1])
    scaled = N.new("ShaderNodeVectorMath")
    scaled.operation = "SCALE"
    L.new(toward.outputs["Vector"], scaled.inputs[0])
    L.new(g["Wave Radial"], scaled.inputs["Scale"])
    wave_vec = N.new("ShaderNodeVectorMath")
    wave_vec.operation = "ADD"
    L.new(g["Wave Axis"], wave_vec.inputs[0])
    L.new(scaled.outputs["Vector"], wave_vec.inputs[1])
    waved = out
    for name, value in (
        ("ar_tap0", vec(lv[0], lv[1], lv[2])),
        ("ar_tap1", vec(lv[3], lv[4], lv[5])),
        ("ar_tap2", vec(lv[6], lv[7], lo)),
        ("ar_tap3", vec(span, g["Tap Min"], g["Tap Max"])),
        ("ar_wave", wave_vec.outputs["Vector"]),
        ("ar_color_a", g["Color A"]),
        ("ar_color_b", g["Color B"]),
    ):
        store = N.new("GeometryNodeStoreNamedAttribute")
        store.data_type = "FLOAT_VECTOR"
        store.domain = "POINT"
        store.inputs["Name"].default_value = name
        L.new(waved, store.inputs["Geometry"])
        L.new(value, store.inputs["Value"])
        waved = store.outputs["Geometry"]
    pick = N.new("GeometryNodeSwitch")
    pick.input_type = "GEOMETRY"
    L.new(on.outputs[0], pick.inputs["Switch"])
    L.new(out, pick.inputs["False"])
    L.new(waved, pick.inputs["True"])
    out = pick.outputs[0]
    L.new(out, go.inputs["Geometry"])

    for i, n in enumerate(N):  # ponytail: one row so the tree is at least untangled if opened
        n.location = (i * 180, 0)
    return ng
