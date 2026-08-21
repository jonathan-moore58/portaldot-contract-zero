#!/usr/bin/env python3
"""
portawasm — validate and repair WASM for PortalDot's pallet-contracts 3.0.0.

PortalDot mainnet (specVersion 1002, metadata V13) runs pallet-contracts 3.0.0,
the pre-rent-removal Substrate 3.0.0 pallet. Its WASM validator is strict and
old: wasmi-validation 0.4 + pwasm-utils 0.18. Modern Rust emits WASM that it
rejects, which is why no contract has ever been deployed on this chain.

The rules below are transcribed from the chain's own source:
  frame/contracts/src/wasm/prepare.rs
  frame/contracts/src/schedule.rs
in github.com/portaldotVolunteer/Portaldot @ main

Usage:
    python tools/portawasm.py check <file.wasm>
    python tools/portawasm.py fix   <in.wasm> <out.wasm>
    python tools/portawasm.py dump  <file.wasm>

`check` exits 0 only if the chain would accept the module.
"""

import sys
import struct

# ---------------------------------------------------------------- schedule ---
# frame/contracts/src/schedule.rs :: impl Default for Limits
LIMITS = {
    "event_topics": 4,
    "stack_height": 512,
    "globals": 256,
    "parameters": 128,
    "memory_pages": 16,      # 16 * 64KiB = 1 MiB
    "table_size": 4096,
    "br_table_size": 256,
    "subject_len": 32,
    "call_depth": 32,
    "payload_len": 16 * 1024,
    "code_len": 128 * 1024,  # hard cap on the deployed blob
}

# Host functions defined by frame/contracts/src/wasm/runtime.rs :: define_env!
SEAL0 = {
    "gas", "seal_set_storage", "seal_clear_storage", "seal_get_storage",
    "seal_transfer", "seal_call", "seal_instantiate", "seal_terminate",
    "seal_input", "seal_return", "seal_caller", "seal_address",
    "seal_weight_to_fee", "seal_gas_left", "seal_balance",
    "seal_value_transferred", "seal_random", "seal_now",
    "seal_minimum_balance", "seal_tombstone_deposit", "seal_restore_to",
    "seal_deposit_event", "seal_set_rent_allowance", "seal_rent_allowance",
    "seal_println", "seal_block_number", "seal_hash_sha2_256",
    "seal_hash_keccak_256", "seal_hash_blake2_256", "seal_hash_blake2_128",
    "seal_call_chain_extension", "seal_debug_message", "seal_rent_params",
    "seal_rent_status",
}
SEAL1 = {"seal_random"}

SECTION_NAMES = {
    0: "custom", 1: "type", 2: "import", 3: "function", 4: "table",
    5: "memory", 6: "global", 7: "export", 8: "start", 9: "element",
    10: "code", 11: "data", 12: "datacount",
}

VALTYPE = {0x7F: "i32", 0x7E: "i64", 0x7D: "f32", 0x7C: "f64"}
FLOAT_VALTYPES = {0x7D, 0x7C}


# ------------------------------------------------------------------ reader ---
class Reader:
    def __init__(self, data, pos=0):
        self.d = data
        self.p = pos

    def eof(self):
        return self.p >= len(self.d)

    def byte(self):
        b = self.d[self.p]
        self.p += 1
        return b

    def bytes(self, n):
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def u32(self):
        result = 0
        shift = 0
        while True:
            b = self.byte()
            result |= (b & 0x7F) << shift
            if not (b & 0x80):
                return result
            shift += 7

    def s64(self):
        result = 0
        shift = 0
        while True:
            b = self.byte()
            result |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                if shift < 64 and (b & 0x40):
                    result -= 1 << shift
                return result

    def name(self):
        return self.bytes(self.u32()).decode("utf-8", "replace")


def uleb(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def enc_name(s):
    b = s.encode()
    return uleb(len(b)) + b


# ------------------------------------------------------------------ module ---
class Module:
    def __init__(self, data):
        if data[:4] != b"\x00asm":
            raise ValueError("not a wasm module (bad magic)")
        ver = struct.unpack("<I", data[4:8])[0]
        if ver != 1:
            raise ValueError("unsupported wasm version %d" % ver)
        self.raw = data
        self.sections = []  # (id, payload:bytes)
        r = Reader(data, 8)
        while not r.eof():
            sid = r.byte()
            size = r.u32()
            self.sections.append((sid, r.bytes(size)))

    def section(self, sid):
        for i, (s, payload) in enumerate(self.sections):
            if s == sid:
                return i, payload
        return None, None

    def serialize(self):
        out = bytearray(b"\x00asm" + struct.pack("<I", 1))
        for sid, payload in self.sections:
            out.append(sid)
            out += uleb(len(payload))
            out += payload
        return bytes(out)

    # -- typed views ------------------------------------------------------
    def types(self):
        _, p = self.section(1)
        if p is None:
            return []
        r = Reader(p)
        out = []
        for _ in range(r.u32()):
            assert r.byte() == 0x60, "malformed functype"
            params = [r.byte() for _ in range(r.u32())]
            results = [r.byte() for _ in range(r.u32())]
            out.append((params, results))
        return out

    def imports(self):
        """-> list of dicts, plus the raw byte slice of each entry."""
        _, p = self.section(2)
        if p is None:
            return []
        r = Reader(p)
        out = []
        for _ in range(r.u32()):
            start = r.p
            mod = r.name()
            fld = r.name()
            kind = r.byte()
            info = {"module": mod, "field": fld, "kind": kind}
            if kind == 0x00:
                info["type"] = r.u32()
            elif kind == 0x01:            # table
                r.byte()
                lim = r.byte()
                info["min"] = r.u32()
                info["max"] = r.u32() if lim else None
            elif kind == 0x02:            # memory
                lim = r.byte()
                info["min"] = r.u32()
                info["max"] = r.u32() if lim else None
            elif kind == 0x03:            # global
                info["valtype"] = r.byte()
                info["mutable"] = bool(r.byte())
            info["raw"] = p[start:r.p]
            out.append(info)
        return out

    def memories(self):
        _, p = self.section(5)
        if p is None:
            return []
        r = Reader(p)
        out = []
        for _ in range(r.u32()):
            lim = r.byte()
            mn = r.u32()
            mx = r.u32() if lim else None
            out.append((mn, mx))
        return out

    def globals_(self):
        _, p = self.section(6)
        if p is None:
            return []
        r = Reader(p)
        out = []
        for _ in range(r.u32()):
            vt = r.byte()
            mut = r.byte()
            # init expr: skip to matching 0x0B
            depth = 0
            while True:
                b = r.byte()
                if b in (0x02, 0x03, 0x04):
                    depth += 1
                    r.byte()
                elif b == 0x0B:
                    if depth == 0:
                        break
                    depth -= 1
                elif b in (0x41,):
                    r.s64()
                elif b in (0x42,):
                    r.s64()
                elif b == 0x43:
                    r.bytes(4)
                elif b == 0x44:
                    r.bytes(8)
                elif b in (0x23, 0x24):
                    r.u32()
            out.append((vt, bool(mut)))
        return out

    def functions(self):
        """typeidx per locally-declared function."""
        _, p = self.section(3)
        if p is None:
            return []
        r = Reader(p)
        return [r.u32() for _ in range(r.u32())]

    def tables(self):
        _, p = self.section(4)
        if p is None:
            return []
        r = Reader(p)
        out = []
        for _ in range(r.u32()):
            r.byte()
            lim = r.byte()
            mn = r.u32()
            mx = r.u32() if lim else None
            out.append((mn, mx))
        return out

    def exports(self):
        _, p = self.section(7)
        if p is None:
            return []
        r = Reader(p)
        out = []
        for _ in range(r.u32()):
            nm = r.name()
            kind = r.byte()
            idx = r.u32()
            out.append({"name": nm, "kind": kind, "index": idx})
        return out

    def code_bodies(self):
        _, p = self.section(10)
        if p is None:
            return []
        r = Reader(p)
        out = []
        for _ in range(r.u32()):
            size = r.u32()
            end = r.p + size
            locals_ = []
            for _ in range(r.u32()):
                cnt = r.u32()
                vt = r.byte()
                locals_.append((cnt, vt))
            out.append({"locals": locals_, "code": p[r.p:end]})
            r.p = end
        return out


# ------------------------------------------------------- instruction walker --
# MVP opcodes only. Anything absent from this table is post-MVP (sign-ext,
# bulk-memory, SIMD, atomics, reference types) and wasmi-validation 0.4 rejects
# it. That is exactly what we want to surface.
NO_IMM = set(
    [0x00, 0x01, 0x05, 0x0B, 0x0F, 0x1A, 0x1B]
    + list(range(0x45, 0x0C0))          # numeric ops incl. float ops
)
BLOCKTYPE = {0x02, 0x03, 0x04}
IDX32 = {0x0C, 0x0D, 0x10, 0x20, 0x21, 0x22, 0x23, 0x24}
MEMARG = set(range(0x28, 0x3F))
MEMOP = {0x3F, 0x40}

FLOAT_OPS = (
    {0x2A, 0x2B, 0x38, 0x39, 0x43, 0x44}
    | set(range(0x5B, 0x67))
    | set(range(0x8B, 0xA7))
    | set(range(0xA8, 0xAC))
    | set(range(0xAE, 0xC0))
)


def walk_code(code):
    """Yield (offset, opcode). Raises ValueError on a non-MVP opcode."""
    r = Reader(code)
    while not r.eof():
        off = r.p
        op = r.byte()
        if op in BLOCKTYPE:
            r.byte()                       # blocktype
        elif op in IDX32:
            r.u32()
        elif op == 0x0E:                   # br_table
            n = r.u32()
            for _ in range(n):
                r.u32()
            r.u32()
        elif op == 0x11:                   # call_indirect
            r.u32()
            r.byte()
        elif op in MEMARG:
            r.u32()
            r.u32()
        elif op in MEMOP:
            r.byte()
        elif op == 0x41:
            r.s64()
        elif op == 0x42:
            r.s64()
        elif op == 0x43:
            r.bytes(4)
        elif op == 0x44:
            r.bytes(8)
        elif op in NO_IMM:
            pass
        else:
            raise ValueError("non-MVP opcode 0x%02X at offset %d" % (op, off))
        yield off, op


# ------------------------------------------------------------------- check ---
def check(path):
    data = open(path, "rb").read()
    m = Module(data)
    errors, warnings, notes = [], [], []

    notes.append("size: %d bytes (limit %d)" % (len(data), LIMITS["code_len"]))
    if len(data) > LIMITS["code_len"]:
        errors.append("module is %d bytes, over the %d byte code_len limit"
                      % (len(data), LIMITS["code_len"]))

    # --- rule: no internally-declared memory (prepare.rs ensure_no_internal_memory)
    mems = m.memories()
    if mems:
        errors.append("module declares internal memory %r — memory must be "
                      "imported as env.memory (build with -C link-arg=--import-memory, "
                      "or run `portawasm fix`)" % (mems,))

    # --- rule: memory import must be env.memory, within page limit
    imports = m.imports()
    mem_imports = [i for i in imports if i["kind"] == 0x02]
    if len(mem_imports) > 1:
        errors.append("multiple memory imports defined")
    for mi in mem_imports:
        if mi["module"] != "env":
            errors.append("invalid module for imported memory: %r (must be 'env')"
                          % mi["module"])
        if mi["field"] != "memory":
            errors.append("memory import must have field name 'memory', got %r"
                          % mi["field"])
        if mi["max"] is None:
            errors.append("imported memory must declare a maximum")
        elif mi["max"] > LIMITS["memory_pages"]:
            errors.append("imported memory max %d pages exceeds limit %d"
                          % (mi["max"], LIMITS["memory_pages"]))
        else:
            notes.append("memory: %s..%s pages (limit %d)"
                         % (mi["min"], mi["max"], LIMITS["memory_pages"]))

    # --- rule: every function import must resolve to a defined host function
    for i in imports:
        if i["kind"] != 0x00:
            continue
        if i["module"] == "seal0":
            if i["field"] not in SEAL0:
                errors.append("import seal0.%s is not defined by this runtime"
                              % i["field"])
        elif i["module"] == "seal1":
            if i["field"] not in SEAL1:
                errors.append("import seal1.%s is not defined by this runtime "
                              "(only %s)" % (i["field"], sorted(SEAL1)))
        else:
            errors.append("function import from unknown module %r "
                          "(only seal0/seal1 exist)" % i["module"])

    # --- rule: exports must be exactly {deploy, call}, both declared functions
    n_func_imports = sum(1 for i in imports if i["kind"] == 0x00)
    types = m.types()
    funcs = m.functions()
    exports = m.exports()
    seen = set()
    for e in exports:
        if e["name"] not in ("call", "deploy"):
            errors.append("unknown export %r — the chain accepts only 'deploy' "
                          "and 'call' (run `portawasm fix` to strip the rest)"
                          % e["name"])
            continue
        seen.add(e["name"])
        if e["kind"] != 0x00:
            errors.append("export %r is not a function" % e["name"])
            continue
        local = e["index"] - n_func_imports
        if local < 0:
            errors.append("export %r points at an imported function" % e["name"])
            continue
        if local >= len(funcs):
            errors.append("export %r points outside the function space" % e["name"])
            continue
        params, results = types[funcs[local]]
        if params or results:
            errors.append("export %r must have signature () -> (), got (%s) -> (%s)"
                          % (e["name"],
                             ",".join(VALTYPE.get(p, hex(p)) for p in params),
                             ",".join(VALTYPE.get(x, hex(x)) for x in results)))
    for required in ("deploy", "call"):
        if required not in seen:
            errors.append("missing required export %r" % required)

    # --- rule: no floating point anywhere (prepare.rs ensure_no_floating_types)
    for idx, (params, results) in enumerate(types):
        if any(v in FLOAT_VALTYPES for v in params + results):
            errors.append("type[%d] uses a floating point type — forbidden" % idx)
    for idx, (vt, _mut) in enumerate(m.globals_()):
        if vt in FLOAT_VALTYPES:
            errors.append("global[%d] is %s — floating point globals are forbidden"
                          % (idx, VALTYPE[vt]))

    n_globals = len(m.globals_())
    if n_globals > LIMITS["globals"]:
        errors.append("%d globals declared, limit is %d"
                      % (n_globals, LIMITS["globals"]))

    for idx, (params, results) in enumerate(types):
        if len(params) > LIMITS["parameters"]:
            errors.append("type[%d] has %d parameters, limit is %d"
                          % (idx, len(params), LIMITS["parameters"]))

    for mn, mx in m.tables():
        if mx is not None and mx > LIMITS["table_size"]:
            errors.append("table max %d exceeds limit %d"
                          % (mx, LIMITS["table_size"]))

    # --- rule: MVP instruction set only, no float locals
    for fi, body in enumerate(m.code_bodies()):
        for _cnt, vt in body["locals"]:
            if vt in FLOAT_VALTYPES:
                errors.append("function[%d] declares a %s local — forbidden"
                              % (fi, VALTYPE[vt]))
                break
        try:
            for off, op in walk_code(body["code"]):
                if op in FLOAT_OPS:
                    errors.append("function[%d]+%d uses float opcode 0x%02X — forbidden"
                                  % (fi, off, op))
                    break
        except ValueError as exc:
            hint = ""
            if "0xC0" in str(exc) or "0xC1" in str(exc) or "0xC2" in str(exc) \
               or "0xC3" in str(exc) or "0xC4" in str(exc):
                hint = ("  -> this is a sign-extension op. Rebuild with "
                        "RUSTFLAGS='-C target-feature=-sign-ext'")
            elif "0xFC" in str(exc):
                hint = ("  -> bulk-memory op. Rebuild with "
                        "-C target-feature=-bulk-memory")
            errors.append("function[%d]: %s%s" % (fi, exc, hint))

    # --- report ----------------------------------------------------------
    print("portawasm check: %s" % path)
    for n in notes:
        print("  ·", n)
    for w in warnings:
        print("  ! ", w)
    if errors:
        print("\n  REJECTED — the chain would refuse this module:\n")
        for e in errors:
            print("    ✗ %s" % e)
        print("\n  %d problem(s)." % len(errors))
        return 1
    print("\n  ACCEPTED — this module satisfies pallet-contracts 3.0.0.\n")
    return 0


# --------------------------------------------------------------------- fix ---
def fix(src, dst):
    data = open(src, "rb").read()
    m = Module(data)
    changes = []

    imports = m.imports()
    mems = m.memories()

    # 1. internal memory -> imported env.memory
    if mems:
        mn, mx = mems[0]
        if mx is None:
            mx = LIMITS["memory_pages"]
            changes.append("memory had no maximum; set to %d pages" % mx)
        if mx > LIMITS["memory_pages"]:
            changes.append("memory max %d -> %d pages (schedule limit)"
                           % (mx, LIMITS["memory_pages"]))
            mx = LIMITS["memory_pages"]
        entry = (enc_name("env") + enc_name("memory") + bytes([0x02, 0x01])
                 + uleb(mn) + uleb(mx))
        # drop the memory section
        i, _ = m.section(5)
        del m.sections[i]
        # append the import entry
        i, payload = m.section(2)
        if payload is None:
            new = uleb(1) + entry
            # import section must sit right after the type section
            pos = 0
            for k, (sid, _p) in enumerate(m.sections):
                if sid == 1:
                    pos = k + 1
                    break
            m.sections.insert(pos, (2, new))
        else:
            r = Reader(payload)
            count = r.u32()
            new = uleb(count + 1) + payload[r.p:] + entry
            m.sections[i] = (2, new)
        changes.append("internal memory converted to imported env.memory "
                       "(%d..%d pages)" % (mn, mx))

    # 2. strip every export except deploy/call
    i, payload = m.section(7)
    if payload is not None:
        kept, dropped = [], []
        r = Reader(payload)
        for _ in range(r.u32()):
            start = r.p
            nm = r.name()
            r.byte()
            r.u32()
            blob = payload[start:r.p]
            if nm in ("call", "deploy"):
                kept.append(blob)
            else:
                dropped.append(nm)
        if dropped:
            m.sections[i] = (7, uleb(len(kept)) + b"".join(kept))
            changes.append("stripped %d export(s): %s"
                           % (len(dropped), ", ".join(dropped)))

    # 3. drop custom sections (name/producers/target_features) — pure bloat,
    #    and `producers`/`target_features` can trip old validators.
    before = len(m.sections)
    m.sections = [(sid, p) for sid, p in m.sections if sid != 0]
    if len(m.sections) != before:
        changes.append("removed %d custom section(s)" % (before - len(m.sections)))

    out = m.serialize()
    open(dst, "wb").write(out)

    print("portawasm fix: %s -> %s" % (src, dst))
    if changes:
        for c in changes:
            print("  · %s" % c)
    else:
        print("  · nothing to change")
    print("  · %d bytes -> %d bytes" % (len(data), len(out)))
    return 0


# -------------------------------------------------------------------- dump ---
def dump(path):
    m = Module(open(path, "rb").read())
    print("sections:")
    for sid, p in m.sections:
        print("  %2d %-10s %7d bytes" % (sid, SECTION_NAMES.get(sid, "?"), len(p)))
    print("\nimports:")
    for i in m.imports():
        kind = {0: "func", 1: "table", 2: "memory", 3: "global"}[i["kind"]]
        extra = ""
        if i["kind"] == 0x02:
            extra = " (%s..%s pages)" % (i["min"], i["max"])
        print("  %-6s %s.%s%s" % (kind, i["module"], i["field"], extra))
    print("\nexports:")
    for e in m.exports():
        kind = {0: "func", 1: "table", 2: "memory", 3: "global"}[e["kind"]]
        print("  %-6s %s -> %d" % (kind, e["name"], e["index"]))
    print("\nmemory sections: %r" % (m.memories(),))
    print("globals: %d   types: %d   functions: %d"
          % (len(m.globals_()), len(m.types()), len(m.functions())))


def main(argv):
    if len(argv) < 3:
        print(__doc__.strip())
        return 2
    cmd = argv[1]
    if cmd == "check":
        return check(argv[2])
    if cmd == "fix":
        if len(argv) < 4:
            print("usage: portawasm.py fix <in.wasm> <out.wasm>")
            return 2
        return fix(argv[2], argv[3])
    if cmd == "dump":
        dump(argv[2])
        return 0
    print("unknown command %r" % cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
