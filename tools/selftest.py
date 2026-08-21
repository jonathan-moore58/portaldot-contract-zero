#!/usr/bin/env python3
"""
Self-test for portawasm, with hand-built WASM fixtures.

Two modules are assembled byte by byte:

  good.wasm  what a correct PortalDot contract looks like — env.memory imported,
             exactly deploy+call exported, MVP instructions only.
  bad.wasm   what rustc actually emits before repair — internal memory, extra
             exports (memory, __data_end, __heap_base), and an i32.extend8_s
             sign-extension opcode from LLVM.

`check` must accept the first and reject the second for the right reasons, and
`fix` must turn the second into something `check` accepts.

    python tools/selftest.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "out")


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


def vec(items):
    return uleb(len(items)) + b"".join(items)


def name(s):
    b = s.encode()
    return uleb(len(b)) + b


def section(sid, payload):
    return bytes([sid]) + uleb(len(payload)) + payload


# type 0: () -> ()        type 1: (i32,i32,i32) -> ()
TYPES = vec([
    b"\x60" + vec([]) + vec([]),
    b"\x60" + vec([b"\x7f", b"\x7f", b"\x7f"]) + vec([]),
])

IMPORT_SEAL = name("seal0") + name("seal_return") + b"\x00" + uleb(1)
IMPORT_MEM = name("env") + name("memory") + b"\x02" + b"\x01" + uleb(2) + uleb(16)

FUNCS = vec([uleb(0), uleb(0)])            # two functions, both type 0

# i32.const 0 ×3 ; call 0 ; end
BODY_OK = b"\x41\x00\x41\x00\x41\x00\x10\x00\x0b"
# same, but with i32.extend8_s (0xC0) spliced in — post-MVP, LLVM emits these
BODY_SIGNEXT = b"\x41\x00\xc0\x1a\x41\x00\x41\x00\x41\x00\x10\x00\x0b"


def code_section(bodies):
    entries = []
    for b in bodies:
        payload = vec([]) + b                # no locals
        entries.append(uleb(len(payload)) + payload)
    return vec(entries)


def build_good():
    return (
        b"\x00asm\x01\x00\x00\x00"
        + section(1, TYPES)
        + section(2, vec([IMPORT_SEAL, IMPORT_MEM]))
        + section(3, FUNCS)
        + section(7, vec([
            name("deploy") + b"\x00" + uleb(1),
            name("call") + b"\x00" + uleb(2),
        ]))
        + section(10, code_section([BODY_OK, BODY_OK]))
    )


def build_bad():
    return (
        b"\x00asm\x01\x00\x00\x00"
        + section(1, TYPES)
        + section(2, vec([IMPORT_SEAL]))
        + section(3, FUNCS)
        + section(5, vec([b"\x01" + uleb(2) + uleb(32)]))   # internal memory, 32 pages
        + section(7, vec([
            name("memory") + b"\x02" + uleb(0),
            name("deploy") + b"\x00" + uleb(1),
            name("call") + b"\x00" + uleb(2),
            name("__data_end") + b"\x03" + uleb(0),
            name("__heap_base") + b"\x03" + uleb(1),
        ]))
        + section(6, vec([
            b"\x7f\x00" + b"\x41\x80\x80\x04\x0b",
            b"\x7f\x00" + b"\x41\x80\x80\x04\x0b",
        ]))
        + section(10, code_section([BODY_OK, BODY_SIGNEXT]))
    )


def run(*args):
    cmd = [sys.executable, os.path.join(HERE, "portawasm.py")] + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def main():
    os.makedirs(OUT, exist_ok=True)
    good = os.path.join(OUT, "selftest-good.wasm")
    bad = os.path.join(OUT, "selftest-bad.wasm")
    fixed = os.path.join(OUT, "selftest-fixed.wasm")

    open(good, "wb").write(build_good())
    open(bad, "wb").write(build_bad())

    failures = []

    print("=" * 68)
    print("1. a correct module must be ACCEPTED")
    print("=" * 68)
    rc, out = run("check", good)
    print(out)
    if rc != 0:
        failures.append("check rejected a valid module")

    print("=" * 68)
    print("2. raw rustc output must be REJECTED, with the real reasons")
    print("=" * 68)
    rc, out = run("check", bad)
    print(out)
    if rc == 0:
        failures.append("check accepted an invalid module")
    for expect in ("internal memory", "unknown export", "non-MVP opcode 0xC0"):
        if expect not in out:
            failures.append("check did not report %r" % expect)
    if "target-feature=-sign-ext" not in out:
        failures.append("check did not suggest the sign-ext fix")

    print("=" * 68)
    print("3. fix must repair it")
    print("=" * 68)
    rc, out = run("fix", bad, fixed)
    print(out)

    rc, out = run("check", fixed)
    print(out)
    # the sign-ext opcode is a compiler problem, not something a patcher should
    # silently rewrite — fix must not pretend to have solved it
    if "non-MVP opcode 0xC0" not in out:
        failures.append("fix wrongly claimed to resolve a sign-ext opcode")
    if "internal memory" in out:
        failures.append("fix failed to move memory to an import")
    if "unknown export" in out:
        failures.append("fix failed to strip extra exports")

    print("=" * 68)
    if failures:
        print("SELFTEST FAILED")
        for f in failures:
            print("  ✗ %s" % f)
        return 1
    print("SELFTEST PASSED")
    print()
    print("  · structural problems (memory, exports) are repairable after the fact")
    print("  · sign-ext is not — it has to come out of the compiler correctly,")
    print("    which is what contracts/minimal/.cargo/config.toml is for")
    return 0


if __name__ == "__main__":
    sys.exit(main())
