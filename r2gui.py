"""Remnant 2 character editor GUI.  Run:  python r2gui.py

Double-click a value to edit it, or use "Add items…" to add new items.
Every save first copies your whole save folder to
Documents/Remnant 2 Editor Backups (the 5 most recent are kept).
"""
import csv
import io
import os
import shutil
import struct
import subprocess
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import r2edit
import r2items
import r2model
import r2save

SAVE_ROOT = os.path.join(os.path.expanduser("~"), "Saved Games", "Remnant2", "Steam")
BACKUP_ROOT = os.path.join(os.path.expanduser("~"), "Documents", "Remnant 2 Editor Backups")
KEEP_BACKUPS = 5
QTY_MAX = r2items.QTY_MAX
I32_MAX = 2**31 - 1

TABS = [
    ("Character", None,
     "Trait points and experience. Trait Rank, Power Level and Item Level are recalculated by the game, "
     "so they are read-only. If you raise trait levels, add the same number of points to both Trait Points fields."),
    ("Traits", ("Trait_", "Archetype_", "Perk_", "SkillTrait_"),
     "Trait and archetype levels are limited to 0–10, the game's maximum. Perks and archetype skills unlock "
     "with archetype level, so they are read-only here. Raise the archetype level instead."),
    ("Materials", ("Material_",),
     "Quantities are limited to 0–9,999,999. Boss and crafting materials are single items: add them with "
     "Add items… rather than changing their quantity."),
    ("Consumables", ("Consumable_",),
     "Quantities are limited to 0–9,999,999. Very large stacks are harmless but look odd in-game."),
    ("Weapons & Gear", ("Weapon_", "Mod_", "Armor_", "Ring_", "Amulet_", "Relic_"),
     "Weapon levels: regular weapons 0–20, boss weapons (with a built-in mod) 0–10. Gear left at "
     "level 0 has no stored level; add it fresh or upgrade it once in-game before editing."),
    ("Fragments & Prisms", ("RelicFragment_", "MetaGem_", "Prism"),
     "Relic fragment levels are limited to 1–10. Prism levels are tied to their segment data, so they are read-only."),
]
HIDDEN_FIELDS = {"Ammo", "AmmoInClip"}


def tab_for(name):
    for tab, prefixes, _ in TABS[1:]:
        if name.startswith(prefixes):
            return tab
    return "Weapons & Gear"


def game_running():
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return any(row and row[0].lower().startswith("remnant2") for row in csv.reader(io.StringIO(out)))
    except OSError:
        return False


# ---------------------------------------------------------------- backups

def backup_dir_for(save_dir):
    return os.path.join(BACKUP_ROOT, os.path.basename(os.path.normpath(save_dir)))


def list_backups(save_dir):
    root = backup_dir_for(save_dir)
    if not os.path.isdir(root):
        return []
    return sorted((d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))), reverse=True)


def make_backup(save_dir):
    """Copies every .sav in save_dir into a new timestamped folder; keeps the newest KEEP_BACKUPS."""
    root = backup_dir_for(save_dir)
    stamp = time.strftime("%Y-%m-%d %H-%M-%S")
    dest, n = os.path.join(root, stamp), 1
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(root, f"{stamp} ({n})")
    os.makedirs(dest)
    files = [f for f in os.listdir(save_dir) if f.lower().endswith(".sav")]
    for f in files:
        shutil.copy2(os.path.join(save_dir, f), dest)
    for old in list_backups(save_dir)[KEEP_BACKUPS:]:
        shutil.rmtree(os.path.join(root, old), ignore_errors=True)
    return dest, files


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Remnant 2 Character Editor")
        self.geometry("900x680")
        self.minsize(700, 480)
        self.path = None
        self.original = None   # bytes as loaded from disk
        self.full = None       # working copy (includes added items)
        self.fields, self.rows, self.edits = {}, [], {}
        self.added = []
        self.qty_fixes = []    # quantities added where the game had left them out
        self.char_slots = []
        self.catalog = r2items.load_catalog()
        self.catalog_by_path = {e["path"]: e for e in self.catalog}
        self.catalog_by_short = {e["path"].rsplit(".", 1)[-1].removesuffix("_C"): e for e in self.catalog}

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="Open profile.sav…", command=self.open_file).pack(side="left")
        ttk.Label(top, text="  Character:").pack(side="left")
        self.char_box = ttk.Combobox(top, state="readonly", width=22)
        self.char_box.pack(side="left")
        self.char_box.bind("<<ComboboxSelected>>", lambda e: self.load_character())
        ttk.Label(top, text="  Search:").pack(side="left")
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *a: self.refresh())
        ttk.Entry(top, textvariable=self.search, width=18).pack(side="left")
        ttk.Button(top, text="Add items…", command=self.open_add_dialog).pack(side="right")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8)
        self.trees = {}
        for tab, _, note in TABS:
            frame = ttk.Frame(self.nb)
            self.nb.add(frame, text=tab)
            ttk.Label(frame, text="⚠ " + note, wraplength=840, foreground="#8a5a00",
                      padding=(4, 6)).pack(fill="x")
            body = ttk.Frame(frame)
            body.pack(fill="both", expand=True)
            tree = ttk.Treeview(body, columns=("name", "field", "value", "limit"), show="headings",
                                selectmode="browse")
            for col, title, w, anchor in (("name", "Name", 340, "w"), ("field", "Field", 120, "w"),
                                          ("value", "Value", 120, "e"), ("limit", "Allowed", 160, "w")):
                tree.heading(col, text=title)
                tree.column(col, width=w, anchor=anchor)
            tree.tag_configure("edited", foreground="#c25400")
            tree.tag_configure("locked", foreground="#888888")
            tree.tag_configure("new", foreground="#1a7f37")
            sb = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            tree.bind("<Double-1>", lambda e, t=tree: self.begin_edit(e, t))
            tree.bind("<Return>", lambda e, t=tree: self.begin_edit(None, t))
            self.trees[tab] = tree

        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Save", command=self.save).pack(side="right")
        ttk.Button(bottom, text="Revert changes", command=self.revert).pack(side="right", padx=6)
        ttk.Button(bottom, text="Restore backup…", command=self.open_restore_dialog).pack(side="right")
        self.status = tk.StringVar(value="Open your profile.sav to begin (close the game first).")
        ttk.Label(bottom, textvariable=self.status).pack(side="left")
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---- state ----
    def dirty(self):
        return bool(self.edits or self.added or self.qty_fixes)

    def slot(self):
        return self.char_slots[self.char_box.current()][0]

    def rule(self, key):
        """(lo, hi) for an editable field, or None if it is read-only."""
        name, _, field = key.partition(".")
        if not field:
            if key in ("TraitRank", "PowerLevel", "ItemLevel"):
                return None
            if key in ("TraitPoints", "LastSavedTraitPoints"):
                return (0, 9999)
            return (0, I32_MAX)
        if field == "Quantity":
            return (0, QTY_MAX)
        if field == "Experience":
            return (0, I32_MAX)
        if field == "Level":
            if name.startswith(("Perk_", "SkillTrait_", "Prism")):
                return None
            if name.startswith(("Trait_", "Archetype_")):
                return (0, 10)
            if name.startswith("RelicFragment_"):
                return (1, 10)
            entry = next((e for e in self.catalog if e["id"] == name), None)
            if entry and entry["type"] == "weapon":
                return r2items.level_range(entry)
            return (0, 20)
        return None

    # ---- loading ----
    def open_file(self):
        if self.dirty() and not messagebox.askyesno("Discard changes?", "You have unsaved changes. Discard them?"):
            return
        start = SAVE_ROOT if os.path.isdir(SAVE_ROOT) else os.path.expanduser("~")
        path = filedialog.askopenfilename(title="Open Remnant 2 profile.sav", initialdir=start,
                                          filetypes=[("Remnant 2 profile", "profile.sav"), ("Save files", "*.sav")])
        if path:
            self.load_path(path)

    def load_path(self, path):
        try:
            full = r2save.unpack(open(path, "rb").read())
            slots, active = r2edit.characters(full)
            r2model.load(full)  # make sure the full model can read it too
        except Exception as e:
            messagebox.showerror("Could not read save", f"{e}\n\nMake sure this is profile.sav (not save_0.sav).")
            return False
        self.path, self.original, self.full, self.char_slots = path, full, full, slots
        self.char_box["values"] = [f"Slot {s + 1}: {a}" + (" (active)" if s == active else "") for s, a in slots]
        self.char_box.current(next((i for i, (s, _) in enumerate(slots) if s == active), 0))
        self.load_character()
        return True

    def load_character(self):
        self.edits, self.added, self.qty_fixes = {}, [], []
        self.full = self.original
        self.recollect()
        self.status.set(f"Loaded {os.path.basename(self.path)}. Double-click a value to edit it.")

    def recollect(self):
        _, self.rows, self.fields = r2edit.collect(self.full, self.slot())
        self.refresh()

    def refresh(self):
        if not self.full:
            return
        q = self.search.get().lower()
        for tree in self.trees.values():
            tree.delete(*tree.get_children())
        char_keys = ("TraitPoints", "LastSavedTraitPoints", "Experience", "TraitRank", "PowerLevel", "ItemLevel")
        entries = [("Character", k, "", k, k) for k in char_keys if k in self.fields]
        seen = set()
        added_names = {e["path"].rsplit(".", 1)[-1].removesuffix("_C") for e, _, _ in self.added}
        for section, name, props in self.rows:
            shown = False
            for f in props:
                key = f"{name}.{f}"
                if key in seen or key not in self.fields or f in HIDDEN_FIELDS:
                    continue
                seen.add(key)
                shown = True
                entries.append((tab_for(name), r2items.pretty(name), f, key, name))
            if not shown and section == "Item" and name not in seen:
                seen.add(name)
                entries.append((tab_for(name), r2items.pretty(name), "owned", None, name))
        for tab, label, field, key, name in entries:
            if q and q not in label.lower() and q not in name.lower():
                continue
            if key is None and r2items.is_stackable(self.catalog_by_short.get(name)) and name not in added_names:
                # Owned stackable whose quantity the game didn't store (it omits the default of 1)
                self.trees[tab].insert("", "end", iid=f"qty:{name}",
                                       values=(label, "Quantity", 1, f"0 – {QTY_MAX:,} (not stored yet)"))
                continue
            if key is None:
                new = name in added_names
                self.trees[tab].insert("", "end", values=(label, field, "", "new, unsaved" if new else "nothing to edit"),
                                       tags=("new",) if new else ("locked",))
                continue
            rng = self.rule(key)
            v = self.edits.get(key, self.fields[key].value)
            tags = ("edited",) if key in self.edits else ("new",) if name in added_names else ("locked",) if rng is None else ()
            limit = "read-only" if rng is None else f"{rng[0]:,} – {rng[1]:,}"
            self.trees[tab].insert("", "end", iid=key, values=(label, field, v, limit), tags=tags)
        for i, (tab, _, _) in enumerate(TABS):
            self.nb.tab(i, text=f"{tab} ({len(self.trees[tab].get_children())})")

    # ---- editing ----
    def begin_edit(self, event, tree):
        key = tree.focus() if event is None else tree.identify_row(event.y)
        if not key or (key not in self.fields and not key.startswith("qty:")):
            return
        if not key.startswith("qty:") and self.rule(key) is None:
            self.status.set(f"{key} is read-only. The game calculates it, or editing it could break your save.")
            return
        x, y, w, h = tree.bbox(key, "value")
        entry = ttk.Entry(tree, justify="right")
        entry.insert(0, tree.set(key, "value"))
        entry.select_range(0, "end")
        entry.place(x=x, y=y, width=w, height=h)
        entry.focus_set()

        def commit(_=None):
            text = entry.get().strip().replace(",", "")
            entry.destroy()
            if key.startswith("qty:"):
                self.set_missing_quantity(key[4:], text)
            else:
                self.apply_edit(key, text)

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", lambda e: entry.destroy())

    def apply_edit(self, key, text):
        val, rng = self.fields[key], self.rule(key)
        if rng is None:
            return
        try:
            new = int(text)
        except ValueError:
            self.status.set(f"'{text}' is not a whole number.")
            return
        if not rng[0] <= new <= rng[1]:
            self.status.set(f"Blocked: {key} must be between {rng[0]:,} and {rng[1]:,}.")
            return
        if new == val.value:
            self.edits.pop(key, None)
        else:
            self.edits[key] = new
        self.status.set(f"{key} → {new}. {len(self.edits) + len(self.added) + len(self.qty_fixes)} unsaved change(s).")
        self.refresh()

    def set_missing_quantity(self, name, text):
        """Adds a Quantity the game left out; a structural edit, like adding an item."""
        entry = self.catalog_by_short.get(name)
        try:
            qty = int(text)
        except ValueError:
            self.status.set(f"'{text}' is not a whole number.")
            return
        if qty == 1:
            return  # unchanged: 1 is what the missing value already means
        self.commit_edits()
        model = r2model.load(self.full)
        try:
            r2items.set_quantity(model, self.slot(), entry, qty)
        except ValueError as e:
            self.status.set("Blocked: " + str(e))
            return
        self.full = r2model.dump(model)
        self.qty_fixes.append(entry["name"])
        self.recollect()
        self.status.set(f"{entry['name']} quantity → {qty:,}. Not saved yet.")

    def commit_edits(self):
        """Folds pending number edits into the working copy (needed before structural changes)."""
        if not self.edits:
            return
        buf = bytearray(self.full)
        for key, new in self.edits.items():
            v = self.fields[key]
            struct.pack_into("<" + v.fmt, buf, v.off, new)
        self.full, self.edits = bytes(buf), {}

    def add_items(self, picks):
        """picks: [(entry, quantity, level)]. Returns an error string or None."""
        self.commit_edits()
        model = r2model.load(self.full)
        try:
            for entry, qty, lvl in picks:
                r2items.add_item(model, self.slot(), entry, qty, lvl)
        except ValueError as e:
            return str(e)
        self.full = r2model.dump(model)
        self.added += picks
        self.recollect()
        self.status.set(f"Added {', '.join(e['name'] for e, _, _ in picks)}. "
                        f"{len(self.added)} item(s) added, not saved yet.")
        return None

    def revert(self):
        self.edits, self.added, self.qty_fixes = {}, [], []
        if self.original:
            self.full = self.original
            self.recollect()
        self.status.set("All unsaved changes reverted.")

    # ---- saving ----
    def save(self):
        if not self.full:
            return
        if not self.dirty():
            self.status.set("Nothing to save.")
            return
        if game_running():
            messagebox.showwarning("Remnant 2 is running",
                                   "Close Remnant 2 completely before saving. The game overwrites the "
                                   "save on exit, which would undo or corrupt your edits.")
            return
        if not messagebox.askyesno("Save changes?",
                                   f"{len(self.edits) + len(self.qty_fixes)} value edit(s) and {len(self.added)} added item(s) will be "
                                   "written.\n\nYour whole save folder is backed up first. Turn off Steam Cloud "
                                   "for Remnant 2 so it doesn't overwrite the edit.\n\nContinue?"):
            return
        self.commit_edits()
        packed = r2save.pack(self.full)
        try:  # verify the new file reads back with both parsers before touching disk
            check = r2save.unpack(packed)
            assert check[8:] == self.full[8:]
            assert r2model.dump(r2model.load(check))[8:] == check[8:]
            r2edit.collect(check, self.slot())
        except Exception as e:
            messagebox.showerror("Save blocked", f"The edited save failed verification, so nothing was written.\n\n{e}")
            return
        save_dir = os.path.dirname(self.path)
        try:
            dest, files = make_backup(save_dir)
        except OSError as e:
            messagebox.showerror("Backup failed", f"Could not back up your save folder, so nothing was written.\n\n{e}")
            return
        with open(self.path, "wb") as f:
            f.write(packed)
        self.load_path(self.path)
        self.status.set(f"Saved. Backup of {len(files)} file(s) in {dest}")

    # ---- dialogs ----
    def open_add_dialog(self):
        if not self.full:
            messagebox.showinfo("Open a save first", "Open your profile.sav before adding items.")
            return
        AddDialog(self)

    def open_restore_dialog(self):
        save_dir = os.path.dirname(self.path) if self.path else None
        if not save_dir:
            start = SAVE_ROOT if os.path.isdir(SAVE_ROOT) else os.path.expanduser("~")
            save_dir = filedialog.askdirectory(title="Pick your Remnant 2 save folder", initialdir=start)
            if not save_dir:
                return
        RestoreDialog(self, save_dir)

    def on_close(self):
        if self.dirty() and not messagebox.askyesno("Quit?", "You have unsaved changes. Quit anyway?"):
            return
        self.destroy()


class AddDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Add items")
        self.geometry("820x560")
        self.transient(app)
        self.owned = r2items.owned_paths(r2model.load(app.full), app.slot())

        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")
        ttk.Label(bar, text="Search:").pack(side="left")
        self.q = tk.StringVar()
        self.q.trace_add("write", lambda *a: self.fill())
        e = ttk.Entry(bar, textvariable=self.q, width=28)
        e.pack(side="left")
        e.focus_set()
        ttk.Label(bar, text="  Category:").pack(side="left")
        cats = ["All"] + sorted({x["label"] for x in app.catalog})
        self.cat = ttk.Combobox(bar, values=cats, state="readonly", width=30)
        self.cat.current(0)
        self.cat.pack(side="left")
        self.cat.bind("<<ComboboxSelected>>", lambda e: self.fill())
        self.hide_owned = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Hide owned", variable=self.hide_owned, command=self.fill).pack(side="left", padx=8)

        mid = ttk.Frame(self, padding=(8, 0))
        mid.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(mid, columns=("name", "cat", "world"), show="headings", selectmode="browse")
        for col, title, w in (("name", "Item", 300), ("cat", "Category", 220), ("world", "World", 120)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=w)
        self.tree.tag_configure("owned", foreground="#888888")
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.on_select())
        self.tree.bind("<Double-1>", lambda e: self.add())

        det = ttk.Frame(self, padding=8)
        det.pack(fill="x")
        self.info = tk.StringVar(value="Select an item to see where it normally comes from.")
        ttk.Label(det, textvariable=self.info, wraplength=790, justify="left").pack(fill="x")
        row = ttk.Frame(det)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Quantity:").pack(side="left")
        self.qty = ttk.Spinbox(row, from_=1, to=QTY_MAX, width=10)
        self.qty.set(1)
        self.qty.pack(side="left")
        ttk.Label(row, text="   Level:").pack(side="left")
        self.lvl = ttk.Spinbox(row, from_=0, to=20, width=6)
        self.lvl.set(0)
        self.lvl.pack(side="left")
        self.range_lbl = ttk.Label(row, text="")
        self.range_lbl.pack(side="left", padx=6)
        ttk.Button(row, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(row, text="Add to inventory", command=self.add).pack(side="right", padx=6)
        self.msg = tk.StringVar()
        ttk.Label(det, textvariable=self.msg, foreground="#b42318", wraplength=790).pack(fill="x", pady=(6, 0))
        not_addable = "; ".join(f"{k}: {v}" for k, v in r2items.NOT_ADDABLE_REASON.items())
        ttk.Label(det, text="Not addable (could break your save): " + not_addable, wraplength=790,
                  foreground="#8a5a00").pack(fill="x", pady=(6, 0))
        self.fill()

    def fill(self):
        q, cat = self.q.get().lower(), self.cat.get()
        self.tree.delete(*self.tree.get_children())
        for i, x in enumerate(self.app.catalog):
            owned = x["path"] in self.owned
            if owned and self.hide_owned.get():
                continue
            if cat != "All" and x["label"] != cat:
                continue
            if q and q not in x["name"].lower() and q not in x["id"].lower():
                continue
            self.tree.insert("", "end", iid=str(i), values=(x["name"] + (" (owned)" if owned else ""), x["label"],
                                                             x["world"].replace("World_", "")),
                             tags=("owned",) if owned else ())

    def selected(self):
        sel = self.tree.selection()
        return self.app.catalog[int(sel[0])] if sel else None

    def on_select(self):
        x = self.selected()
        if not x:
            return
        self.msg.set("")
        has_qty = r2items.ADDABLE[x["type"]][2]
        rng = r2items.level_range(x)
        self.qty.configure(state="normal" if has_qty else "disabled")
        self.qty.set(1)
        self.lvl.configure(state="normal" if rng else "disabled", from_=rng[0] if rng else 0, to=rng[1] if rng else 0)
        self.lvl.set(rng[0] if rng else 0)
        self.range_lbl.configure(text=f"(allowed {rng[0]}–{rng[1]})" if rng else "")
        extra = f"  Built-in mod: {r2items.pretty(x['mod'])}." if x.get("mod") else ""
        self.info.set(f"{x['name']} [{x['label']}]{extra}\n{x['note'] or 'No source notes available.'}")

    def add(self):
        x = self.selected()
        if not x:
            self.msg.set("Pick an item first.")
            return
        try:
            qty = int(self.qty.get()) if r2items.ADDABLE[x["type"]][2] else 1
            lvl = int(self.lvl.get()) if r2items.level_range(x) else 0
        except ValueError:
            self.msg.set("Quantity and level must be whole numbers.")
            return
        err = r2items.validate(x, qty, lvl, self.owned) or self.app.add_items([(x, qty, lvl)])
        if err:
            self.msg.set("Blocked: " + err)
            return
        self.owned.add(x["path"])
        self.msg.set(f"Added {x['name']}. It shows as new and unsaved until you click Save in the main window.")
        self.fill()


class RestoreDialog(tk.Toplevel):
    def __init__(self, app, save_dir):
        super().__init__(app)
        self.app, self.save_dir = app, save_dir
        self.title("Restore a backup")
        self.geometry("560x340")
        self.transient(app)
        ttk.Label(self, text=f"Backups of {save_dir}\n(newest first; the {KEEP_BACKUPS} most recent are kept)",
                  padding=8, wraplength=540).pack(fill="x")
        self.lb = tk.Listbox(self, height=8)
        self.lb.pack(fill="both", expand=True, padx=8)
        self.backups = list_backups(save_dir)
        for b in self.backups:
            files = os.listdir(os.path.join(backup_dir_for(save_dir), b))
            self.lb.insert("end", f"{b}   ({len(files)} files)")
        if not self.backups:
            self.lb.insert("end", "No backups yet. One is made automatically every time you save.")
        row = ttk.Frame(self, padding=8)
        row.pack(fill="x")
        ttk.Button(row, text="Open backups folder", command=self.open_folder).pack(side="left")
        ttk.Button(row, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(row, text="Restore selected", command=self.restore).pack(side="right", padx=6)

    def open_folder(self):
        path = backup_dir_for(self.save_dir)
        os.makedirs(path, exist_ok=True)
        os.startfile(path)

    def restore(self):
        sel = self.lb.curselection()
        if not sel or not self.backups:
            return
        name = self.backups[sel[0]]
        if game_running():
            messagebox.showwarning("Remnant 2 is running", "Close Remnant 2 before restoring a backup.", parent=self)
            return
        if not messagebox.askyesno("Restore backup?",
                                   f"Replace the save files in your save folder with the backup from {name}?\n\n"
                                   "Your current files are backed up first.", parent=self):
            return
        src = os.path.join(backup_dir_for(self.save_dir), name)
        data = {f: open(os.path.join(src, f), "rb").read() for f in os.listdir(src)}  # read before pruning
        make_backup(self.save_dir)
        for f, b in data.items():
            with open(os.path.join(self.save_dir, f), "wb") as out:
                out.write(b)
        profile = os.path.join(self.save_dir, "profile.sav")
        if os.path.exists(profile):
            self.app.load_path(profile)
        self.app.status.set(f"Restored backup from {name}.")
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
