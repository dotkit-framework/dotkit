"""Small Mach-O files for the tests of audit/macho.py and the binaries source: one segment with the sections
that the test gives, thin or in a universal binary."""

from __future__ import annotations

import json
import struct
import zlib
from typing import Dict, List, Tuple

CPU_ARM64 = 0x0100000C
CPU_X86_64 = 0x01000007


def macho(sections: Dict[str, bytes], cpu: int = CPU_ARM64) -> bytes:
    nsects = len(sections)
    cmdsize = 72 + 80 * nsects
    header = struct.pack("<IiiIIIII", 0xFEEDFACF, cpu, 0, 2, 1, cmdsize, 0, 0)
    offset, payload, sects = 32 + cmdsize, b"", b""
    for name, data in sections.items():
        sects += struct.pack("<16s16sQQIIIIIIII", name.encode(), b"__DATA", 0, len(data), offset + len(payload),
                             0, 0, 0, 0, 0, 0, 0)
        payload += data
    segment = struct.pack("<II16sQQQQiiII", 0x19, cmdsize, b"__DATA", 0, 0, 0, 0, 0, 0, nsects, 0)
    return header + segment + sects + payload


def universal(slices: List[Tuple[int, bytes]]) -> bytes:
    """A universal binary: each (cpu, thin image) at a 4096-byte boundary."""
    head = struct.pack(">II", 0xCAFEBABE, len(slices))
    offset, body = 4096, b""
    for cpu, image in slices:
        head += struct.pack(">iiIII", cpu, 0, offset + len(body), len(image), 12)
        body += image + b"\0" * (-len(image) % 4096)
    return head + b"\0" * (4096 - len(head)) + body


def _varint(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | 0x80 if n else b])
        if not n:
            return out


def go_buildinfo(version: str, modules: List[Tuple[str, str, str]]) -> bytes:
    """__go_buildinfo of Go 1.18 and newer: version (go1.25.3), and lines (kind, path, version):
    mod, dep, or =>."""
    text = "".join(f"{k}\t{p}\t{v}\th1:x=\n" for k, p, v in modules).encode()
    info = b"0" * 16 + text + b"1" * 16
    head = b"\xff Go buildinf:" + bytes([8, 2]) + b"\0" * 16
    return head + _varint(len(version)) + version.encode() + _varint(len(info)) + info


def rust_deps(packages: List[dict]) -> bytes:
    return zlib.compress(json.dumps({"packages": packages}).encode())


def go_functions(names: List[str]) -> bytes:
    """A __gopclntab with the function names, NUL-terminated."""
    return b"\0" + b"\0".join(n.encode() for n in names) + b"\0"
