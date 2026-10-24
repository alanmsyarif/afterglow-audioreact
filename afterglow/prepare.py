"""Make selected objects emission-ready without changing their original materials.

Material overrides belong to the selected object. Persistent slot records retain the
original material IDs across saves, so removing the reaction can restore assignments
without replacing geometry or overwriting a later manual material change.
"""
import bpy

TAG = "afterglow_prepared_materials"
OWNED = "afterglow_auto_emission"
VERSION = 1
STRENGTH = 5.0


def _plain(value):
    if hasattr(value, "keys") and not isinstance(value, bpy.types.ID):
        return {key: _plain(value[key]) for key in value.keys()}
    return value


def _reachable(socket):
    pending = [link.from_node for link in socket.links]
    found = set()
    while pending:
        node = pending.pop()
        if node in found:
            continue
        found.add(node)
        pending.extend(link.from_node for entry in node.inputs for link in entry.links)
    return found


def _has_surface_emission(material):
    if not material or not material.node_tree:
        return False
    outputs = [node for node in material.node_tree.nodes
               if node.type == "OUTPUT_MATERIAL" and node.is_active_output]
    return bool(outputs) and all(any(
        node.type == "EMISSION" or (node.type == "BSDF_PRINCIPLED" and (
            node.inputs["Emission Strength"].is_linked
            or node.inputs["Emission Strength"].default_value > 0))
        for node in _reachable(output.inputs["Surface"])) for output in outputs)


def has_authored_emission(obj):
    """Recognize existing emission, including isolated copies of shared materials."""
    record = obj.get(TAG)
    for index, slot in enumerate(obj.material_slots):
        material = slot.material
        if material is None:
            continue
        if not material.get(OWNED) and _has_surface_emission(material):
            return True
        saved = record.get("slots", {}).get(str(index)) if record else None
        if saved and saved.get("prepared") == material:
            if _has_surface_emission(saved.get("original")):
                return True
    return False


def _restore_slot(slot, link, material):
    # Switching to DATA alone leaves a hidden object-material ID reference behind.
    # Clear that override first, then expose the unchanged underlying data slot.
    if slot.link == "OBJECT":
        slot.material = None
    slot.link = link
    if link == "OBJECT":
        slot.material = material


def _emission_ready(material):
    """Activate connected Principled emission, or add it beside a custom surface."""
    tree = material.node_tree
    if tree is None:
        material.use_nodes = True
        tree = material.node_tree
    outputs = [node for node in tree.nodes
               if node.type == "OUTPUT_MATERIAL" and node.is_active_output]
    if not outputs:
        outputs = [tree.nodes.new("ShaderNodeOutputMaterial")]
    for output in outputs:
        surface = output.inputs["Surface"]
        shaders = [node for node in _reachable(surface) if node.type == "BSDF_PRINCIPLED"]
        if shaders:
            for node in shaders:
                color, base = node.inputs["Emission Color"], node.inputs["Base Color"]
                if not color.is_linked:
                    if base.is_linked:
                        tree.links.new(base.links[0].from_socket, color)
                    else:
                        color.default_value = base.default_value
                node.inputs["Emission Strength"].default_value = STRENGTH
        else:
            emission = tree.nodes.new("ShaderNodeEmission")
            emission.label = "Noctune emission"
            emission.inputs["Color"].default_value = material.diffuse_color
            emission.inputs["Strength"].default_value = STRENGTH
            emission.location = (output.location.x - 420, output.location.y - 180)
            if surface.is_linked:
                original = surface.links[0].from_socket
                add = tree.nodes.new("ShaderNodeAddShader")
                add.label = "Original surface + Noctune"
                add.location = (output.location.x - 180, output.location.y)
                tree.links.new(original, add.inputs[0])
                tree.links.new(emission.outputs[0], add.inputs[1])
                tree.links.new(add.outputs[0], surface)
            else:
                tree.links.new(emission.outputs[0], surface)


def _new_material(obj):
    material = bpy.data.materials.new(f"Noctune {obj.name}")
    try:
        material.diffuse_color = obj.color
        node = next((node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
        if node:
            node.inputs["Base Color"].default_value = obj.color
        _emission_ready(material)
        material[OWNED] = VERSION
        return material
    except Exception:
        bpy.data.materials.remove(material)
        raise


def _copy_material(material):
    local = material.copy()
    try:
        local.name = f"Noctune {material.name}"
        local.use_fake_user = False
        if not _has_surface_emission(local):
            _emission_ready(local)
        local[OWNED] = VERSION
        return local
    except Exception:
        bpy.data.materials.remove(local)
        raise


def selected(objects):
    """Prepare editable objects atomically, returning the number of changed slots.

    Existing emissive materials are retained when all their object users belong to
    this selection. Non-emissive, linked or externally shared materials are copied.
    Object-linked material slots isolate shared meshes; slot-less shared/linked data
    is copied before adding a slot. Geometry is never modified.
    """
    objects = tuple(objects)
    chosen = set(objects)
    for obj in objects:
        if obj.type not in {"MESH", "CURVE", "FONT"} or not obj.is_editable:
            raise ValueError("Choose editable meshes, curves or text before preparing materials")
        if not obj.material_slots and (obj.data.users > 1 or not obj.data.is_editable):
            if obj.is_property_readonly("data"):
                raise ValueError(f"Make {obj.name}'s geometry local before preparing materials")
        if any((slot.link != "OBJECT" and slot.is_property_readonly("link"))
               or (slot.link == "OBJECT" and slot.is_property_readonly("material"))
               for slot in obj.material_slots):
            raise ValueError(f"Make {obj.name}'s material slots editable first")

    outside = {slot.material for obj in bpy.data.objects if obj not in chosen
               for slot in obj.material_slots if slot.material}
    plans = []
    for obj in objects:
        slots = list(obj.material_slots)
        for index, slot in enumerate(slots or (None,)):
            material = slot.material if slot else None
            if (material and material.is_editable and material not in outside
                    and _has_surface_emission(material)):
                continue
            plans.append((obj, index, slot.link if slot else "DATA", material))
    if not plans:
        return 0

    snapshots = {obj: (obj.data, [(slot.link, slot.material) for slot in obj.material_slots],
                       _plain(obj[TAG]) if TAG in obj else None) for obj in objects}
    created_materials, created_data = [], []
    replacements, blank = {}, {}
    try:
        # Prepare all node trees before changing any object assignment.
        ready = []
        for obj, index, link, material in plans:
            cache = replacements if material else blank
            key = material if material else obj
            if key not in cache:
                cache[key] = _copy_material(material) if material else _new_material(obj)
                created_materials.append(cache[key])
            ready.append((obj, index, link, material, cache[key]))
        for obj, index, link, material, prepared in ready:
            if not obj.material_slots:
                if obj.data.users > 1 or not obj.data.is_editable:
                    obj.data = obj.data.copy()
                    created_data.append(obj.data)
                # Keep the underlying data slot empty; only this object's override
                # receives the generated material, including after a later duplicate.
                obj.data.materials.append(None)
            record = _plain(obj[TAG]) if TAG in obj else {"version": VERSION, "slots": {}}
            old = record["slots"].get(str(index))
            if not old or material != old.get("prepared") or record.get("data") != obj.data:
                old = {"link": link}
                if material is not None:
                    old["original"] = material
            old["prepared"] = prepared
            record["slots"][str(index)] = old
            record["data"] = obj.data
            slot = obj.material_slots[index]
            if slot.link != "OBJECT":
                slot.link = "OBJECT"
            slot.material = prepared
            obj[TAG] = record
        return len(ready)
    except Exception:
        for obj, (data, slots, record) in snapshots.items():
            if obj.data != data:
                obj.data = data
            else:
                while len(obj.data.materials) > len(slots):
                    obj.data.materials.pop(index=len(obj.data.materials) - 1)
            for slot, (link, material) in zip(obj.material_slots, slots):
                _restore_slot(slot, link, material)
            if record is None:
                if TAG in obj:
                    del obj[TAG]
            else:
                obj[TAG] = record
        for data in created_data:
            if data.users == 0:
                (bpy.data.meshes if isinstance(data, bpy.types.Mesh) else bpy.data.curves).remove(data)
        for material in created_materials:
            if material.users == 0:
                bpy.data.materials.remove(material)
        raise


def restore(obj):
    """Restore still-owned slot overrides, retaining manual material/geometry edits."""
    record = obj.get(TAG)
    if not record:
        return 0
    candidates = set()
    count = 0
    if record.get("version") == VERSION:
        for name, slot_record in record.get("slots", {}).items():
            prepared = slot_record.get("prepared")
            if prepared:
                candidates.add(prepared)
            index = int(name)
            if record.get("data") != obj.data or index >= len(obj.material_slots):
                continue
            slot = obj.material_slots[index]
            if slot.material != prepared:
                continue
            _restore_slot(slot, slot_record["link"], slot_record.get("original"))
            count += 1
    del obj[TAG]
    for material in candidates:
        if material.users == 0 and material.get(OWNED):
            bpy.data.materials.remove(material)
    return count
