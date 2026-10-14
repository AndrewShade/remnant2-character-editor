"""List and edit Remnant 2 character values in profile.sav.

  python r2edit.py list profile.sav
  python r2edit.py set  profile.sav out.sav "Material_Scraps.Quantity=99999" "Trait_Vigor.Level=10" "TraitPoints=50"

Targets are "<ItemOrTraitName>.<Field>" (as shown by `list`) or a character-level
field: TraitPoints, Experience, TraitRank, PowerLevel, ItemLevel, LastSavedTraitPoints.
Only values already present in the save can be changed (sizes never change).
"""
import struct
import sys

import r2parse
import r2save
from r2parse import Val


def short(path):
    return path.rsplit(".", 1)[-1].removesuffix("_C") if path else None


def characters(full):
    """[(slot, archetype name)] for each used character slot, plus the active slot."""
    tree = r2parse.parse(full)
    profile = tree["objects"][0]["props"]
    out = []
    for slot, ref in enumerate(profile["Characters"]):
        if ref["_idx"].value >= 0:
            props = tree["objects"][ref["_idx"].value]["props"]
            arch = short(props.get("Archetype")) or "Unknown"
            out.append((slot, arch.removeprefix("Archetype_").removesuffix("_UI")))
    return out, profile["ActiveCharacterIndex"].value


def collect(full, char_index=None):
    tree = r2parse.parse(full)
    profile = tree["objects"][0]["props"]
    if char_index is None:
        char_index = profile["ActiveCharacterIndex"].value
    char_obj = tree["objects"][profile["Characters"][char_index]["_idx"].value]["props"]
    data = char_obj["CharacterData"]["objects"]
    comps = data[0]["components"]

    fields = {}  # target -> Val
    rows = []    # (section, name, {field: Val})
    for k in ("TraitRank", "PowerLevel", "ItemLevel", "LastSavedTraitPoints"):
        if isinstance(char_obj.get(k), Val):
            fields[k] = char_obj[k]
    fields["TraitPoints"] = comps["Traits"]["TraitPoints"]
    fields["Experience"] = comps["Progression"]["Experience"]

    for item in comps["Inventory"]["Items"]:
        name = short(item["ItemBP"]["_obj"])
        inst = item.get("InstanceData")
        props = {}
        if inst and inst["_idx"].value >= 0:
            p = data[inst["_idx"].value].get("props", {})
            props = {k: v for k, v in p.items() if isinstance(v, Val) and k in ("Quantity", "Level", "Ammo", "AmmoInClip")}
        rows.append(("Item", name, props))
    for t in comps["Traits"]["Traits"]:
        name = short(t["TraitBP"]["_obj"])
        props = {"Level": t["Level"]}
        if name.startswith("Archetype_"):
            props["Experience"] = t["InstanceData"]
        rows.append(("Trait", name, props))

    for _, name, props in rows:
        for f, v in props.items():
            fields.setdefault(f"{name}.{f}", v)
    return char_obj, rows, fields


def cmd_list(full):
    _, rows, fields = collect(full)
    print("== Character")
    for k in ("TraitPoints", "LastSavedTraitPoints", "TraitRank", "Experience", "PowerLevel", "ItemLevel"):
        if k in fields:
            print(f"  {k:24} {fields[k].value}")
    for section in ("Trait", "Item"):
        print(f"== {section}s")
        for s, name, props in rows:
            if s == section:
                vals = "  ".join(f"{k}={v.value}" for k, v in props.items())
                print(f"  {name:55} {vals}")


def cmd_set(full, assignments, char_index=None):
    _, _, fields = collect(full, char_index)
    buf = bytearray(full)
    for a in assignments:
        target, value = a.split("=", 1)
        if target not in fields:
            raise SystemExit(f"unknown or absent field: {target}")
        v = fields[target]
        new = float(value) if v.fmt in "fd" else int(value)
        struct.pack_into("<" + v.fmt, buf, v.off, new)
        print(f"  {target}: {v.value} -> {new}")
    return bytes(buf)


if __name__ == "__main__":
    cmd, src = sys.argv[1], sys.argv[2]
    full = r2save.unpack(open(src, "rb").read())
    if cmd == "list":
        cmd_list(full)
    elif cmd == "set":
        out = cmd_set(full, sys.argv[4:])
        packed = r2save.pack(out)
        assert r2save.unpack(packed)[4:] == out[4:]  # re-checks crc and content
        open(sys.argv[3], "wb").write(packed)
        print("wrote", sys.argv[3])
