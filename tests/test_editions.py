"""Install the actual archives in isolated processes, covering the complete Free object workflow."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]

# No repository test helpers: each worker must import only its extracted archive.
WORKER = r'''
import json
import math
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import wave

package, folder, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, str(package))
import bpy
import afterglow
from afterglow import bake, core, creator, edition, effect_schema, lookbook, nodes, presets, workflow

assert Path(afterglow.__file__).resolve().parent == package / "afterglow"
bpy.ops.wm.read_factory_settings(use_empty=True)
afterglow.register()

PAID = {"effects", "environment", "lasers", "network", "looks"}


def rejected(action):
    try:
        action()
    except ValueError:
        return
    raise AssertionError("A paid operation must fail before any mutations")


def native(scene):
    r = scene.render
    return [r.engine, r.filepath, r.resolution_x, r.resolution_y,
            r.resolution_percentage, r.fps, r.fps_base,
            scene.frame_start, scene.frame_current,
            scene.use_preview_range, scene.frame_preview_start]


def fixture():
    scene = bpy.context.scene
    objects = []
    for index in range(2):
        mesh = bpy.data.meshes.new(f"Source mesh {index}")
        mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)],
                         [], [(0, 1, 2, 3)])
        obj = bpy.data.objects.new(f"My object {index}", mesh)
        scene.collection.objects.link(obj)
        obj.location.x = index * 4 - 2
        objects.append(obj)
    workflow.use_selected(SimpleNamespace(scene=scene, selected_objects=objects))
    assert all(obj.material_slots for obj in objects), "Free must prepare emission automatically"
    sound_path = folder / "music.wav"
    rate = 22050
    samples = bytearray()
    for index in range(3 * rate):
        t = index / rate
        frequency = 100 if t < 1 else (1000 if t < 2 else 8000)
        samples.extend(struct.pack("<h", int(20000 * math.sin(2 * math.pi * frequency * t))))
    with wave.open(str(sound_path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(samples)
    scene.afterglow.sound = bpy.data.sounds.load(str(sound_path), check_existing=True)
    scene.afterglow.sound.pack()
    scene.render.filepath = "//my-native-output/"
    scene.render.resolution_x, scene.render.resolution_y = 913, 1519
    scene.frame_start, scene.frame_end = 7, 32
    scene.use_preview_range = True
    scene.frame_preview_start, scene.frame_preview_end = 9, 26
    bpy.context.view_layer.update()
    return scene, objects


def check_free():
    assert not edition.IS_PRO and edition.NAME == "Noctune Free"
    assert not hasattr(bpy.context.scene.afterglow, "effects")
    assert not hasattr(bpy.types, "AFTERGLOW_PT_effects")
    for operator in ("build_environment", "build_effects", "remove_effects", "build_show"):
        try:
            getattr(bpy.ops.afterglow, operator).get_rna_type()
        except (AttributeError, KeyError, RuntimeError):
            pass
        else:
            raise AssertionError(f"Paid operator registered in Free: {operator}")
    assert not {f"afterglow.{name}" for name in PAID}.intersection(sys.modules)


if mode == "free":
    check_free()
    scene, objects = fixture()
    before = native(scene)
    result = workflow.create_reaction(scene, "AURORA_FLOW")  # Legacy ALL defaults to OBJECT.
    assert result["applied"] == result["baked"] == 2 and not bake.outdated(scene)
    assert native(scene) == before
    assert presets.apply(scene, "NEON_CLUB") == len(objects)
    before = [len(bpy.data.objects), len(bpy.data.materials), len(bpy.data.node_groups),
              len(scene.sequence_editor.strips)]
    for action in (lambda: presets.apply(scene, "NEON_CLUB", scope="EFFECTS"),
                   lambda: workflow.create_reaction(scene, "NEON_CLUB", style_scope="EFFECTS"),
                   lambda: workflow.create_show(scene, "NEON_CLUB")):
        rejected(action)
    assert before == [len(bpy.data.objects), len(bpy.data.materials), len(bpy.data.node_groups),
                      len(scene.sequence_editor.strips)]
    scene.afterglow.new_look_name = "My free look"
    assert bpy.ops.afterglow.save_look() == {"FINISHED"}
    saved = lookbook.loads(creator.selected_look(scene).payload)
    assert saved["format"] == "afterglow.object-look" and set(saved) == {"format", "version", "object"}
    saved_path = folder / "object-look.json"
    assert bpy.ops.afterglow.export_look(filepath=str(saved_path)) == {"FINISHED"}
    assert bpy.ops.afterglow.import_look(filepath=str(saved_path)) == {"FINISHED"}
    assert bpy.ops.afterglow.build_saved_look() == {"FINISHED"}
    assert bpy.ops.afterglow.apply_saved_look() == {"FINISHED"}
    assert lookbook.loads(creator.selected_look(scene).payload) == saved
    rejected(lambda: lookbook.apply(scene, saved, scope="EFFECTS"))
    project = folder / "free.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(project))
    bpy.ops.wm.open_mainfile(filepath=str(project))
    scene = bpy.context.scene
    assert scene.afterglow.sound.packed_file and len(scene.afterglow.saved_looks) == 2
    assert not bake.outdated(scene)
    afterglow.unregister()
    afterglow.register()
    check_free()
    objects = list(scene.afterglow.collection.all_objects)
    core.remove(scene)
    assert all(not core.find_modifier(obj) for obj in objects)
    assert not scene.sequence_editor.strips

else:
    raise AssertionError(mode)

afterglow.unregister()
print("EDITION PASS", mode)
'''


def builder():
    spec = importlib.util.spec_from_file_location("afterglow_edition_builder", ROOT / "tools/build_editions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def archive_install(module, edition, folder):
    archive = module.build(edition, folder / "archives")
    parent = folder / edition
    package = parent / "afterglow"
    package.mkdir(parents=True)
    with zipfile.ZipFile(archive) as source:
        source.extractall(package)
    return parent


def run_worker(worker, package, folder, mode):
    env = dict(os.environ, OMP_NUM_THREADS="2", PYTHONNOUSERSITE="1")
    completed = subprocess.run([sys.executable, "-I", str(worker), str(package), str(folder), mode],
                               cwd=folder, env=env, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=180)
    if completed.returncode or f"EDITION PASS {mode}" not in completed.stdout:
        raise AssertionError(f"{mode} failed ({completed.returncode}):\n{completed.stdout}")


def test_clean_free_archive_supports_complete_object_workflow():
    with tempfile.TemporaryDirectory(prefix="afterglow-free-install-") as temporary:
        folder = Path(temporary)
        worker = folder / "worker.py"
        worker.write_text(WORKER)
        package = archive_install(builder(), "free", folder)
        run_worker(worker, package, folder, "free")


if __name__ == "__main__":
    tests = [test_clean_free_archive_supports_complete_object_workflow]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"{len(tests)}/{len(tests)} passed")
