"""Item catalog and add-item support for Remnant 2 profile saves.

items_db.json comes from AndrewSav/lib.remnant2.analyzer (MIT license).
Inventory entries are cloned from an existing entry in the save, so the
structure always matches what the game itself writes.
"""
import copy
import json
import os
import re

import r2model
from r2model import N, Node

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "items_db.json")

# db Type -> (tab label, instance class, has quantity, has level)
ADDABLE = {
    "weapon": ("Weapon", None, False, True),  # class depends on melee/ranged
    "armor": ("Armor", "EquipmentInstanceData", False, False),
    "ring": ("Ring", "EquipmentInstanceData", False, False),
    "amulet": ("Amulet", "EquipmentInstanceData", False, False),
    "relic": ("Relic", "EquipmentInstanceData", False, False),
    "mod": ("Mod", "EquipmentModInstanceData", False, False),
    "mutator": ("Mutator", "EquipmentModInstanceData", False, False),
    "fragment": ("Relic Fragment", "EquipmentModInstanceData", False, True),
    "material": ("Boss / Crafting Material", "ItemInstanceData", False, False),
    "currency": ("Currency (Scrap, Iron, Lumenite…)", "ItemInstanceData", True, False),
    "consumable": ("Consumable", "EquipmentInstanceData", True, False),
    "concoction": ("Concoction", "EquipmentInstanceData", True, False),
}
NOT_ADDABLE_REASON = {
    "engram": "Engrams unlock archetypes through extra save data this editor doesn't create.",
    "archetype": "Archetypes are unlocked with engrams in-game.",
    "skill": "Skills come with their archetype.",
    "trait": "Traits are stored in the trait list, not the inventory.",
    "prism": "Prisms carry seeded segment data that can't be generated safely.",
    "specialmod": "Special mods are bound to their boss weapon and come with it.",
    "quest_item": "Quest items drive story progress and can break quests.",
}
SCRIPT = "/Script/GunfireRuntime."
QTY_MAX = 9_999_999


PREFIXES = ("RelicFragment_", "Material_", "Consumable_", "Weapon_", "Trait_", "Archetype_", "Perk_",
            "SkillTrait_", "MetaGem_", "Ring_", "Amulet_", "Armor_", "Mod_", "Relic_")


def pretty(name):
    """'RelicFragment_MeleeCritChance' -> 'Melee Crit Chance'."""
    for p in PREFIXES:
        name = name.removeprefix(p)
    name = name.replace("_", " ")
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


def load_catalog():
    with open(DB_PATH, encoding="utf-8-sig") as f:
        db = json.load(f)
    out = []
    for x in db:
        t, path = x.get("Type"), x.get("ProfileId")
        if t in ADDABLE and path:
            out.append({
                "id": x["Id"], "name": x.get("Name") or pretty(x["Id"]), "type": t, "path": path,
                "label": ADDABLE[t][0] + (f" ({x['Subtype']})" if x.get("Subtype") else ""),
                "subtype": x.get("Subtype"), "mod": x.get("WeaponOrMod"),
                "note": x.get("Note", ""), "world": x.get("World", ""),
            })
    return sorted(out, key=lambda e: (e["label"], e["name"]))


# Relic fragment tiers: each has a base level plus +1..+4 (5 stored levels),
# then Mythic as a single final level. Stored 2 = Cracked +1, 7 = Ordinary +1,
# 10 = Ordinary +4 and 31 = Mythic were confirmed in-game; the rest follows the
# community tier list.
FRAGMENT_TIERS = ["Cracked", "Ordinary", "Solid", "Polished", "Regal", "Flawless"]
FRAGMENT_MAX = len(FRAGMENT_TIERS) * 5 + 1  # 31 = Mythic


def fragment_tier(level):
    """Stored fragment level -> in-game name, e.g. 10 -> 'Ordinary +4', 31 -> 'Mythic'."""
    if level >= FRAGMENT_MAX:
        return "Mythic"
    if level < 1:
        return "?"
    tier, step = divmod(level - 1, 5)
    return FRAGMENT_TIERS[tier] + (f" +{step}" if step else "")


def level_range(entry):
    """Allowed upgrade level range, or None if the item has no level."""
    if entry["type"] == "weapon":
        return (0, 10) if entry["mod"] else (0, 20)
    if entry["type"] == "fragment":
        return (1, FRAGMENT_MAX)
    return None


def _character_data(model, slot):
    profile = model.data.objects[0].props
    ref = r2model.prop(profile, "Characters").value.items[slot]
    char = model.data.objects[ref]
    return r2model.prop(char.props, "CharacterData").value.value


def owned_paths(model, slot):
    cd = _character_data(model, slot)
    items = r2model.prop(_inventory(cd), "Items").value.items
    return {cd.objects[r2model.prop(e, "ItemBP").value].path for e in items}


def _inventory(cd):
    return next(c for c in cd.objects[0].comps if c.key == "Inventory").data


def _class_object(cd, path):
    for i, o in enumerate(cd.objects):
        if o.loaded and o.path == path:
            return i
    cd.objects.append(Node("Object", loaded=1, path=path, oname=None, outer=None, props=None,
                           extra=None, comps=None, index=len(cd.objects), actor=0))
    return len(cd.objects) - 1


def _int_prop(name, value):
    return Node("Prop", name=N(name), type=N("IntProperty"), size=4, index=0, guid=b"\x00", value=value)


def _byte_prop(name, value):
    return Node("Prop", name=N(name), type=N("ByteProperty"), size=1, index=0, guid=None,
                value=Node("Byte", enum=N("None"), guid=b"\x00", value=value))


def _find_template(cd, items, instance_class, need_prop=None):
    for e in items:
        inst = r2model.prop(e, "InstanceData").value
        if inst < 0:
            continue
        o = cd.objects[inst]
        if o.path == SCRIPT + instance_class and (need_prop is None or (o.props and r2model.prop(o.props, need_prop))):
            return o
    return None


def validate(entry, quantity, level, owned):
    """Returns an error message if this addition is unsafe, else None."""
    if entry["path"] in owned and not ADDABLE[entry["type"]][2]:
        return f"You already own {entry['name']}. Owning two copies of a unique item can corrupt your inventory."
    if entry["path"] in owned:
        return f"You already have {entry['name']}. Edit its quantity in its tab instead."
    if ADDABLE[entry["type"]][2] and not 1 <= quantity <= QTY_MAX:
        return f"Quantity must be between 1 and {QTY_MAX:,}."
    rng = level_range(entry)
    if rng and not rng[0] <= level <= rng[1]:
        return f"Level for {entry['name']} must be between {rng[0]} and {rng[1]}."
    return None


def add_item(model, slot, entry, quantity=1, level=0):
    """Adds one catalog entry to character `slot`'s inventory (mutates model)."""
    cd = _character_data(model, slot)
    inv = _inventory(cd)
    items = r2model.prop(inv, "Items").value.items
    err = validate(entry, quantity, level, owned_paths(model, slot))
    if err:
        raise ValueError(err)

    t = entry["type"]
    ranged = t == "weapon" and entry["subtype"] in ("Hand Gun", "Long Gun")
    cls = "RangedWeaponInstanceData" if ranged else "EquipmentInstanceData" if t == "weapon" else ADDABLE[t][1]

    props = []
    if ranged:
        props += [_int_prop("Ammo", 0), _int_prop("AmmoInClip", 10000)]
        if entry["mod"]:
            tmpl = _find_template(cd, items, cls, "Mods")
            if tmpl is None:
                raise ValueError("No boss weapon in your inventory to copy the built-in mod layout from.")
            mods = copy.deepcopy(r2model.prop(tmpl.props, "Mods"))
            slot0 = mods.value.value.props[0].value.items[0]
            r2model.prop(slot0, "Mod").value = _class_object(cd, _mod_path(entry["mod"]))
            r2model.prop(slot0, "ModItemID").value = -1
            r2model.prop(slot0, "ModLevel").value.value = 1
            r2model.prop(slot0, "ModSlot").value.value = 0
            r2model.prop(slot0, "HasSavedInstanceData").value = 0
            r2model.prop(slot0, "InstanceData").value = -1
            mods.value.value.props[0].value.items = [slot0]
            props.append(mods)
    if ADDABLE[t][2]:
        props.append(_int_prop("Quantity", quantity))
    if level_range(entry) and level > 0:
        props.append(_byte_prop("Level", level))

    tmpl_inst = _find_template(cd, items, cls)
    if tmpl_inst is None:
        tmpl_inst = next(o for o in cd.objects if not o.loaded and o.path.startswith(SCRIPT))
    inst = copy.deepcopy(tmpl_inst)
    used = [o.oname.number for o in cd.objects if o.oname and o.oname.number is not None]
    inst.path = SCRIPT + cls
    inst.oname = N(cls, min(used) - 1 if used else None)
    inst.index = len(cd.objects)
    inst.props = Node("Bag", props=props, none=N("None"))
    inst.extra = b"\x00" * 4
    cd.objects.append(inst)

    idgen = r2model.prop(inv, "IDGen")
    idgen.value += 1
    new = copy.deepcopy(items[0])
    for name, val in (("ID", idgen.value), ("ItemBP", _class_object(cd, entry["path"])), ("New", 1),
                      ("Favorited", 0), ("Hidden", 0), ("EquipmentSlotIndex", -1), ("InstanceData", inst.index)):
        r2model.prop(new, name).value = val
    items.append(new)


# Consumable-typed items that are really permanent tools, not stacks
NOT_STACKABLE = {"Consumable_LiquidEscape"}


def is_stackable(entry):
    return entry is not None and ADDABLE[entry["type"]][2] and entry["id"] not in NOT_STACKABLE


def set_quantity(model, slot, entry, quantity):
    """Sets Quantity on an owned stackable item, adding the property when the game
    left it out (it omits the default). Mutates model."""
    if not is_stackable(entry):
        raise ValueError(f"{entry['name']} doesn't stack.")
    if not 0 <= quantity <= QTY_MAX:
        raise ValueError(f"Quantity must be between 0 and {QTY_MAX:,}.")
    cd = _character_data(model, slot)
    for e in r2model.prop(_inventory(cd), "Items").value.items:
        if cd.objects[r2model.prop(e, "ItemBP").value].path != entry["path"]:
            continue
        inst = r2model.prop(e, "InstanceData").value
        if inst < 0:
            raise ValueError(f"{entry['name']} has no instance data to hold a quantity.")
        obj = cd.objects[inst]
        if obj.props is None:
            obj.props = Node("Bag", props=[], none=N("None"))
            obj.extra = obj.extra or b"\x00" * 4
        existing = r2model.prop(obj.props, "Quantity")
        if existing:
            existing.value = quantity
        else:
            obj.props.props.append(_int_prop("Quantity", quantity))
        return
    raise ValueError(f"You don't own {entry['name']}.")


# Relic charges: each Wallace upgrade is one hidden Item_DragonHeartUpgrade in
# the inventory. Confirmed in-game: 7 upgrade items = 10 charges (3 base).
DRAGON_HEART_UPGRADE = "/Game/Items/Common/Item_DragonHeartUpgrade.Item_DragonHeartUpgrade_C"
BASE_CHARGES = 3
MAX_CHARGES = 10


def _upgrade_entries(cd):
    items = r2model.prop(_inventory(cd), "Items").value.items
    return [e for e in items if cd.objects[r2model.prop(e, "ItemBP").value].path == DRAGON_HEART_UPGRADE]


def relic_charges(model, slot):
    return BASE_CHARGES + len(_upgrade_entries(_character_data(model, slot)))


def set_relic_charges(model, slot, charges):
    """Adds or removes Dragon Heart upgrade items so the relic has `charges`. Mutates model."""
    if not BASE_CHARGES <= charges <= MAX_CHARGES:
        raise ValueError(f"Relic charges must be between {BASE_CHARGES} and {MAX_CHARGES}.")
    cd = _character_data(model, slot)
    inv = _inventory(cd)
    items = r2model.prop(inv, "Items").value.items
    have = _upgrade_entries(cd)
    want = charges - BASE_CHARGES
    if want < len(have):
        remove = have[want:]  # keep the oldest `want`, drop the rest
        doomed = {r2model.prop(e, "InstanceData").value for e in remove} - {-1}
        for e in remove:
            items.remove(e)
        if not _upgrade_entries(cd):  # drop the class object too once nothing uses it
            doomed |= {i for i, o in enumerate(cd.objects) if o.loaded and o.path == DRAGON_HEART_UPGRADE}
        r2model.remove_objects(cd, doomed)
    for _ in range(want - len(have)):
        tmpl = have[0] if have else None
        inst_tmpl = cd.objects[r2model.prop(tmpl, "InstanceData").value] if tmpl else \
            next(o for o in cd.objects if not o.loaded and o.path == SCRIPT + "ItemInstanceData")
        inst = copy.deepcopy(inst_tmpl)
        used = [o.oname.number for o in cd.objects if o.oname and o.oname.number is not None]
        inst.oname = N("ItemInstanceData", min(used) - 1 if used else None)
        inst.index = len(cd.objects)
        inst.props = Node("Bag", props=[], none=N("None"))
        inst.extra = b"\x00" * 4
        cd.objects.append(inst)
        idgen = r2model.prop(inv, "IDGen")
        idgen.value += 1
        new = copy.deepcopy(items[0])
        for name, val in (("ID", idgen.value), ("ItemBP", _class_object(cd, DRAGON_HEART_UPGRADE)), ("New", 0),
                          ("Favorited", 0), ("Hidden", 0), ("EquipmentSlotIndex", -1), ("InstanceData", inst.index)):
            r2model.prop(new, name).value = val
        items.append(new)


_MOD_PATHS = None


def _mod_path(mod_id):
    global _MOD_PATHS
    if _MOD_PATHS is None:
        with open(DB_PATH, encoding="utf-8-sig") as f:
            _MOD_PATHS = {x["Id"]: x["ProfileId"] for x in json.load(f) if x.get("ProfileId")}
    if mod_id not in _MOD_PATHS:
        raise ValueError(f"Unknown built-in mod {mod_id}")
    return _MOD_PATHS[mod_id]
