"""Read-only parser for unpacked Remnant 2 saves (port of AndrewSav/lib.remnant2.saves).

Every scalar value is a Val that remembers its absolute offset in the unpacked
buffer, so fixed-size numbers can be patched in place without re-serializing.
"""
import struct


class Val:
    __slots__ = ("type", "value", "off", "fmt")

    def __init__(self, type_, value, off, fmt):
        self.type, self.value, self.off, self.fmt = type_, value, off, fmt

    def __repr__(self):
        return f"{self.value!r}"


class R:
    def __init__(self, buf, base=0):
        self.buf, self.pos, self.base = buf, 0, base

    def u(self, fmt):
        v = struct.unpack_from("<" + fmt, self.buf, self.pos)
        self.pos += struct.calcsize("<" + fmt)
        return v[0] if len(v) == 1 else v

    def val(self, type_, fmt):
        off = self.base + self.pos
        return Val(type_, self.u(fmt), off, fmt)

    def bytes(self, n):
        b = self.buf[self.pos:self.pos + n]
        self.pos += n
        return b

    def fstr(self):
        n = self.u("i")
        if n == 0:
            return None
        if n < 0:
            s = self.bytes(-n * 2)[:-2].decode("utf-16-le")
        else:
            s = self.bytes(n)[:-1].decode("ascii")
        return s


class Ctx:
    def __init__(self, names, class_path):
        self.names, self.class_path, self.objects = names, class_path, None


def fname(r, ctx):
    i = r.u("H")
    num = None
    if i & 0x8000:
        i &= 0x7FFF
        num = r.u("i")
    return ctx.names[i]


def prop_guid(r):
    if r.u("B"):
        r.bytes(16)


SIMPLE = {"IntProperty": "i", "Int16Property": "h", "Int64Property": "q", "UInt64Property": "Q",
          "FloatProperty": "f", "DoubleProperty": "d", "UInt16Property": "H", "UInt32Property": "I"}


def read_value(r, ctx, type_, raw=True):
    if type_ in SIMPLE:
        if not raw:
            prop_guid(r)
        return r.val(type_, SIMPLE[type_])
    if type_ in ("StrProperty", "SoftClassPath", "SoftObjectProperty"):
        if not raw:
            prop_guid(r)
        return r.fstr()
    if type_ == "BoolProperty":
        v = r.val(type_, "B")
        if not raw:
            prop_guid(r)
        return v
    if type_ == "NameProperty":
        if not raw:
            prop_guid(r)
        return fname(r, ctx)
    if type_ == "ByteProperty":
        if raw:
            return r.val(type_, "B")
        enum = fname(r, ctx)
        prop_guid(r)
        return r.val(type_, "B") if enum == "None" else fname(r, ctx)
    if type_ == "StructProperty":
        if raw:
            return r.bytes(16)
        stype = fname(r, ctx)
        r.bytes(16)
        prop_guid(r)
        return read_struct(r, ctx, stype)
    if type_ == "ObjectProperty":
        if not raw:
            prop_guid(r)
        idx = r.val("ObjectIndex", "i")
        path = ctx.objects[idx.value]["path"] if idx.value != -1 and ctx.objects else None
        return {"_obj": path, "_idx": idx}
    if type_ == "EnumProperty":
        fname(r, ctx)
        prop_guid(r)
        return fname(r, ctx)
    if type_ == "MapProperty":
        kt, vt = fname(r, ctx), fname(r, ctx)
        prop_guid(r)
        for _ in range(r.u("i")):
            read_value(r, ctx, kt)
        return [(read_value(r, ctx, kt), read_value(r, ctx, vt)) for _ in range(r.u("i"))]
    if type_ == "TextProperty":
        if not raw:
            prop_guid(r)
        r.u("I")
        h = r.u("B")
        if h == 0:
            r.fstr(); r.fstr()
            return r.fstr()
        if h == 255:
            return r.fstr() if r.u("I") else None
        raise ValueError("text history")
    if type_ == "ArrayProperty":
        et = fname(r, ctx)
        prop_guid(r)
        count = r.u("I")
        if et == "StructProperty":
            r.u("H"); r.u("H"); r.u("I"); r.u("I")
            st = fname(r, ctx)
            r.bytes(16)
            prop_guid(r)
            return [read_struct(r, ctx, st) for _ in range(count)]
        return [read_value(r, ctx, et) for _ in range(count)]
    raise ValueError(f"unknown property type {type_} at {r.base + r.pos:#x}")


def read_struct(r, ctx, stype):
    if stype in ("SoftClassPath", "SoftObjectPath"):
        return r.fstr()
    if stype in ("Timespan", "DateTime"):
        return r.val(stype, "q")
    if stype == "Guid":
        return r.bytes(16)
    if stype in ("Vector", "Rotator"):
        return r.u("ddd")
    if stype == "PersistenceBlob":
        n = r.u("i")
        sub = R(r.bytes(n), r.base + r.pos - n)
        if ctx.class_path == "/Game/_Core/Blueprints/Base/BP_RemnantSaveGameProfile":
            return read_savedata(sub, True, False)
        return read_container(sub)
    return read_bag(r, ctx)


def read_bag(r, ctx):
    bag = {}
    while True:
        name = fname(r, ctx)
        if name == "None":
            return bag
        ptype = fname(r, ctx)
        size = r.u("I")
        r.u("I")
        if name == "FowVisitedCoordinates":
            r.bytes(size + 19)
            continue
        v = read_value(r, ctx, ptype, raw=False)
        bag.setdefault(name, v)


def read_variables(r, ctx):
    fname(r, ctx)
    r.u("Q")
    out = {}
    for _ in range(r.u("i")):
        n = fname(r, ctx)
        t = r.u("B")
        if t in (1, 2):
            out[n] = r.val("Var", "I")
        elif t == 3:
            out[n] = r.val("Var", "f")
        elif t == 4:
            out[n] = fname(r, ctx)
        else:
            out[n] = None
    return out


VAR_KEYS = {"GlobalVariables", "Variables", "Variable", "PersistenceKeys", "PersistanceKeys1", "PersistenceKeys1"}


def read_savedata(r, has_pkg=True, has_path=True):
    if has_pkg:
        r.u("II")
    class_path = None
    if has_path:
        class_path = r.fstr()
        r.fstr()
    names_off, _ver, objs_off = r.u("qIq")
    data_off = r.pos
    r.pos = names_off
    names = [r.fstr() for _ in range(r.u("i"))]
    ctx = Ctx(names, class_path)
    r.pos = objs_off
    objs = []
    for i in range(r.u("i")):
        loaded = r.u("B")
        path = class_path if loaded and i == 0 and class_path else r.fstr()
        o = {"path": path}
        if not loaded:
            o["name"] = fname(r, ctx)
            r.u("I")
        objs.append(o)
    ctx.objects = objs
    r.pos = data_off
    for o in objs:
        r.u("i")
        ln = r.u("I")
        start = r.pos
        if ln:
            o["props"] = read_bag(r, ctx)
            r.pos = start + ln
        if r.u("B"):
            comps = {}
            for _ in range(r.u("I")):
                key = r.fstr()
                ln = r.u("i")
                s = r.pos
                comps[key] = read_variables(r, ctx) if key in VAR_KEYS else read_bag(r, ctx)
                r.pos = s + ln
            o["components"] = comps
    return {"objects": objs}


def read_container(r):
    r.u("I")
    index_off, dyn_off = r.u("ii")
    r.pos = index_off
    infos = [r.u("QiI") for _ in range(r.u("I"))]
    actors = []
    for uid, off, size in infos:
        sub = R(r.buf[off:off + size], r.base + off)
        if sub.u("I"):
            sub.bytes(8 * 10)
        actors.append({"uid": uid, "archive": read_savedata(sub, False, False)})
    return {"actors": actors}


def parse(full):
    r = R(full)
    r.u("IiIi")
    return read_savedata(r)
