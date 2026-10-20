import time

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty, CollectionProperty)

from . import bake, core, delivery, effects, materials, nodes, presets, starter, tempo

REBAKE_DELAY = 0.3  # s of quiet after the last setting change before the automatic re-bake
_pending = {}       # scene name -> time of its last change still waiting for a re-bake


def _sync(self, context):
    core.sync(context.scene)


def _bake_now(scene):
    if scene.afterglow.fast_playback:
        try:
            bake.bake_all(scene)
        except ValueError:
            pass  # the panel's Re-bake button reports it


def _flush():
    """Timer: re-bake every scene whose settings have been still for REBAKE_DELAY."""
    now = time.monotonic()
    for name, t in list(_pending.items()):
        if now - t >= REBAKE_DELAY:
            del _pending[name]
            scene = bpy.data.scenes.get(name)
            if scene:
                _bake_now(scene)
    return REBAKE_DELAY - (now - min(_pending.values())) if _pending else None


def _interactive():
    return not bpy.app.background


def _rebake(scene):
    """Re-bake after a setting change. Dragging a slider fires this on every step, so the
    UI waits for the value to settle instead of baking each one; scripts and headless runs
    have no event loop for timers and bake at once."""
    if not scene.afterglow.fast_playback:
        return
    if not _interactive():
        _bake_now(scene)
        return
    _pending[scene.name] = time.monotonic()
    if not bpy.app.timers.is_registered(_flush):
        bpy.app.timers.register(_flush, first_interval=REBAKE_DELAY)


def _sync_rebake(self, context):
    core.sync(context.scene)
    _rebake(context.scene)


def _fast(self, context):
    core.sync(context.scene)  # writes Use Baked
    _bake_now(context.scene)  # one click, not a drag: never leave Use Baked without a bake


def _replace(self, context):
    """Ripple From / Origin / Reverse / Through changed: move the wave's start, then re-bake."""
    core.sync(context.scene)
    core.replace(context.scene)
    _rebake(context.scene)


def _live(self, context):
    """A live effect control changed: retune the last build in place, no rebuild."""
    effects.live_update(self.id_data)


class AfterglowFXLayer:
    """One stackable environment-effect layer (mixin: LAYER_TYPES adds each layer's own default
    colour). Floats are keyframeable on the Scene: effects.build samples their animation per
    event, so layers can join as a track builds."""
    enabled: BoolProperty(name="Enabled", default=False,
                          description="This layer fires on its trigger band's onsets (Build Effects to apply)")
    weight: FloatProperty(name="Weight", default=0.8, min=0.0, max=1.0,
                          description="Share of onsets this layer answers; keyframe it to bring the layer in later")
    intensity: FloatProperty(name="Intensity", default=1.0, min=0.0, max=4.0, update=_live,
                             description="Brightness multiplier for the layer's lights, applied live; "
                                         "keyframed, Build samples it per event")
    timing: FloatProperty(name="Timing", default=1.0, min=0.25, max=4.0,
                          description="Event duration multiplier; keyframeable, Build samples it per event")
    trigger_band: EnumProperty(name="Trigger Band", default="LOW", items=effects.BAND_ITEMS,
                               description="Which frequency band's onsets fire this layer")


LAYER_COLORS = {"strikes": (0.55, 0.85, 1.0), "blooms": (1.0, 0.45, 0.85), "flashes": (1.0, 1.0, 1.0),
                "trails": (1.0, 0.7, 0.3), "downward": (0.6, 0.5, 1.0), "gun": (1.0, 0.3, 0.2),
                "signals": (0.5, 0.9, 1.0)}
LAYER_TYPES = {  # one class per layer: a PropertyGroup can't vary a default per instance
    name: type(f"AfterglowFXLayer_{name}", (AfterglowFXLayer, bpy.types.PropertyGroup), {"__annotations__": {
        "color": FloatVectorProperty(name="Color", subtype="COLOR", size=3, min=0.0, max=1.0,
                                     default=c, update=_live, description="Light colour, applied live")}})
    for name, c in LAYER_COLORS.items()}


def _reshape_cage(self, context):
    effects.refresh_cage(context.scene)


class AfterglowEffects(bpy.types.PropertyGroup):
    master_brightness: FloatProperty(name="Master Brightness", default=1.0, min=0.0, max=4.0,
                                     options=set(), update=_live,
                                     description="Retune all built effect lights and graphics live without rebuilding")
    solo_layer: EnumProperty(name="Solo Layer", default="ALL", options=set(), update=_live,
                             items=(("ALL", "All Layers", "See the whole lighting mix"),) + tuple(
                                 (name, name.replace("downward", "cascade trails").replace("gun", "gun bursts").title(),
                                  "Preview this built layer without changing the show settings") for name in effects.LAYERS),
                             description="Preview one built layer while retaining all enabled layers and events")
    timing_offset: FloatProperty(name="Timing Offset", default=0.0, min=-2.0, max=2.0,
                                 soft_min=-0.25, soft_max=0.25, precision=3, unit="TIME_ABSOLUTE",
                                 options=set(), description="Shift all environment events after beat snapping: "
                                 "negative fires earlier, positive later (seconds). Build Effects to apply; "
                                 "does not move the sound or existing object reactions")
    venue_shape: EnumProperty(name="Venue Shape", default="CIRCLE", update=_reshape_cage, items=(
        ("CIRCLE", "Circular", "Emitters ride a circle around the venue centre"),
        ("SQUARE", "Square", "Emitters ride a square around the venue bounds"),
        ("RECT", "Rectangular", "Emitters ride the venue bounds' own rectangle"),
        ("TRIANGLE", "Triangular", "Emitters ride a triangle around the venue bounds"),
    ), description="Emitter paths and spawn footprints follow this shape over the whole venue")
    seed: IntProperty(name="Seed", default=20260, min=0,
                      description="Deterministic event placement; same seed, same show")
    height: FloatProperty(name="Height", default=8.0, min=0.5, soft_max=50.0,
                          description="How far above the venue the effects rise")
    margin: FloatProperty(name="Margin", default=1.15, min=0.2, max=4.0,
                          description="Grow or shrink the venue footprint around the collection")
    beams: BoolProperty(name="Visible Beams", default=False,
                        description="Give each Data Strike and Gun Burst light a glowing shaft mesh, so they "
                        "show as beams in the air (the same in EEVEE and Cycles). Adds tagged shaft meshes "
                        "and materials to the effects collection")
    beam_glow: FloatProperty(name="Glow", default=1.0, min=0.0, soft_max=4.0, update=_live,
                             description="Brightness of the beam shafts, applied live")
    strike_style: EnumProperty(name="Style", default="FILAMENT", options=set(), items=(
        ("FILAMENT", "Filament", "One thin round filament and its glow sleeve"),
        ("TWIN", "Twin", "Two separated parallel filaments"),
        ("RIBBON", "Ribbon", "A flat, wider strip of light"),
    ), description="Data Strike silhouette; Build Effects to apply")
    strike_width: FloatProperty(name="Width", default=1.0, min=0.1, soft_max=4.0,
                                options=set(), description="Strike filament thickness multiplier; Build to apply")
    strike_variation: FloatProperty(name="Variation", default=0.35, min=0.0, max=1.0, subtype="FACTOR",
                                    options=set(), description="Fresh seeded width, rotation, breakup and brightness per hit; "
                                    "0 keeps a uniform look. Beat times never move; Build to apply")
    strike_companions: IntProperty(name="Companions", default=1, min=0, max=4, options=set(),
                                   description="Beaded filaments beside each strike; Build to apply")
    strike_spread: FloatProperty(name="Spread", default=0.625, min=0.0, soft_max=3.0, unit="LENGTH",
                                 options=set(), description="Distance of companion filaments from the strike; Build to apply")
    gun_style: EnumProperty(name="Style", default="TRACER", options=set(), items=(
        ("TRACER", "Tracer", "Expanding tail behind a bright pointed head"),
        ("DART", "Dart", "A broad shoulder near the head, narrowing to a fine tail"),
        ("STREAK", "Streak", "A thin, nearly constant-width streak"),
    ), description="Gun Burst silhouette; Build Effects to apply")
    gun_length: FloatProperty(name="Length", default=1.0, min=0.05, max=4.0, options=set(),
                              description="Tracer length multiplier, independent of width; Build to apply")
    gun_width: FloatProperty(name="Width", default=1.0, min=0.1, soft_max=4.0, options=set(),
                             description="Tracer thickness multiplier; Build to apply")
    gun_variation: FloatProperty(name="Variation", default=0.35, min=0.0, max=1.0, subtype="FACTOR",
                                 options=set(), description="Fresh seeded width, tail length, fade and brightness per hit; "
                                 "0 keeps a uniform look. Beat times never move; Build to apply")
    gun_taper: FloatProperty(name="Taper", default=1.0, min=0.05, max=3.0, options=set(),
                             description="Tail width relative to the selected shape; Build to apply")
    gun_fade: FloatProperty(name="Tail Fade", default=2.0, min=0.25, max=6.0, options=set(),
                            description="Higher values fade the tracer tail sooner; Build to apply")
    beam_direction: EnumProperty(name="Direction", default="DOWN", items=(
        ("DOWN", "Down", "Dashes and beads stream down each Data Strike"),
        ("UP", "Up", "Dashes and beads stream up each Data Strike"),
    ), description="Which way the dashes and beads stream")
    beam_gaps: FloatProperty(name="Gaps", default=0.375, min=0.0, max=1.0, subtype="FACTOR",
                             description="How broken up the Data Strike filaments are: 0 almost solid, "
                             "1 mostly gaps (the companion is always more broken up)")
    beam_dash: FloatProperty(name="Dash Size", default=0.75, min=0.05, soft_max=5.0, unit="LENGTH",
                             description="Length of the dash pattern's features along a Data Strike")
    beam_walk: EnumProperty(name="Walk", default="DOWN", items=(
        ("DOWN", "Down", "Each Data Strike beam grows from the top and lands on the floor on the beat, "
         "then its tail follows it down"),
        ("UP", "Up", "Each Data Strike beam leaves the floor on the beat and grows to the top, then its "
         "tail follows it up"),
        ("OFF", "Off", "Each Data Strike beam appears all at once"),
    ), description="How a Data Strike beam travels in and out")
    beam_walk_time: FloatProperty(name="Walk Time", default=0.25, min=0.02, soft_max=2.0, max=10.0,
                                  unit="TIME_ABSOLUTE",
                                  description="Seconds a Data Strike beam's head takes to cross the whole height "
                                  "(its tail takes as long to leave). Walking Down it starts early, so it "
                                  "still lands on the beat")
    strike_placement: EnumProperty(name="Placement", default="RANDOM", items=(
        ("RANDOM", "Random", "Data Strikes land at random spots inside the venue"),
        ("POINTS", "Points", "Data Strikes land on the strike points you placed by hand"),
    ), description="Where Data Strikes land")
    strike_order: EnumProperty(name="Order", default="SEQUENCE", items=(
        ("SEQUENCE", "Sequence", "Strike the points one after another, in name order"),
        ("RANDOM", "Random", "Strike a point drawn at random each time (the same every build, from the Seed)"),
    ), description="Which strike point each Data Strike lands on")
    strike_points: PointerProperty(type=bpy.types.Collection, name="Strike Points",
                                   description="Every object in this collection is a strike point (only its "
                                   "position on the floor counts). Add Point makes one at the 3D cursor")
    emitter: PointerProperty(type=bpy.types.Object, name="Emitter Cage",
                             description="Effects spawn inside this cage: move, turn (Z) and scale it in the "
                             "viewport. Empty: the venue is fitted to the collection (Height, Margin)")
    beam_flow: FloatProperty(name="Flow", default=1.0, min=0.0, soft_max=4.0,
                             description="How fast the dashes and beads stream down each Data Strike "
                             "(0 holds them still)")
    strikes: PointerProperty(type=LAYER_TYPES["strikes"], name="Data Strikes",
                             description="Spots of light that snap on over the venue and shine straight down")
    blooms: PointerProperty(type=LAYER_TYPES["blooms"], name="Data Blooms",
                            description="Soft pulses of light that swell and fade inside the venue")
    flashes: PointerProperty(type=LAYER_TYPES["flashes"], name="Rapid Flashes",
                             description="Very short whole-venue light bursts")
    trails: PointerProperty(type=LAYER_TYPES["trails"], name="Travelling Light Trails",
                            description="A bright moving light riding the venue path")
    downward: PointerProperty(type=LAYER_TYPES["downward"], name="Cascade Trails",
                              description="A trail of lights travelling one line: down, up, or along the "
                              "camera's view")
    cascade_direction: EnumProperty(name="Direction", default="DOWN", items=(
        ("DOWN", "Down", "Fall from the venue top to the floor"),
        ("UP", "Up", "Rise from the floor to the venue top"),
        ("TO_CAMERA", "To Camera", "Travel from the first surface the active camera looks at straight "
         "toward the lens (follows a moving camera)"),
        ("FROM_CAMERA", "From Camera", "Leave the lens and travel to the first surface the active camera "
         "looks at (follows a moving camera)"),
    ), description="Which line each Cascade Trail travels")
    cascade_placement: EnumProperty(name="Placement", default="RANDOM", items=(
        ("RANDOM", "Random", "Each cascade runs at a random spot inside the venue"),
        ("CENTER", "Center", "Every cascade runs through the venue centre"),
    ), description="Where Down and Up cascades run (camera cascades follow the camera's view axis)")
    cascade_count: IntProperty(name="Count", default=3, min=1, max=8,
                               description="Lights in one cascade, each fainter than the one ahead")
    cascade_spacing: FloatProperty(name="Spacing", default=0.12, min=0.0, max=0.8, subtype="FACTOR",
                                   description="Delay between one cascade light and the next, as a share "
                                   "of the event: wider spacing stretches the trail")
    cascade_size: FloatProperty(name="Size", default=0.25, min=0.0, soft_max=2.0, unit="LENGTH",
                                update=_live, description="Radius of each cascade light, applied live: "
                                "larger gives softer spill and a bigger highlight")
    gun: PointerProperty(type=LAYER_TYPES["gun"], name="Gun Bursts",
                         description="Short fast directed spots fired from the rim toward the centre")
    signals: PointerProperty(type=LAYER_TYPES["signals"], name="Signal Network",
                             description="Audio attacks send a light pulse along connected anchors")
    signal_anchors: PointerProperty(type=bpy.types.Collection, name="Signal Anchors",
                                    description="Anchor objects in name order, using their full world-space "
                                    "positions. Empty: four anchors fitted to the venue")
    signal_speed: FloatProperty(name="Speed", default=8.0, min=0.1, soft_max=50.0,
                                unit="VELOCITY", description="Pulse travel speed in scene units per second; "
                                "layer Timing multiplies the travel duration")
    signal_route: EnumProperty(name="Route", default="CHAIN", items=(
        ("CHAIN", "Chain", "Each attack travels through all anchors in name order"),
        ("SPOKES", "Spokes", "Successive attacks travel from the first anchor to each other anchor in turn"),
    ))
    signal_direction: EnumProperty(name="Direction", default="FORWARD", items=(
        ("FORWARD", "Forward", "Travel from the first anchor"),
        ("REVERSE", "Reverse", "Retrace each route toward the first anchor"),
        ("ALTERNATE", "Alternate", "Alternate forward and reverse travel on successive attacks"),
    ))
    signal_surface: BoolProperty(name="Surface Impacts", default=True,
                                 description="Find scene surfaces below the destination for the arrival flash")
    signal_graphics: BoolProperty(name="Signal Graphics", default=False,
                                  description="Opt in to thin emissive pulse lines and arrival frames; "
                                  "otherwise this layer generates only real lights")
    signal_width: FloatProperty(name="Line Width", default=0.02, min=0.001, soft_max=0.1,
                                unit="LENGTH", description="Diameter of the optional thin signal curves")
    signal_tail: FloatProperty(name="Tail Length", default=0.75, min=0.01, soft_max=10.0,
                               unit="LENGTH", description="Length of the visible pulse behind its moving head")
    signal_frame_size: FloatProperty(name="Frame Size", default=0.8, min=0.05, soft_max=5.0,
                                     unit="LENGTH", description="Size of the broken rectangular arrival glyph")
    signal_glow: FloatProperty(name="Signal Glow", default=1.0, min=0.0, soft_max=4.0,
                               update=_live, description="Emission brightness of signal graphics, applied live")


def _retempo(self, context):
    """Sound, Start Frame or Ripple changed: sync, and re-key the tempo curve if rippling."""
    core.sync(context.scene)
    if context.scene.afterglow.ripple:
        try:
            tempo.bake(context.scene)
        except ValueError:
            pass  # Apply reports it; keep the previous curve
    _rebake(context.scene)


class AfterglowSavedLook(bpy.types.PropertyGroup):
    name: StringProperty(name="Look Name", default="My Look", maxlen=64)
    payload: StringProperty(name="Style Data", options={"HIDDEN"})


class AfterglowScene(bpy.types.PropertyGroup):
    stage_layout: EnumProperty(name="Stage Design", items=starter.LAYOUT_ITEMS, default="RING", options=set(),
                               description="Choose a design, then Add Starter Stage; browsing keeps your scene intact")
    preview_offset: FloatProperty(name="From", default=0.0, min=0.0, soft_max=600.0,
                                  unit="TIME_ABSOLUTE", options=set(), description="Seconds into the audio track to audition")
    preview_duration: FloatProperty(name="Length", default=8.0, min=0.1, soft_max=60.0,
                                    unit="TIME_ABSOLUTE", options=set(), description="Playback-only loop duration in seconds")
    delivery_format: EnumProperty(name="Output Size", items=delivery.FORMAT_ITEMS, default="LANDSCAPE", options=set())
    output_format: EnumProperty(name="File Format", items=delivery.OUTPUT_ITEMS, default="PNG_SEQUENCE", options=set())
    output_path: StringProperty(name="Output Path", default="//afterglow-output/", subtype="FILE_PATH")
    output_include_tail: BoolProperty(name="Include Fade-out", default=True,
                                      description="Keep two seconds after the track for reaction and effect fade-out")
    saved_looks: CollectionProperty(type=AfterglowSavedLook)
    saved_look_index: IntProperty(name="Saved Look", default=0, min=0)
    new_look_name: StringProperty(name="Name", default="My Look", maxlen=64)
    creative_look: EnumProperty(name="Creative Look", items=presets.ITEMS, options=set(),
                                description="Browse a curated style, then press Apply Look; browsing does not change your scene")
    collection: PointerProperty(type=bpy.types.Collection, name="Collection",
                                description="Every emissive object in here reacts", update=_sync)
    sound: PointerProperty(type=bpy.types.Sound, name="Sound", update=_retempo)
    start_frame: IntProperty(name="Start Frame", default=1,
                             description="Frame where the audio starts playing", update=_retempo)
    ripple: BoolProperty(name="Ripple", description="Every kick sweeps across the collection by "
                         "position; the sweep follows the song's tempo", update=_retempo)
    beats_per_sweep: FloatProperty(name="Beats per Sweep", default=1.0, min=0.05, max=16.0,
                                   description="How many beats one sweep takes to cross the collection",
                                   update=_sync_rebake)
    ripple_from: EnumProperty(name="Ripple From", default="LONGEST", update=_replace, items=(
        ("LONGEST", "Longest Axis", "Along the axis the objects are spread out on most"),
        ("X", "X", "Along X, from -X to +X"),
        ("Y", "Y", "Along Y, from -Y to +Y"),
        ("Z", "Z", "Along Z, from -Z to +Z"),
        ("EMPTY", "Empty", "Outward from the Origin object, nearest objects first"),
        ("CENTER", "Collection Center", "Outward from the middle of the collection"),
    ))
    ripple_origin: PointerProperty(type=bpy.types.Object, name="Origin", update=_replace,
                                   description="Where the wave starts (Ripple From: Empty). Moving it "
                                   "takes effect on Re-spread or Apply")
    ripple_reverse: BoolProperty(name="Reverse", description="Flip the direction: from the far end, "
                                 "or inward instead of outward", update=_replace)
    through: BoolProperty(name="Through Objects", description="The wave crosses each object's surface "
                          "pixel by pixel instead of lighting it all at once (needs Fast Playback)",
                          update=_replace)
    fast_playback: BoolProperty(name="Fast Playback", description="Precompute the reaction so playback "
                                "never evaluates the slow sound node. Re-bake after editing bands, "
                                "Decay or Delay in the modifier panel", update=_fast)
    effects: PointerProperty(type=AfterglowEffects, name="Environment Effects")


def _meter(self, context):
    """Level Meter toggled/edited: takes effect at once, even in a file from an older version."""
    nodes.migrate()  # pre-0.7 groups have no Meter inputs until upgraded
    ob = self.id_data
    mod = core.find_modifier(ob)
    if mod:
        core.write_meter(ob, mod)
        for m in core._materials(ob):  # pre-0.7 classic patches can't draw the meter
            if materials.has_emission(m) and not m.library:
                materials.patch_material(m, wave=context.scene.afterglow.through)


class AfterglowObject(bpy.types.PropertyGroup):
    locked: BoolProperty(name="Lock Band", description="Keep this object's frequency band when re-spreading")
    meter: BoolProperty(name="Level Meter", description="Fill the object along an axis by loudness, like "
                        "an equalizer bar, instead of lighting all of it", update=_meter)
    meter_axis: EnumProperty(name="Axis", default="Z", update=_meter, items=(
        ("X", "X", "Fill along the object's local X"),
        ("Y", "Y", "Fill along the object's local Y"),
        ("Z", "Z", "Fill along the object's local Z (up)"),
    ))
    meter_invert: BoolProperty(name="Invert", description="Fill from the far end (top down)", update=_meter)


CLASSES = (*LAYER_TYPES.values(), AfterglowEffects, AfterglowSavedLook, AfterglowScene, AfterglowObject)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.afterglow = PointerProperty(type=AfterglowScene)
    bpy.types.Object.afterglow = PointerProperty(type=AfterglowObject)


def unregister():
    if bpy.app.timers.is_registered(_flush):
        bpy.app.timers.unregister(_flush)
    _pending.clear()
    del bpy.types.Object.afterglow
    del bpy.types.Scene.afterglow
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
