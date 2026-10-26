"""Compact shared native shader groups for bounded ripple launches."""
import bpy
from . import ripples


def _launch_group():
    existing = next((g for g in bpy.data.node_groups if g.get("afterglow_ripple_launch") == 1), None)
    if existing:
        return existing
    group = bpy.data.node_groups.new("Noctune Ripple Launch", "ShaderNodeTree")
    group["afterglow_ripple_launch"] = 1
    for name in ("Age", "Amplitude"):
        group.interface.new_socket(name, in_out="INPUT", socket_type="NodeSocketFloat")
    for name in ("Level", "Trail"):
        group.interface.new_socket(name, in_out="OUTPUT", socket_type="NodeSocketFloat")
    N, L = group.nodes, group.links
    from .materials import _shader_tools
    tools = _shader_tools(N, L)
    gi, go = N.new("NodeGroupInput"), N.new("NodeGroupOutput")
    age, raw = gi.outputs["Age"], gi.outputs["Amplitude"]
    op, clamp, attr, xyz, _, mix = tools
    gain, threshold, mode = xyz('ar_base')
    pos, idle, width = xyz('ar_wave')
    intensity, color_mode, dynamics = xyz('ar_color')
    encoded = op('SUBTRACT', mode, 10)
    style = op('FLOORED_MODULO', encoded, 8)
    loop = op('MINIMUM', op('FLOOR', op('DIVIDE', encoded, 8)), 1)
    def eq(code):
        return op('LESS_THAN', op('ABSOLUTE', op('SUBTRACT', style, code)), .1)
    chase, bounce, echo, collision = [eq(k) for k in (2, 3, 4, 5)]
    tail_scale = op('SUBTRACT', .45, op('MULTIPLY', chase, .27))
    tail_end = op('SUBTRACT', 2.5, op('MULTIPLY', chase, 1.5))
    wraps = op('MULTIPLY', loop, op('SUBTRACT', 1, op('MAXIMUM', bounce, collision)))
    def choose(factor, a, b):
        return op('ADD', a, op('MULTIPLY', factor, op('SUBTRACT', b, a)))
    def smooth(x):
        x = clamp(x)
        return op('MULTIPLY', op('MULTIPLY', x, x), op('SUBTRACT', 3, op('MULTIPLY', x, 2)))
    def profile(d, w):
        edge_width = op('MAXIMUM', op('MULTIPLY', w, .16), 1e-5)
        wrapped = op('SUBTRACT', op('FLOORED_MODULO', op('ADD', d, edge_width), 1), edge_width)
        d = choose(wraps, d, wrapped)
        front = smooth(op('DIVIDE', op('ADD', d, edge_width), edge_width))
        decay = op('EXPONENT', op('DIVIDE', op('MAXIMUM', d, 0),
                                   op('MINIMUM', op('MULTIPLY', op('MULTIPLY', w, tail_scale), -1), -1e-5)))
        end = smooth(op('DIVIDE', op('SUBTRACT', op('MULTIPLY', w, tail_end), d),
                                op('MAXIMUM', op('MULTIPLY', w, .4), 1e-5)))
        return op('MULTIPLY', op('MULTIPLY', front, decay), end)
    level = op('MULTIPLY', op('MAXIMUM', op('SUBTRACT', raw, threshold), 0), gain)
    w = op('MULTIPLY', width, op('ADD', 1, op('MULTIPLY', dynamics,
         op('SUBTRACT', op('MULTIPLY', clamp(level), .75), .45))))
    reflected = op('SUBTRACT', 1, op('ABSOLUTE', op('SUBTRACT', 1, op('MINIMUM', op('MAXIMUM', age, 0), 2))))
    head = choose(bounce, age, reflected)
    head = choose(collision, head, op('MULTIPLY', age, .5))
    returning = op('MULTIPLY', bounce, op('GREATER_THAN', age, 1))
    distance = choose(returning, op('SUBTRACT', head, pos), op('SUBTRACT', pos, head))
    distance = choose(collision, distance, op('SUBTRACT', head, op('MINIMUM', pos, op('SUBTRACT', 1, pos))))
    pulse = profile(distance, w)
    pulse = op('MULTIPLY', pulse, op('SUBTRACT', 1, op('MULTIPLY', collision, op('GREATER_THAN', age, 1))))
    trail = clamp(op('DIVIDE', op('MAXIMUM', distance, 0), op('MAXIMUM', w, 1e-5)))
    for k in range(1, 4):
        factor = op('ADD', op('MULTIPLY', chase, .82**k), op('MULTIPLY', echo, .46**k if k < 3 else 0))
        offset = op('ADD', op('MULTIPLY', chase, k*.18), op('MULTIPLY', echo, k*.15))
        follower_distance = op('SUBTRACT', op('SUBTRACT', age, offset), pos)
        follower = op('MULTIPLY', factor, profile(follower_distance, w))
        follower_trail = clamp(op('DIVIDE', op('MAXIMUM', follower_distance, 0), op('MAXIMUM', w, 1e-5)))
        trail = choose(op('GREATER_THAN', follower, pulse), trail, follower_trail)
        pulse = op('MAXIMUM', pulse, follower)
    impact = clamp(op('SUBTRACT', 1, op('DIVIDE', op('ABSOLUTE', op('SUBTRACT', pos, .5)),
                                       op('MAXIMUM', op('MULTIPLY', w, 1.5), .03))))
    impact = op('MULTIPLY', op('MULTIPLY', impact, impact), op('MULTIPLY', collision,
                op('MULTIPLY', op('SUBTRACT', 1, op('LESS_THAN', age, 1)),
                   clamp(op('SUBTRACT', 1, op('DIVIDE', op('SUBTRACT', age, 1), .25))))))
    trail = choose(op('GREATER_THAN', impact, pulse), trail, 0.)
    pulse = op('MAXIMUM', pulse, impact)
    life = 1.8
    for code in (2, 3, 4, 5):
        life = choose(eq(code), life, ripples.lifetime(code))
    life = choose(loop, life, ripples.lifetime(1, True))
    fade = clamp(op('DIVIDE', op('SUBTRACT', life, age), .35))
    valid = op('MULTIPLY', op('SUBTRACT', 1, op('LESS_THAN', age, 0)),
                          op('SUBTRACT', 1, op('GREATER_THAN', age, life)))
    value = op('MULTIPLY', level, op('MULTIPLY', pulse, op('MULTIPLY', fade, valid)))
    L.new(value, go.inputs["Level"])
    L.new(trail, go.inputs["Trail"])
    return group


def build(N, L, tools):
    op, clamp, attr, xyz, _, mix = tools
    gain, threshold, mode = xyz('ar_base')
    pos, idle, width = xyz('ar_wave')
    intensity, color_mode, dynamics = xyz('ar_color')
    group = _launch_group()
    packed = [v for k in range(4) for v in xyz(f'ar_tap{k}')]
    def choose(factor, a, b):
        return op('ADD', a, op('MULTIPLY', factor, op('SUBTRACT', b, a)))
    maximum, color_weight = 0., 0.
    for slot in range(ripples.SLOTS):
        node = N.new('ShaderNodeGroup')
        node.node_tree = group
        L.new(packed[2*slot], node.inputs['Age'])
        L.new(packed[2*slot+1], node.inputs['Amplitude'])
        value = node.outputs['Level']
        wins = op('GREATER_THAN', value, maximum)
        color_weight = choose(wins, color_weight, node.outputs['Trail'])
        maximum = op('MAXIMUM', maximum, value)
    level = op('MAXIMUM', op('MULTIPLY', maximum, intensity), idle)
    trail_colors = op('GREATER_THAN', color_mode, 1.5)
    color_factor = choose(trail_colors, clamp(maximum), color_weight)
    color = mix(color_factor, attr('ar_color_a', 'Vector'), attr('ar_color_b', 'Vector'))
    return level, color, op('MINIMUM', color_mode, 1), op('GREATER_THAN', mode, 9.5)
