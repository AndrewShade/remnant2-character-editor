"""Lossless read/write model for unpacked Remnant 2 profile saves
(port of AndrewSav/lib.remnant2.saves). Used for structural edits such as
adding inventory items; simple number edits are patched in place instead.

  m = load(full_bytes); ...; full_bytes = dump(m)
"""
import io
import struct

PROFILE_CLASS = "/Game/_Core/Blueprints/Base/BP_RemnantSaveGameProfile"
VAR_KEYS = {"GlobalVariables", "Variables", "Variable", "PersistenceKeys", "PersistanceKeys1", "PersistenceKeys1"}
SIMPLE = {"IntProperty": "i", "Int16Property": "h", "Int64Property": "q", "UInt64Property": "Q",
          "FloatProperty": "f", "DoubleProperty": "d", "UInt16Property": "H", "UInt32Property": "I"}


class N:
    """FName: a names-table string plus optional number suffix."""
    __slots__ = ("name", "number")

    def __init__(self, name, number=None):
        self.name, self.number = name, number

    def __repr__(self):
        return self.name

    def wlen(self):
        return 2 if self.number is None else 6


class Node:
    """Generic record with attribute access."""

    def __init__(self, kind, **kw):
        self.kind = kind
        self.__dict__.update(kw)

    def __repr__(self):
        return f"<{self.kind}>"


# ---------------------------------------------------------------- reading

class R:
    def __init__(self, buf):
        self.buf, self.pos = buf, 0

    def u(self, fmt):
        v = struct.unpack_from("<" + fmt, self.buf, self.pos)
        self.pos += struct.calcsize("<" + fmt)
        return v[0] if len(v) == 1 else v

    def bytes(self, n):
        b = bytes(self.buf[self.pos:self.pos + n])
        self.pos += n
        return b

    def fstr(self):
        n = self.u("i")
        if n == 0:
            return None
        if n < 0:
            return self.bytes(-n * 2)[:-2].decode("utf-16-le")
        return self.bytes(n)[:-1].decode("ascii")

    def guid(self):
        has = self.u("B")
        return b"\x00" if not has else bytes([has]) + self.bytes(16)


def rname(r, names):
    i = r.u("H")
    if i & 0x8000:
        return N(names[i & 0x7FFF], r.u("i"))
    return N(names[i])


def r_value(r, ctx, t, raw=True):
    """Returns (value, guid) where guid is None for raw values."""
    g = None
    if t in SIMPLE:
        if not raw: g = r.guid()
        return r.u(SIMPLE[t]), g
    if t in ("StrProperty", "SoftClassPath", "SoftObjectProperty"):
        if not raw: g = r.guid()
        return r.fstr(), g
    if t == "BoolProperty":
        v = r.u("B")
        if not raw: g = r.guid()
        return v, g
    if t == "NameProperty":
        if not raw: g = r.guid()
        return rname(r, ctx.names), g
    if t == "ByteProperty":
        if raw:
            return r.u("B"), None
        enum = rname(r, ctx.names)
        pg = r.guid()
        val = r.u("B") if enum.name == "None" else rname(r, ctx.names)
        return Node("Byte", enum=enum, guid=pg, value=val), None
    if t == "StructProperty":
        if raw:
            return r.bytes(16), None
        st = rname(r, ctx.names)
        sguid = r.bytes(16)
        pg = r.guid()
        return Node("Struct", type=st, sguid=sguid, guid=pg, value=r_struct(r, ctx, st.name)), None
    if t == "ObjectProperty":
        if not raw: g = r.guid()
        return r.u("i"), g
    if t == "EnumProperty":
        et = rname(r, ctx.names)
        pg = r.guid()
        return Node("Enum", type=et, guid=pg, value=rname(r, ctx.names)), None
    if t == "MapProperty":
        kt, vt = rname(r, ctx.names), rname(r, ctx.names)
        pg = r.guid()
        removes = [r_value(r, ctx, kt.name)[0] for _ in range(r.u("i"))]
        pairs = [(r_value(r, ctx, kt.name)[0], r_value(r, ctx, vt.name)[0]) for _ in range(r.u("i"))]
        return Node("Map", kt=kt, vt=vt, guid=pg, removes=removes, pairs=pairs), None
    if t == "TextProperty":
        if not raw: g = r.guid()
        flags, hist = r.u("I"), r.u("B")
        if hist == 0:
            data = (r.fstr(), r.fstr(), r.fstr())
        elif hist == 255:
            flag = r.u("I")
            data = (flag, r.fstr() if flag else None)
        else:
            raise ValueError("unsupported text history")
        return Node("Text", flags=flags, hist=hist, data=data), g
    if t == "ArrayProperty":
        et = rname(r, ctx.names)
        pg = r.guid()
        count = r.u("I")
        if et.name == "StructProperty":
            ni, ti, size, idx = r.u("HHII")
            elem = rname(r, ctx.names)
            sguid = r.bytes(16)
            ig = r.guid()
            items = [r_struct(r, ctx, elem.name) for _ in range(count)]
            return Node("ArrayStruct", et=et, guid=pg, ni=ni, ti=ti, size=size, index=idx, elem=elem,
                        sguid=sguid, iguid=ig, items=items), None
        return Node("Array", et=et, guid=pg, items=[r_value(r, ctx, et.name)[0] for _ in range(count)]), None
    raise ValueError(f"unknown property type {t}")


def r_struct(r, ctx, st):
    if st in ("SoftClassPath", "SoftObjectPath"):
        return r.fstr()
    if st in ("Timespan", "DateTime"):
        return r.u("q")
    if st == "Guid":
        return r.bytes(16)
    if st in ("Vector", "Rotator"):
        return r.bytes(24)
    if st == "PersistenceBlob":
        n = r.u("i")
        blob = r.bytes(n)
        if ctx.class_path != PROFILE_CLASS:
            return Node("RawBlob", data=blob)
        sub = R(blob)
        sd = r_savedata(sub, True, False)
        sd.extra = blob[sub.pos:] or None
        return sd
    return r_bag(r, ctx)


def r_bag(r, ctx):
    props = []
    while True:
        name = rname(r, ctx.names)
        if name.name == "None":
            return Node("Bag", props=props, none=name)
        t = rname(r, ctx.names)
        size, index = r.u("II")
        p = Node("Prop", name=name, type=t, size=size, index=index, guid=None)
        if name.name == "FowVisitedCoordinates":
            p.value, p.rawfow = r.bytes(size + 19), True
        else:
            p.value, p.guid = r_value(r, ctx, t.name, raw=False)
        props.append(p)


def r_variables(r, ctx):
    name = rname(r, ctx.names)
    unknown = r.u("Q")
    items = []
    for _ in range(r.u("i")):
        n = rname(r, ctx.names)
        t = r.u("B")
        v = r.u("I") if t in (1, 2) else r.u("f") if t == 3 else rname(r, ctx.names) if t == 4 else None
        items.append((n, t, v))
    return Node("Vars", name=name, unknown=unknown, items=items)


class Ctx:
    def __init__(self, names, class_path):
        self.names, self.class_path = names, class_path
        self._index = {}

    def name_index(self, s):
        if s not in self._index:
            try:
                self._index[s] = self.names.index(s)
            except ValueError:
                self.names.append(s)
                self._index[s] = len(self.names) - 1
        return self._index[s]


def r_savedata(r, has_pkg=True, has_path=True):
    sd = Node("SaveData", pkg=r.bytes(8) if has_pkg else None, path=None, extra=None)
    if has_path:
        sd.path = (r.fstr(), r.fstr())
    names_off, sd.version, objs_off = r.u("qIq")
    data_off = r.pos
    r.pos = names_off
    sd.names = [r.fstr() for _ in range(r.u("i"))]
    end = r.pos
    class_path = sd.path[0] if sd.path else None
    ctx = Ctx(sd.names, class_path)
    r.pos = objs_off
    sd.objects = []
    for i in range(r.u("i")):
        o = Node("Object", loaded=r.u("B"), oname=None, outer=None, props=None, extra=None, comps=None)
        o.path = class_path if o.loaded and i == 0 and class_path else r.fstr()
        if not o.loaded:
            o.oname = rname(r, sd.names)
            o.outer = r.u("I")
        sd.objects.append(o)
    end = max(end, r.pos)
    r.pos = data_off
    for o in sd.objects:
        o.index = r.u("i")
        ln = r.u("I")
        start = r.pos
        if ln:
            o.props = r_bag(r, ctx)
            if r.pos < start + ln:
                o.extra = r.bytes(start + ln - r.pos)
        o.actor = r.u("B")
        if o.actor:
            o.comps = []
            for _ in range(r.u("I")):
                key = r.fstr()
                ln = r.u("i")
                s = r.pos
                data = r_variables(r, ctx) if key in VAR_KEYS else r_bag(r, ctx)
                extra = r.bytes(s + ln - r.pos) if r.pos < s + ln else None
                o.comps.append(Node("Comp", key=key, data=data, extra=extra))
    r.pos = max(end, r.pos)
    return sd


def load(full):
    r = R(full)
    header = r.bytes(16)
    return Node("File", header=header, data=r_savedata(r))


# ---------------------------------------------------------------- writing

class W:
    def __init__(self):
        self.b = io.BytesIO()

    def p(self, fmt, *v):
        self.b.write(struct.pack("<" + fmt, *v))

    def raw(self, data):
        self.b.write(data)

    @property
    def pos(self):
        return self.b.tell()

    def patch(self, at, fmt, *v):
        here = self.pos
        self.b.seek(at)
        self.p(fmt, *v)
        self.b.seek(here)

    def fstr(self, s):
        if s is None:
            self.p("i", 0)
        elif s.isascii():
            self.p("i", len(s) + 1)
            self.raw(s.encode("ascii") + b"\x00")
        else:
            self.p("i", -(len(s) + 1))
            self.raw(s.encode("utf-16-le") + b"\x00\x00")

    def getvalue(self):
        return self.b.getvalue()


def wname(w, ctx, n):
    i = ctx.name_index(n.name)
    if n.number is None:
        w.p("H", i)
    else:
        w.p("H", i | 0x8000)
        w.p("i", n.number)


def w_value(w, ctx, t, v, g=None):
    if t in SIMPLE:
        if g is not None: w.raw(g)
        w.p(SIMPLE[t], v)
    elif t in ("StrProperty", "SoftClassPath", "SoftObjectProperty"):
        if g is not None: w.raw(g)
        w.fstr(v)
    elif t == "BoolProperty":
        w.p("B", v)
        if g is not None: w.raw(g)
    elif t == "NameProperty":
        if g is not None: w.raw(g)
        wname(w, ctx, v)
    elif t == "ByteProperty":
        if isinstance(v, int):
            w.p("B", v)
        else:
            wname(w, ctx, v.enum)
            w.raw(v.guid)
            w.p("B", v.value) if v.enum.name == "None" else wname(w, ctx, v.value)
    elif t == "StructProperty":
        if isinstance(v, bytes):
            w.raw(v)
        else:
            wname(w, ctx, v.type)
            w.raw(v.sguid)
            w.raw(v.guid)
            w_struct(w, ctx, v.type.name, v.value)
    elif t == "ObjectProperty":
        if g is not None: w.raw(g)
        w.p("i", v)
    elif t == "EnumProperty":
        wname(w, ctx, v.type)
        w.raw(v.guid)
        wname(w, ctx, v.value)
    elif t == "MapProperty":
        wname(w, ctx, v.kt)
        wname(w, ctx, v.vt)
        w.raw(v.guid)
        w.p("i", len(v.removes))
        for k in v.removes:
            w_value(w, ctx, v.kt.name, k)
        w.p("i", len(v.pairs))
        for k, val in v.pairs:
            w_value(w, ctx, v.kt.name, k)
            w_value(w, ctx, v.vt.name, val)
    elif t == "TextProperty":
        if g is not None: w.raw(g)
        w.p("I", v.flags)
        w.p("B", v.hist)
        if v.hist == 0:
            for s in v.data:
                w.fstr(s)
        else:
            w.p("I", v.data[0])
            if v.data[0]:
                w.fstr(v.data[1])
    elif t == "ArrayProperty":
        wname(w, ctx, v.et)
        w.raw(v.guid)
        w.p("I", len(v.items))
        if v.kind == "ArrayStruct":
            w.p("HH", v.ni, v.ti)
            size_at = w.pos
            w.p("II", v.size, v.index)
            wname(w, ctx, v.elem)
            w.raw(v.sguid)
            w.raw(v.iguid)
            start = w.pos
            for it in v.items:
                w_struct(w, ctx, v.elem.name, it)
            v.size = w.pos - start
            w.patch(size_at, "I", v.size)
        else:
            for it in v.items:
                w_value(w, ctx, v.et.name, it)
    else:
        raise ValueError(f"unknown property type {t}")


def w_struct(w, ctx, st, v):
    if st in ("SoftClassPath", "SoftObjectPath"):
        w.fstr(v)
    elif st in ("Timespan", "DateTime"):
        w.p("q", v)
    elif st in ("Guid", "Vector", "Rotator"):
        w.raw(v)
    elif st == "PersistenceBlob":
        data = v.data if v.kind == "RawBlob" else w_savedata(v)
        w.p("i", len(data))
        w.raw(data)
    else:
        w_bag(w, ctx, v)


def overhead(p):
    t, v = p.type.name, p.value
    if t == "ArrayProperty":
        return v.et.wlen() + len(v.guid)
    if t == "BoolProperty":
        return 1 + len(p.guid)
    if t == "ByteProperty":
        return v.enum.wlen() + len(v.guid) if not isinstance(v, int) else 0
    if t == "EnumProperty":
        return v.type.wlen() + len(v.guid)
    if t == "MapProperty":
        return v.kt.wlen() + v.vt.wlen() + len(v.guid)
    if t == "StructProperty":
        return v.type.wlen() + 16 + len(v.guid)
    return len(p.guid) if p.guid is not None else 1


def w_bag(w, ctx, bag):
    for p in bag.props:
        wname(w, ctx, p.name)
        wname(w, ctx, p.type)
        size_at = w.pos
        w.p("II", p.size, p.index)
        start = w.pos
        if getattr(p, "rawfow", False):
            w.raw(p.value)
            continue
        w_value(w, ctx, p.type.name, p.value, p.guid)
        p.size = w.pos - start - overhead(p)
        w.patch(size_at, "I", p.size)
    wname(w, ctx, bag.none)


def w_variables(w, ctx, v):
    wname(w, ctx, v.name)
    w.p("Q", v.unknown)
    w.p("i", len(v.items))
    for n, t, val in v.items:
        wname(w, ctx, n)
        w.p("B", t)
        if t in (1, 2):
            w.p("I", val)
        elif t == 3:
            w.p("f", val)
        elif t == 4:
            wname(w, ctx, val)


def w_savedata(sd, base=0):
    """base: bytes that precede this blob in its buffer (the file header at top level)."""
    w = W()
    w.raw(b"\x00" * base)
    if sd.pkg is not None:
        w.raw(sd.pkg)
    if sd.path:
        w.fstr(sd.path[0])
        w.fstr(sd.path[1])
    info_at = w.pos
    w.p("qIq", 0, sd.version, 0)
    class_path = sd.path[0] if sd.path else None
    ctx = Ctx(sd.names, class_path)
    for o in sd.objects:
        w.p("i", o.index)
        len_at = w.pos
        w.p("I", 0)
        if o.props is not None:
            start = w.pos
            w_bag(w, ctx, o.props)
            if o.extra:
                w.raw(o.extra)
            w.patch(len_at, "I", w.pos - start)
        w.p("B", o.actor)
        if o.actor:
            w.p("I", len(o.comps))
            for c in o.comps:
                w.fstr(c.key)
                clen_at = w.pos
                w.p("i", 0)
                start = w.pos
                w_variables(w, ctx, c.data) if c.key in VAR_KEYS else w_bag(w, ctx, c.data)
                if c.extra:
                    w.raw(c.extra)
                w.patch(clen_at, "i", w.pos - start)
    objs_off = w.pos
    w.p("i", len(sd.objects))
    for i, o in enumerate(sd.objects):
        w.p("B", o.loaded)
        if not (o.loaded and i == 0 and class_path):
            w.fstr(o.path)
        if not o.loaded:
            wname(w, ctx, o.oname)
            w.p("I", o.outer)
    names_off = w.pos
    w.p("i", len(sd.names))
    for s in sd.names:
        w.fstr(s)
    if sd.extra:
        w.raw(sd.extra)
    w.patch(info_at, "qIq", names_off, sd.version, objs_off)
    return w.getvalue()[base:]


def dump(model):
    """Returns the unpacked ("full") bytes; r2save.pack fixes size and crc."""
    body = w_savedata(model.data, len(model.header))
    header = bytearray(model.header)
    struct.pack_into("<i", header, 4, len(header) + len(body))
    return bytes(header) + body


# ---------------------------------------------------------------- helpers

def prop(bag, name):
    for p in bag.props:
        if p.name.name == name:
            return p
    return None


def object_refs(sd):
    """Yields (getter, setter) for every object index stored in sd's data:
    ObjectProperty values in bags, arrays and maps (nested blobs have their own tables)."""
    def visit(v, t):
        # v is a value of property type t; yields refs inside it
        if t == "ObjectProperty":
            return  # handled by the caller, which owns the slot
        if t == "StructProperty" and isinstance(v, Node) and v.kind == "Struct":
            yield from bag_refs(v.value) if isinstance(v.value, Node) and v.value.kind == "Bag" else ()
        elif t == "ArrayProperty" and isinstance(v, Node):
            if v.kind == "ArrayStruct":
                for it in v.items:
                    if isinstance(it, Node) and it.kind == "Bag":
                        yield from bag_refs(it)
            elif v.et.name == "ObjectProperty":
                for i in range(len(v.items)):
                    yield (lambda i=i: v.items[i], lambda x, i=i: v.items.__setitem__(i, x))
        elif t == "MapProperty" and isinstance(v, Node):
            for i, (k, val) in enumerate(v.pairs):
                if v.kt.name == "ObjectProperty":
                    yield (lambda i=i: v.pairs[i][0], lambda x, i=i: v.pairs.__setitem__(i, (x, v.pairs[i][1])))
                if v.vt.name == "ObjectProperty":
                    yield (lambda i=i: v.pairs[i][1], lambda x, i=i: v.pairs.__setitem__(i, (v.pairs[i][0], x)))
                for x, xt in ((k, v.kt.name), (val, v.vt.name)):
                    if xt == "StructProperty" and isinstance(x, Node) and x.kind == "Bag":
                        yield from bag_refs(x)

    def bag_refs(bag):
        for p in bag.props:
            if p.type.name == "ObjectProperty":
                yield (lambda p=p: p.value, lambda x, p=p: setattr(p, "value", x))
            else:
                yield from visit(p.value, p.type.name)

    for o in sd.objects:
        if o.props is not None:
            yield from bag_refs(o.props)
        for c in o.comps or ():
            if c.data.kind == "Bag":
                yield from bag_refs(c.data)


def remove_objects(sd, doomed):
    """Removes objects (by index) from sd and renumbers every reference to the rest.
    Raises if anything still references a removed object."""
    doomed = set(doomed)
    if not doomed:
        return
    if 0 in doomed:
        raise ValueError("refusing to remove object 0")
    remap, n = {}, 0
    for i in range(len(sd.objects)):
        if i not in doomed:
            remap[i] = n
            n += 1
    refs = list(object_refs(sd))
    for get, _ in refs:
        if get() in doomed:
            raise ValueError(f"object {get()} is still referenced; remove its references first")
    for get, put in refs:
        if get() >= 0:
            put(remap[get()])
    sd.objects = [o for i, o in enumerate(sd.objects) if i not in doomed]
    for i, o in enumerate(sd.objects):
        o.index = i
