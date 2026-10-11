import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty)

from . import bake, core, effects, materials, nodes, tempo


def _sync(self, context):
    core.sync(context.scene)


def _rebake(scene):
    if scene.afterglow.fast_playback:
        try:
            bake.bake_all(scene)
        except ValueError:
            pass  # the panel's Re-bake button reports it


def _sync_rebake(self, context):
    core.sync(context.scene)
    _rebake(context.scene)


def _fast(self, context):
    core.sync(context.scene)  # writes Use Baked
    _rebake(context.scene)


def _replace(self, context):
    """Ripple From / Origin / Reverse / Through changed: move the wave's start, then re-bake."""
    core.sync(context.scene)
    core.replace(context.scene)
    _rebake(context.scene)


class AfterglowFXLayer(bpy.types.PropertyGroup):
    """One stackable environment-effect layer. Floats are keyframeable on the Scene:
    effects.build samples their animation per event, so layers can join as a track builds."""
    enabled: BoolProperty(name="Enabled", default=False,
                          description="This layer fires on its trigger band's onsets (Build Effects to apply)")
    weight: FloatProperty(name="Weight", default=0.8, min=0.0, max=1.0,
                          description="Share of onsets this layer answers; keyframe it to bring the layer in later")
    intensity: FloatProperty(name="Intensity", default=1.0, min=0.0, max=4.0,
                             description="Brightness and size multiplier")
    color: FloatVectorProperty(name="Color", subtype="COLOR", size=3, min=0.0, max=1.0,
                               default=(0.8, 0.9, 1.0), description="Emission and light colour")
    timing: FloatProperty(name="Timing", default=1.0, min=0.25, max=4.0,
                          description="Event duration multiplier")
    trigger_band: EnumProperty(name="Trigger Band", default="LOW", items=effects.BAND_ITEMS,
                               description="Which frequency band's onsets fire this layer")


class AfterglowEffects(bpy.types.PropertyGroup):
    venue_shape: EnumProperty(name="Venue Shape", default="CIRCLE", items=(
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
    strikes: PointerProperty(type=AfterglowFXLayer, name="Data Strikes",
                             description="Thin vertical shafts that snap on over the venue")
    blooms: PointerProperty(type=AfterglowFXLayer, name="Data Blooms",
                            description="Soft spheres of light that swell and fade")
    flashes: PointerProperty(type=AfterglowFXLayer, name="Rapid Flashes",
                             description="Very short whole-venue light bursts")
    trails: PointerProperty(type=AfterglowFXLayer, name="Travelling Light Trails",
                            description="A bright head with a fading tail riding the venue path")
    downward: PointerProperty(type=AfterglowFXLayer, name="Downward Trails",
                              description="Streaks that fall from the top of the venue to the ground")
    gun: PointerProperty(type=AfterglowFXLayer, name="Gun Bursts",
                         description="Short fast directed bursts fired from the rim toward the centre")


def _retempo(self, context):
    """Sound, Start Frame or Ripple changed: sync, and re-key the tempo curve if rippling."""
    core.sync(context.scene)
    if context.scene.afterglow.ripple:
        try:
            tempo.bake(context.scene)
        except ValueError:
            pass  # Apply reports it; keep the previous curve
    _rebake(context.scene)


class AfterglowScene(bpy.types.PropertyGroup):
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


CLASSES = (AfterglowFXLayer, AfterglowEffects, AfterglowScene, AfterglowObject)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.afterglow = PointerProperty(type=AfterglowScene)
    bpy.types.Object.afterglow = PointerProperty(type=AfterglowObject)


def unregister():
    del bpy.types.Object.afterglow
    del bpy.types.Scene.afterglow
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
