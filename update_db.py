"""Download the latest item catalog (items_db.json) from lib.remnant2.analyzer.

  python update_db.py           download, validate, replace (keeps items_db.json.old)
  python update_db.py --check   download and report differences only
"""
import json
import os
import shutil
import sys
import urllib.request

import r2items

URL = "https://raw.githubusercontent.com/AndrewSav/lib.remnant2.analyzer/HEAD/db.json"
HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "items_db.json")
# Types deliberately not addable: account awards, prism fusions, quest-like
# dreams, the "nude" placeholder armor and the Alloy Extractor.
KNOWN_SKIPPED = {"award", "fusion", "dream", "armorspecial", "special"}


def addable_ids(db):
    return {x["Id"] for x in db if x.get("Type") in r2items.ADDABLE and x.get("ProfileId")}


def main():
    check_only = "--check" in sys.argv
    print(f"Downloading {URL}")
    with urllib.request.urlopen(URL, timeout=30) as resp:
        raw = resp.read()
    new = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(new, list) or not all(isinstance(x, dict) and "Id" in x for x in new):
        sys.exit("Downloaded file isn't a list of items with an Id; not replacing.")
    new_ids = addable_ids(new)
    if len(new_ids) < 100:
        sys.exit(f"Only {len(new_ids)} addable items found; that looks wrong, not replacing.")

    old = []
    if os.path.exists(TARGET):
        with open(TARGET, encoding="utf-8-sig") as f:
            old = json.load(f)
    old_ids = addable_ids(old)
    added, removed = sorted(new_ids - old_ids), sorted(old_ids - new_ids)
    known_types = set(r2items.ADDABLE) | set(r2items.NOT_ADDABLE_REASON) | KNOWN_SKIPPED
    new_types = sorted({x.get("Type") for x in new if x.get("ProfileId")} - known_types - {None})

    print(f"Addable items: {len(old_ids)} -> {len(new_ids)}")
    if added:
        print(f"Added ({len(added)}): " + ", ".join(added[:40]) + (" ..." if len(added) > 40 else ""))
    if removed:
        print(f"Removed ({len(removed)}): " + ", ".join(removed[:40]) + (" ..." if len(removed) > 40 else ""))
    if new_types:
        print("Types the editor doesn't handle (not addable until added to ADDABLE in r2items.py): "
              + ", ".join(new_types))

    if check_only:
        print("--check given; items_db.json left unchanged.")
        return
    if os.path.exists(TARGET):
        shutil.copy2(TARGET, TARGET + ".old")
    with open(TARGET, "wb") as f:
        f.write(raw)
    r2items.load_catalog()  # make sure the editor can load it
    print("items_db.json updated" + (" (previous copy: items_db.json.old)" if old else "") + ".")


if __name__ == "__main__":
    main()
