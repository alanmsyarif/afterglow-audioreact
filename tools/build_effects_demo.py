"""Repeatable Environment Effects demo: synthetic pulse track (fixture, not a real song),
a circular pillar venue with classic Afterglow reaction, all six effect layers stacked
with Visible Beams on, a saved .blend, and a short sequential EEVEE render window for
motion proof.

Run (the output folder is optional; it defaults to <temp>/afterglow-effects-demo):
  blender -b --factory-startup --python-exit-code 1 --python tools/build_effects_demo.py -- <OUT>

Outputs:
  <OUT>/effects_demo.blend
  <OUT>/frames/%04d.png   (RENDER_START..RENDER_END)
Then make the clip/contact sheet with ffmpeg (see README.md).
"""
import math
import os
import sys
import tempfile
import wave

import numpy as np

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = os.path.abspath(ARGS[0]) if ARGS else os.path.join(tempfile.gettempdir(), "afterglow-effects-demo")
RENDER_START, RENDER_END = 150, 246   # the drop: every layer active, ~4 s motion proof
FPS = 24
DUR = 12.0

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import bpy  # noqa: E402
import afterglow  # noqa: E402
from afterglow import bake, core, effects  # noqa: E402


def make_track(path):
    """Synthetic pulse fixture: sparse kicks, then a build, then a full drop with hats
    and a mid pad so LOW/MID/HIGH-triggered layers all have real onsets to follow."""
    rate = 44100
    n = int(rate * DUR)
    x = np.zeros(n, np.float32)
    t = np.arange(n, dtype=np.float32) / rate

    def thump(t0, freq, amp, decay):
        i0 = int(t0 * rate)
        k = int(decay * 6 * rate)
        tt = np.arange(k, dtype=np.float32) / rate
        seg = amp * np.exp(-tt / decay) * np.sin(2 * np.pi * freq * tt * (1 - 0.3 * tt))
        seg = seg[:n - i0].astype(np.float32)
        x[i0:i0 + len(seg)] += seg

    def hat(t0, amp):
        i0 = int(t0 * rate)
        k = int(0.05 * rate)
        tt = np.arange(k, dtype=np.float32) / rate
        noise = np.sin(2 * np.pi * 6300 * tt) + 0.7 * np.sin(2 * np.pi * 9800 * tt + 1.3)
        seg = (amp * np.exp(-tt / 0.012) * noise)[:n - i0].astype(np.float32)
        x[i0:i0 + len(seg)] += seg

    kicks = [0.5, 1.5, 2.5] + list(np.arange(3.0, DUR - 0.2, 0.5))
    for t0 in kicks:
        thump(float(t0), 55.0, 0.95 if t0 >= 7.0 else 0.8, 0.22)
    for t0 in np.arange(3.0, DUR - 0.05, 0.25):
        hat(float(t0), 0.28 if t0 >= 7.0 else 0.16)
    pad = (t >= 7.0).astype(np.float32)  # MID-band onset when the drop starts
    ramp = np.clip((t - 7.0) * 4.0, 0, 1)
    x += (0.18 * pad * ramp * (np.sin(2 * np.pi * 330 * t) + 0.6 * np.sin(2 * np.pi * 495 * t))).astype(np.float32)
    x = np.clip(x, -1.0, 1.0)
    data = (x * 32000).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())
    return path


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(os.path.join(OUT, "frames"), exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    afterglow.register()
    scene = bpy.context.scene

    wav = make_track(os.path.join(OUT, "fx_demo_track.wav"))  # synthetic fixture
    sound = bpy.data.sounds.load(wav, check_existing=False)

    # --- dark night look
    world = bpy.data.worlds.new("FX World")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.02, 0.03, 0.05, 1.0)
    bg.inputs["Strength"].default_value = 0.35

    ground = bpy.data.meshes.new("Ground")
    import bmesh
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=24.0)
    bm.to_mesh(ground)
    bm.free()
    gmat = bpy.data.materials.new("GroundMat")
    nt = gmat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.015, 0.017, 0.02, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.38
    ground.materials.append(gmat)
    ground_ob = bpy.data.objects.new("Ground", ground)
    scene.collection.objects.link(ground_ob)

    # --- venue: ring of emissive pillars (classic Afterglow reacts on these)
    coll = bpy.data.collections.new("Venue")
    scene.collection.children.link(coll)
    pillar_me = bpy.data.meshes.new("Pillar")
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.16, radius2=0.16, depth=3.2)
    bm.to_mesh(pillar_me)
    bm.free()
    for i in range(10):
        a = 2 * math.pi * i / 10
        mat = bpy.data.materials.new(f"PillarMat{i}")
        mnt = mat.node_tree
        mnt.nodes.clear()
        em = mnt.nodes.new("ShaderNodeEmission")
        em.inputs["Color"].default_value = (0.6, 0.8, 1.0, 1.0)
        em.inputs["Strength"].default_value = 4.0
        out = mnt.nodes.new("ShaderNodeOutputMaterial")
        mnt.links.new(em.outputs[0], out.inputs["Surface"])
        ob = bpy.data.objects.new(f"Pillar{i}", pillar_me)
        ob.location = (5.0 * math.cos(a), 5.0 * math.sin(a), 1.6)
        coll.objects.link(ob)
        ob.data.materials.append(mat)

    # --- camera
    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    scene.collection.objects.link(cam)
    cam.location = (13.5, -13.5, 5.5)
    cam.rotation_euler = (math.radians(74.0), 0.0, math.radians(45.0))
    scene.camera = cam

    # --- classic Afterglow + Fast Playback bake on the pillars
    scene.afterglow.collection = coll
    scene.afterglow.sound = sound
    scene.afterglow.start_frame = 1
    scene.afterglow.fast_playback = True
    core.apply(scene)
    bake.bake_all(scene)

    # --- all six effect layers, stacked, with per-layer colours and trigger bands
    fx = scene.afterglow.effects
    fx.venue_shape = "CIRCLE"
    fx.beams = True  # dashed strike filaments that walk down and land on the beat, gun tracers
    fx.height = 9.0
    fx.margin = 1.1
    fx.seed = 77
    for name, band, weight, intensity, timing, color in (
        ("strikes", "LOW", 1.0, 1.2, 1.0, (0.85, 0.92, 1.0)),
        ("blooms", "LOW", 0.7, 1.0, 1.0, (0.35, 0.75, 1.0)),
        ("flashes", "LOW", 0.5, 1.4, 0.9, (1.0, 1.0, 1.0)),
        ("trails", "MID", 1.0, 1.1, 1.0, (1.0, 0.72, 0.35)),
        ("downward", "HIGH", 0.9, 1.0, 1.0, (0.7, 0.85, 1.0)),
        ("gun", "LOW", 0.8, 1.2, 0.8, (1.0, 0.45, 0.3)),
    ):
        layer = getattr(fx, name)
        layer.enabled = True
        layer.trigger_band = band
        layer.weight = weight
        layer.intensity = intensity
        layer.timing = timing
        layer.color = color
    counts = effects.build(scene)
    print("DEMO-EVENTS", counts)

    scene.frame_start = 1
    scene.frame_end = max(288, scene.frame_end)
    scene.render.fps = FPS

    # --- save the .blend on frame 1
    scene.frame_set(1)
    blend = os.path.join(OUT, "effects_demo.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    print("DEMO-BLEND", blend)

    # --- short sequential EEVEE motion proof
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scene.render.engine = eng
            break
        except TypeError:
            continue
    scene.render.resolution_x = 640
    scene.render.resolution_y = 360
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    try:
        scene.view_settings.view_transform = "Filmic"
        scene.view_settings.look = "Punchy"
    except (TypeError, AttributeError):
        pass
    frames_dir = os.path.join(OUT, "frames")
    for f in range(RENDER_START, RENDER_END + 1):
        scene.frame_set(f)
        scene.render.filepath = os.path.join(frames_dir, f"{f:04d}.png")
        bpy.ops.render.render(write_still=True)
        if f % 24 == 0:
            print("DEMO-FRAME", f, flush=True)
    pngs = [p for p in os.listdir(frames_dir) if p.endswith(".png")]
    print("DEMO-FRAMES", frames_dir, len(pngs))
    print("DEMO-OK")


main()
