# Maintaining the Remnant 2 Character Editor

This document is for whoever works on the editor next, human or AI. It explains
the save format, how the code is split up, why it is built this way, the bugs we
already hit, and what to do when the game updates.

The format knowledge comes from
[lib.remnant2.saves](https://github.com/AndrewSav/lib.remnant2.saves) (C#, MIT).
When in doubt, that library is the reference; this code is a Python port of the
parts the editor needs.

## Contents

- [Architecture](#architecture)
- [Save file format](#save-file-format)
- [Profile layout](#profile-layout)
- [How edits are written](#how-edits-are-written)
- [Adding items](#adding-items)
- [Safety gates](#safety-gates)
- [Backups](#backups)
- [Hurdles we hit](#hurdles-we-hit)
- [Updating items_db.json](#updating-items_dbjson)
- [After a game patch](#after-a-game-patch)
- [Open questions](#open-questions)

## Architecture

```
r2gui.py      Tkinter GUI: tabs, gates, add-item and restore dialogs, backups
  ├─ r2edit.py    collect(): character fields → {target: Val(offset, fmt)}
  │    └─ r2parse.py  read-only parser; every number remembers its byte offset
  ├─ r2items.py   catalog (items_db.json), per-type recipes, add_item(), validate()
  │    └─ r2model.py  lossless read/write model: load() / dump()
  └─ r2save.py    compression + CRC: unpack() / pack()
```

Two parsers exist on purpose:

- **`r2parse.py`** is small and read-only. It records the absolute offset of
  every number, so the GUI can change a value by overwriting those bytes. Nothing
  else moves, so number edits carry almost no risk.
- **`r2model.py`** keeps everything needed to write the file back. It is only
  used when the structure changes (adding items). It must rewrite an unmodified
  save **byte-for-byte identically**, and `tests/test_roundtrip.py` checks that.

The "full" (unpacked) buffer is the shared format between them. See below.

## Save file format

### Container (`r2save.py`)

Files on disk (`profile.sav`, `save_N.sav`) look like this:

```
uint32  crc32
int32   decompressed size
int32   version            (9 so far)
chunks, until end of file:
  uint64  tag              0x222222229E2A83C1
  uint64  max chunk size   0x20000
  uint8   compressor       3 = zlib
  uint64  compressed size
  uint64  uncompressed size
  uint64  compressed size  (repeated)
  uint64  uncompressed size (repeated)
  bytes   zlib data
```

Concatenating the decompressed chunks gives a stream whose first 4 bytes are
`size - 12`. `r2save.unpack()` turns it into the **full** form:

```
full = crc32, size, version (12 bytes)  +  stream[4:]
len(full) == size
crc32 == zlib.crc32(full[4:])       # covers everything after the CRC itself
```

`r2save.pack()` reverses this. It recomputes size and CRC, writes the stream as
`int32(size - 12) + full[12:]`, and splits it into 0x20000-byte zlib chunks.
The game accepts any zlib compression level, so recompressed files need not
match the original compressed bytes. Only the unpacked content has to be right.

### Inside the full buffer

```
FileHeader   uint32 crc, int32 size, int32 version, int32 build   (16 bytes)
SaveData
```

A **SaveData** block:

```
[PackageVersion]    uint32 UE4, uint32 UE5      (8 bytes, top level and character blob)
[SaveGameClassPath] FString path, FString name  (top level only)
OffsetInfo          int64 namesOffset, uint32 version, int64 objectsOffset
object data         one record per object, in table order
objects table       int32 count, then per object:
                      uint8 wasLoaded
                      FString path   (skipped for object 0 when a class path exists)
                      if !wasLoaded: FName name, uint32 outerId
names table         int32 count, FString × count
[extra bytes]       nested blobs can have trailing bytes; keep them
```

**Offsets are relative to the start of the buffer that contains the SaveData.**
At the top level that buffer begins with the 16-byte FileHeader, so the offsets
include it. In a nested blob they start at 0 inside the blob. Getting this wrong
was our first round-trip bug (see [Hurdles](#hurdles-we-hit)).

Per-object data record:

```
int32   objectIndex
uint32  propertiesLength      0 = no properties
        PropertyBag + extra bytes (instance objects carry 4 zero bytes)
uint8   isActor
if isActor: uint32 count, then per component:
        FString key, int32 length,
        Variables (for GlobalVariables / Variables / Variable / PersistenceKeys*)
        or PropertyBag (everything else), then any extra bytes
```

### Primitive encodings

- **FString**: `int32 n`. 0 means null, `n > 0` is ASCII with a NUL terminator,
  `n < 0` is UTF-16 with `-n` characters including the terminator.
- **FName**: `uint16 index` into the names table. If bit 15 is set, an `int32`
  number follows (Unreal's `Name_123` suffix).
- **Property**: `FName name`, `FName type`, `uint32 size`, `uint32 index`, then
  a type-specific body. A bag ends with a property named `None`.
- **Property GUID**: `uint8 has` plus 16 bytes if `has != 0`. It appears on
  "tagged" (non-raw) properties.

`size` covers the value only. It excludes the type-specific header that comes
before the value. The writer recomputes it as
`bytes written − overhead`:

| Type | Overhead |
| --- | --- |
| Int/Float/Str/Name/Object/Text… | GUID block (1 or 17) |
| BoolProperty | 1 (value byte) + GUID block, so size is 0 |
| ByteProperty | enum FName (2 or 6) + GUID block |
| EnumProperty | enum type FName + GUID block |
| StructProperty | struct type FName + 16 (struct GUID) + GUID block |
| ArrayProperty | element type FName + GUID block |
| MapProperty | key FName + value FName + GUID block |

Array-of-struct arrays carry a second inner header (name index, type index, inner
size, inner index, element type FName, 16-byte GUID, GUID block). Its inner size
covers the struct items only.

Inside arrays and maps, values are **raw**: no GUID block, ByteProperty is a
bare byte, and StructProperty is a bare 16-byte GUID.

## Profile layout

```
objects[0]  BP_RemnantSaveGameProfile_C
  Characters            array of object refs, -1 = empty slot (5 slots)
  ActiveCharacterIndex
objects[n]  /Script/Remnant.SavedCharacter       (one per used slot)
  TraitRank, PowerLevel, ItemLevel, LastSavedTraitPoints, Archetype, ...
  CharacterData         StructProperty of type PersistenceBlob:
                          int32 length + nested SaveData
                          (has PackageVersion, no class path, own names/objects)
```

Inside the character blob, `objects[0]` is `Character_Master_Player_C` and it is
an actor. Its components hold the character:

| Component | Contents |
| --- | --- |
| `Inventory` | `Items` (array of `InventoryItem` structs), `IDGen` (last ID handed out) |
| `Traits` | `TraitPoints`, `Traits` array (`TraitBP`, `Level`; archetype entries keep XP in `InstanceData`) |
| `Progression` | `Experience` |
| `Challenge`, `RadialShortcuts`, … | Not edited |

An `InventoryItem` struct holds `ID`, `ItemBP` (an object ref to the item class),
`New`, `Favorited`, `Hidden`, `EquipmentSlotIndex` (-1 = not equipped) and
`InstanceData` (an object ref, or -1).

Objects inside the blob come in two kinds:

- **Class references**: `wasLoaded = 1`, just a path such as
  `/Game/.../Ring_BlackSpinel.Ring_BlackSpinel_C`. There is one per class, and
  inventory entries and weapon mods share them.
- **Instance data**: `wasLoaded = 0`, path `/Script/GunfireRuntime.<Class>`,
  name FName `<Class>` with a unique number, outer 0, properties, then 4 zero
  extra bytes. The numbers count **down** from about 2³¹, so new ones use
  `min(existing) - 1`.

**Default values are left out.** An instance with `Level` 0 has no `Level`
property, and a single boss material has no `Quantity`. That is why some items
show "nothing to edit": there are no bytes to patch.

## How edits are written

**Number edits:** `r2parse` records `Val(offset, fmt)` for each number.
`r2edit.collect()` maps targets such as `Material_Scraps.Quantity` or
`TraitPoints` to those Vals. The GUI holds pending edits in a dict and, on
save, packs the new numbers into those offsets. Sizes never change.

**Structural edits** (adding items) follow `r2model.load()` → mutate →
`r2model.dump()`. Pending number edits are folded in first
(`App.commit_edits`), because a structural change moves every offset after it.

**Save pipeline (GUI):**

1. Refuse if a `Remnant2*` process is running (`tasklist`).
2. Confirm with the user.
3. `pack()` the working buffer.
4. Verify: unpack it again, compare it with the working buffer, round-trip it
   through `r2model`, and re-run `r2edit.collect()`. Any failure means nothing
   is written.
5. Back up the whole save folder. A failure means nothing is written.
6. Write `profile.sav`, then reload it from disk.

## Adding items

`r2items.add_item()` clones structures the game already wrote rather than
building them from nothing:

1. Find or create the class-reference object for the item path.
2. Build the instance-data object for its type (table below). The 4 extra bytes
   and object layout are deep-copied from an existing instance of the same class
   in the save.
3. Deep-copy `Items[0]` as the inventory entry, then set `ID = ++IDGen`,
   `ItemBP`, `New = 1`, `Favorited/Hidden = 0`, `EquipmentSlotIndex = -1` and
   `InstanceData`.

The recipe for each type comes from what the game writes in real saves:

| `items_db.json` type | Instance class | Properties written |
| --- | --- | --- |
| weapon, ranged (Hand/Long Gun) | `RangedWeaponInstanceData` | `Ammo` 0, `AmmoInClip` 10000, `Level` if > 0; boss weapons also get `Mods` (see below) |
| weapon, melee | `EquipmentInstanceData` | `Level` (ByteProperty) if > 0 |
| armor, ring, amulet, relic | `EquipmentInstanceData` | none |
| mod, mutator | `EquipmentModInstanceData` | none |
| fragment | `EquipmentModInstanceData` | `Level` (ByteProperty) |
| material (boss/crafting) | `ItemInstanceData` | none (single item) |
| currency (scrap, iron, lumenite…) | `ItemInstanceData` | `Quantity` |
| consumable, concoction | `EquipmentInstanceData` | `Quantity` |

`AmmoInClip = 10000` matches what the game writes for weapons it hasn't loaded
yet.

**Boss weapons** (catalog entry has `WeaponOrMod`) copy the `Mods` struct from a
boss weapon already in the save. Slot 0 points at the built-in mod's class
object, with `ModItemID = -1`, `ModLevel = 1` and no instance data. If the save
has no boss weapon to copy from, adding one fails with an error rather than
guessing.

**Not addable** (`NOT_ADDABLE_REASON` in `r2items.py`):
- engrams and archetypes: they also need trait-list entries
- skills and traits: they live in the trait list
- prisms: they carry seeded segment data
- special mods: they belong to their weapon
- quest items: they drive quest state

## Safety gates

| Where | Rule |
| --- | --- |
| `App.rule()` | Ranges per field; `None` means read-only |
| `r2items.level_range()` | Weapon 0–20, boss weapon 0–10, fragment 1–10 |
| `r2items.validate()` | No duplicate items; quantity 1–9,999,999; level in range |
| `game_running()` | No saving or restoring while `Remnant2*` is in `tasklist` |
| `HIDDEN_FIELDS` | `Ammo`/`AmmoInClip` aren't shown (meaningless, often negative) |

The caps are the game's normal maximums. If a patch raises a cap, update these.

## Backups

`make_backup(save_dir)` copies every `.sav` file into
`~/Documents/Remnant 2 Editor Backups/<save folder name>/<YYYY-MM-DD HH-MM-SS>/`
and deletes all but the newest `KEEP_BACKUPS` (5). If a folder name is already
taken, a ` (2)` suffix is added. The whole folder is backed up because
`profile.sav` and the `save_N.sav` world files belong together.

Restore reads the chosen backup into memory **before** making the safety backup.
Otherwise pruning could delete the very backup being restored when it is the
oldest one.

## Hurdles we hit

These are worth knowing so they aren't reintroduced:

1. **Which bytes the CRC covers.** Guessing ranges and CRC variants (CRC32C,
   non-inverted) all failed. The answer, from `Archive.cs`, is standard CRC32
   over the reconstructed full buffer from byte 4, with the header fields
   overwritten into the first 12 bytes of the decompressed stream.
2. **Off by 16 in the top-level offsets.** The first round-trip test failed at
   byte `0x72`. Top-level OffsetInfo counts the 16-byte FileHeader; nested blobs
   don't. `w_savedata(sd, base)` pads by `base` and strips it again afterwards.
3. **Stale header size after adding items.** `dump()` must write the new length
   into the header. Otherwise the size field disagrees with the content until
   `pack()` fixes it.
4. **Verification compared old CRC bytes.** An early check compared the whole
   buffer, including the old CRC that `pack()` replaces, so it always failed and
   every save was refused. Compare from byte 4 (or 8) onward.
5. **Backup name collisions.** Two saves within the same second reused one
   backup name and overwrote the original. Backups now get unique names.
6. **Missing display names.** About half of `items_db.json` entries (666 of
   1,183 with a path) have no `Name`. `r2items.pretty()` builds one from the ID,
   for example `RelicFragment_MeleeCritChance` becomes "Melee Crit Chance".
7. **Currencies aren't "material".** Scrap, iron, lumenite, relic dust and so on
   have type `currency` in the database. Boss materials have type `material` and
   are single items without `Quantity`.
8. **Default values are left out.** See [Profile layout](#profile-layout). Edits
   can only patch values that exist; adding a value means a structural rewrite.
9. **Long paths on Windows.** Running a Windows Python from a very long working
   directory path failed with "path longer than allowed". Use a short folder.

## Updating items_db.json

The catalog is `db.json` from
[lib.remnant2.analyzer](https://github.com/AndrewSav/lib.remnant2.analyzer).
It's a JSON list of entries with `Id`, `ProfileId` (the item's class path),
`Type`, and optional `Name`, `Subtype`, `World`, `Note`, `WeaponOrMod`. The
editor only uses entries that have a `ProfileId` and an addable `Type` (see
`ADDABLE` in `r2items.py`).

To update:

```bash
python update_db.py
```

The script downloads the latest file and checks that it parses and still has
addable items. It prints what was added and removed, then replaces
`items_db.json`; the previous copy is kept as `items_db.json.old`.
`python update_db.py --check` only reports differences.

After updating:

- Look for **new `Type` values** in the output. A new type isn't addable until
  it is in `ADDABLE`, with a recipe confirmed against a real save (find an owned
  item of that type and inspect its instance object).
- If the upstream project stops being maintained, item paths can be read from a
  save that owns the item (the class-reference objects), or from the game files
  with an Unreal asset tool.

## After a game patch

1. Back up your saves by hand.
2. Run the round-trip test on a fresh, unedited `profile.sav`:

   ```bash
   python tests/test_roundtrip.py "C:\Users\<you>\Saved Games\Remnant2\Steam\<id>\profile.sav"
   ```

   - **Container failures** (CRC, chunk tag, version ≠ 9): compare against
     `Compression/Archive.cs` upstream.
   - **"unknown property type"**: a new property type needs reading and writing
     in `r2model.py` and reading in `r2parse.py`. Port it from
     `Model/Properties/` upstream.
   - **Model round-trip not identical**: the test prints the first differing
     offset. Dump the area around it and compare with the upstream reader.
3. Open the save in the GUI and add one item of each type to a copy, then load
   it in-game.
4. Check whether level caps changed.
5. Refresh `items_db.json` if new items were added.

## Open questions

- In-game behaviour of edited and added items hasn't been systematically
  confirmed. The file-level checks pass (CRC, round trip, re-parse), but only
  loading in the game proves an edit is accepted.
- The `ModLevel = 1` for a newly added boss weapon's built-in mod is copied from
  an observed save (Nebula at weapon level 0), not confirmed for other levels.
- Mutator levels: owned mutators in the reference save had no `Level`
  property, so mutators are added at base level.
- Consumables that the game stores without `Quantity` (Bandage, Antidote, …)
  probably mean quantity 1. The editor always writes an explicit `Quantity`.
- `save_N.sav` files use a `PersistenceContainer` layout. `r2parse.py` has
  untested reading code for it, and `r2model.py` only handles profiles. World
  saves are backed up but never edited.
