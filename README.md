# Remnant 2 Character Editor

A desktop editor for Remnant 2 **single-player** character saves. Change trait
points, trait and archetype levels, material and consumable quantities, and
weapon and relic fragment levels. You can also add new items from a catalog of
891 rings, amulets, armor pieces, weapons, mods, mutators, relics, fragments,
materials and consumables.

It is written in plain Python with the built-in Tkinter GUI, so there is nothing
to install beyond Python itself.

> **Unofficial and use at your own risk.** This tool is not affiliated with
> Gunfire Games. It always backs up your saves before writing, but editing saves
> can still break things. It has only been tested on single-player Steam saves.
> Don't use it for co-op or online play.

## Requirements

- Windows with the Steam version of Remnant 2
- [Python 3.10 or newer](https://www.python.org/downloads/) (Tkinter is included
  with the standard Windows installer)

## Quick start

1. **Close Remnant 2 completely.**
2. In Steam, open Remnant 2 → **Properties → General** and turn **Steam Cloud**
   off. Otherwise the cloud copy can overwrite your edited save.
3. Download or clone this repository, open a terminal in its folder and run:

   ```bash
   python r2gui.py
   ```

4. Click **Open profile.sav…**. The file picker starts in
   `C:\Users\<you>\Saved Games\Remnant2\Steam\`. Open the numbered folder inside
   and pick `profile.sav`.
5. Edit, then click **Save**. Launch the game and check your character.
6. Once you are happy with the result, you can turn Steam Cloud back on.

## Using the editor

### Editing values

Values are grouped into tabs: **Character**, **Traits**, **Materials**,
**Consumables**, **Weapons & Gear** and **Fragments & Prisms**. **Search**
filters across all of them.

Double-click a value (or select it and press Enter) to edit it. Changed values
turn orange until you save. **Revert changes** discards everything that hasn't
been saved yet.

### Adding items

Click **Add items…**, search for an item or pick a category, set the quantity or
level if the item has one, and click **Add to inventory**. For many items, the
window also shows where the item normally drops. Added items show in green until
you save and appear in-game marked as new.

### Safety gates

The editor refuses changes that are known to be risky:

| What | Rule |
| --- | --- |
| Trait and archetype levels | 0–10 |
| Regular weapon levels | 0–20 |
| Boss weapon levels (weapons with a built-in mod) | 0–10 |
| Relic fragment levels | 1–10 |
| Item quantities | 0–9,999,999 |
| Trait points | 0–9,999 |
| Trait Rank, Power Level, Item Level | Read-only (the game recalculates them) |
| Perks, archetype skills, prism levels | Read-only (tied to other data) |
| Adding a second copy of an item you own | Blocked (edit the quantity instead) |
| Adding engrams, archetypes, skills, traits, prisms, special mods, quest items | Blocked (they need data the editor doesn't create) |
| Saving while the game is running | Blocked |

Each tab also has a note at the top explaining what is risky there.

### Backups and restoring

Every time you click **Save**, the editor first copies every `.sav` file in your
save folder to:

```
Documents\Remnant 2 Editor Backups\<save folder id>\<date time>\
```

It keeps the **5 most recent** backups. If the backup fails, nothing is written.

To roll back, click **Restore backup…**, pick a backup and confirm. Your current
files are backed up first, so a restore can be undone too. **Open backups
folder** shows the backups in Explorer.

To restore by hand, close the game and copy the files from a backup folder back
into your save folder.

## Command-line use

The GUI is a front end over a few small scripts that you can also run
directly:

```bash
python r2edit.py list profile.sav
```

```bash
python r2edit.py set profile.sav profile_edited.sav "Material_Scraps.Quantity=99999" "TraitPoints=30"
```

```bash
python r2save.py unpack profile.sav profile.raw
```

`r2edit.py set` writes a new file and never changes the input. It only changes
values already stored in the save. The CLI has no safety gates or backups, so
use the GUI unless you know what you are doing.

## Limitations

- Only values already stored in the save can be edited. The game leaves out
  default values, such as level 0, so gear that has never been upgraded shows
  "nothing to edit". Upgrade it once in-game, or add it fresh with a level.
  Stackable consumables are the exception: when the game hasn't stored a count,
  the editor shows a quantity of 1 marked "not stored yet", and you can set it.
- New ranged weapons start with no reserve ammo. It refills at a checkpoint.
- Mutators are added at their base level.
- `save_0.sav` and the other world saves (campaign and adventure progress) are
  backed up but never edited.
- Not yet confirmed in-game: the built-in mod level that the editor gives newly
  added boss weapons.

## Files

| File | Purpose |
| --- | --- |
| `r2gui.py` | The GUI |
| `r2edit.py` | Lists and patches number values in place |
| `r2items.py` | Item catalog, item recipes and add-item rules |
| `r2model.py` | Full lossless read/write model of the save |
| `r2parse.py` | Read-only parser that records where each value is stored |
| `r2save.py` | Save compression and checksum |
| `items_db.json` | Item catalog (from lib.remnant2.analyzer) |
| `update_db.py` | Downloads the latest `items_db.json` |
| `tests/test_roundtrip.py` | Checks that the editor can read and rewrite a save safely |

## Updating the item list

New DLC and patches add items. To fetch the latest catalog:

```bash
python update_db.py
```

See [MAINTAINING.md](MAINTAINING.md) for details, and for what to check after a
game update.

## Credits

- The save format code is a Python port of
  [lib.remnant2.saves](https://github.com/AndrewSav/lib.remnant2.saves) by
  Andrew Savinykh (MIT).
- `items_db.json` comes from
  [lib.remnant2.analyzer](https://github.com/AndrewSav/lib.remnant2.analyzer)
  by Andrew Savinykh (MIT).

## License

MIT. See [LICENSE](LICENSE), which also includes the third-party notices.
Remnant 2 is a trademark of Gunfire Games.
