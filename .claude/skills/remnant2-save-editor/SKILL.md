---
name: remnant2-save-editor
description: Maintain the Remnant 2 character editor in this repo. Use for updating the item catalog (items_db.json), fixing the editor after a Remnant 2 patch breaks loading or saving, making a new item type addable, changing level caps or safety gates, or any change to the save format code (r2save.py, r2parse.py, r2model.py, r2items.py, r2gui.py).
---

# Remnant 2 save editor maintenance

Read `MAINTAINING.md` before changing code. It documents the save format, the
add-item recipes, the safety gates, and bugs that were already fixed and must
not come back. Upstream reference for the format:
https://github.com/AndrewSav/lib.remnant2.saves (C#, MIT).

## Ground rules

- Never test against the user's live save folder. Copy saves into an ignored
  folder (`work/` is gitignored) and point tools at the copy.
- Never commit `.sav` files or anything from `work/` or `ref/`. They identify
  the user's account.
- `r2model.dump(r2model.load(x)) == x` must stay true for an unedited save. Run
  `python tests/test_roundtrip.py <copy of profile.sav>` after any change to
  r2save/r2parse/r2model/r2items.
- Compare from byte 4 (or 8) when checking packed output. The CRC and size
  fields are recomputed by design.
- Don't loosen a safety gate without evidence from a real save or the game.
  Gates live in `App.rule()`, `r2items.level_range()` and `r2items.validate()`.
- Claims about in-game behaviour need the user to load the save in-game. File
  checks alone don't prove the game accepts an edit. Say which one was verified.

## Common jobs

### Update the item list

1. `python update_db.py --check` and read the added, removed and
   unhandled-type report.
2. `python update_db.py` to replace `items_db.json` (keeps `items_db.json.old`).
3. Run the round-trip test. Its add-items check uses the new catalog.
4. For a new type the editor doesn't handle, decide between "make it addable"
   (below), `NOT_ADDABLE_REASON` (unsafe, with a reason shown to the user) and
   `KNOWN_SKIPPED` in `update_db.py` (irrelevant).

### A game patch broke loading or saving

1. Run `tests/test_roundtrip.py` on a fresh, unedited save copy. Each check
   reports separately.
2. Match the failure to the "After a game patch" table in `MAINTAINING.md`:
   container/CRC → `r2save.py`; unknown property type → both parsers; round trip
   not identical → the first-difference offset printed by the test.
3. Port the fix from the upstream C# library when it has one. Keep `r2parse.py`
   (read-only, records offsets) and `r2model.py` (lossless read/write) in sync.

### Make a new item type addable

1. Find a save that owns an item of that type. Dump its inventory entry and
   instance object with `r2model.load()` (see the `InventoryItem` layout in
   `MAINTAINING.md`).
2. Add the type to `ADDABLE` in `r2items.py` with the observed instance class,
   quantity flag and level flag. Extend `level_range()` if it has a level.
3. Copy only properties the game writes for that type, and leave out defaults.
4. Run the round-trip test, then ask the user to add one in-game on a backed-up
   save and confirm before treating it as supported.

### Change a level cap or gate

Edit `App.rule()` in `r2gui.py` and `level_range()` in `r2items.py` together.
Update the tables in `README.md` and `MAINTAINING.md`, and the note at the top
of the matching tab in `TABS`.
