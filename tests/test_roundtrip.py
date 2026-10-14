"""Checks that the editor can read and rewrite a real profile save safely.

  python tests/test_roundtrip.py path/to/profile.sav

Read-only: the save is never modified and nothing is written to disk.
Run it on an unedited save after every game patch (see MAINTAINING.md).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import r2edit  # noqa: E402
import r2items  # noqa: E402
import r2model  # noqa: E402
import r2save  # noqa: E402

failures = []


def check(name, fn):
    try:
        detail = fn()
        print(f"PASS  {name}" + (f"  ({detail})" if detail else ""))
    except Exception as e:  # report every check, don't stop at the first
        failures.append(name)
        print(f"FAIL  {name}: {type(e).__name__}: {e}")


def first_diff(a, b):
    i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return f"first difference at 0x{i:x}: {a[i:i + 16].hex()} vs {b[i:i + 16].hex()}"


def main(path):
    data = open(path, "rb").read()
    state = {}

    def unpack():
        state["full"] = r2save.unpack(data)  # raises on CRC mismatch
        return f"{len(state['full']):,} bytes, CRC ok"

    def repack():
        again = r2save.unpack(r2save.pack(state["full"]))
        assert again == state["full"], first_diff(state["full"], again)

    def model_roundtrip():
        out = r2model.dump(r2model.load(state["full"]))
        assert out == state["full"], first_diff(state["full"], out)
        return "byte-for-byte identical"

    def characters():
        slots, active = r2edit.characters(state["full"])
        assert slots, "no characters found"
        state["slot"] = active if any(s == active for s, _ in slots) else slots[0][0]
        return ", ".join(f"slot {s + 1} {a}" for s, a in slots)

    def collect():
        _, rows, fields = r2edit.collect(state["full"], state["slot"])
        assert "TraitPoints" in fields, "TraitPoints not found"
        return f"{len(rows)} rows, {len(fields)} editable values"

    def add_items():
        model = r2model.load(state["full"])
        owned = r2items.owned_paths(model, state["slot"])
        added, done = [], set()
        for entry in r2items.load_catalog():
            t = entry["type"]
            kind = (t, entry["subtype"], bool(entry.get("mod")))  # e.g. boss vs regular long gun
            if kind in done or entry["path"] in owned:
                continue
            done.add(kind)
            rng = r2items.level_range(entry)
            try:
                r2items.add_item(model, state["slot"], entry, 3 if r2items.ADDABLE[t][2] else 1, rng[0] if rng else 0)
            except ValueError as e:
                if "No boss weapon" in str(e):  # needs a boss weapon in the save to copy from
                    print(f"      skipped {entry['id']}: {e}")
                    continue
                raise
            added.append((entry["id"], t, entry["path"]))
        out = r2model.dump(model)
        assert r2model.dump(r2model.load(out)) == out, "rewritten save does not round-trip"
        packed = r2save.unpack(r2save.pack(out))
        now = r2items.owned_paths(r2model.load(packed), state["slot"])
        missing = [i for i, _, p in added if p not in now]
        assert not missing, f"added but not found: {missing}"
        return f"one of each type added in memory: {', '.join(i for i, _, _ in added)}"

    check("unpack + CRC", unpack)
    if "full" not in state:
        return
    check("repack", repack)
    check("model round trip", model_roundtrip)
    check("find characters", characters)
    if "slot" in state:
        check("collect character values", collect)
        check("add items (in memory)", add_items)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
    print("\nAll checks passed." if not failures else f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1 if failures else 0)
