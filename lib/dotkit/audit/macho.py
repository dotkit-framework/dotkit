"""What a macOS executable says about the packages that it was built from: Mach-O sections.

- Go writes its version and the modules of the build into the section __go_buildinfo, and the names of
  its functions into __gopclntab. The names tell whether a binary has the functions of an advisory, as
  govulncheck checks a binary.
- Rust binaries that cargo-auditable builds (Nixpkgs builds them so) have the crates of the build as
  zlib-compressed JSON in the section .dep-v0.

It reads the header and only the sections that it needs, never the whole file. A universal binary gives
its arm64 part. A file that is not Mach-O, or that it cannot read, gives nothing.
"""

from __future__ import annotations

import json
import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Dict, List, Optional, Tuple

MH_MAGIC_64 = b"\xcf\xfa\xed\xfe"
FAT_MAGIC = b"\xca\xfe\xba\xbe"
FAT_MAGIC_64 = b"\xca\xfe\xba\xbf"
CPU_ARM64 = 0x0100000C
CPU_X86_64 = 0x01000007
LC_SEGMENT_64 = 0x19
GO_MAGIC = b"\xff Go buildinf:"
# The most bytes of a section that dotkit reads: a function name table of a large Go binary is about 20 MB.
MAX_SECTION = 64 << 20


def _slice(f: BinaryIO) -> Optional[int]:
    """The offset of the 64-bit Mach-O image in the file: 0, or the arm64 (else x86_64) part of a universal
    binary."""
    head = f.read(8)
    if head[:4] == MH_MAGIC_64:
        return 0
    if head[:4] not in (FAT_MAGIC, FAT_MAGIC_64) or len(head) < 8:
        return None
    n = struct.unpack(">I", head[4:8])[0]
    wide = head[:4] == FAT_MAGIC_64
    size = 32 if wide else 20
    arches: Dict[int, int] = {}
    for i in range(min(n, 32)):
        raw = f.read(size)
        if len(raw) < size:
            return None
        cpu = struct.unpack(">i", raw[:4])[0]
        offset = struct.unpack(">Q", raw[8:16])[0] if wide else struct.unpack(">I", raw[8:12])[0]
        arches[cpu] = offset
    return arches.get(CPU_ARM64, arches.get(CPU_X86_64))


def sections(path: Path) -> Dict[str, Tuple[int, int]]:
    """Section name -> (file offset, size) of a Mach-O file; {} for another file."""
    try:
        with open(path, "rb") as f:
            base = _slice(f)
            if base is None:
                return {}
            f.seek(base)
            header = f.read(32)
            if header[:4] != MH_MAGIC_64 or len(header) < 32:
                return {}
            ncmds, sizeofcmds = struct.unpack_from("<II", header, 16)
            cmds = f.read(min(sizeofcmds, 1 << 20))
    except OSError:
        return {}
    out: Dict[str, Tuple[int, int]] = {}
    off = 0
    for _ in range(ncmds):
        if off + 8 > len(cmds):
            break
        cmd, size = struct.unpack_from("<II", cmds, off)
        if size < 8:
            break
        if cmd == LC_SEGMENT_64 and off + 72 <= len(cmds):
            nsects = struct.unpack_from("<I", cmds, off + 64)[0]
            for i in range(nsects):
                s = off + 72 + i * 80
                if s + 80 > len(cmds):
                    break
                name = cmds[s:s + 16].rstrip(b"\0").decode("latin-1")
                _addr, ssize, soff = struct.unpack_from("<QQI", cmds, s + 32)
                out.setdefault(name, (base + soff, ssize))
        off += size
    return out


def read_section(path: Path, where: Tuple[int, int]) -> bytes:
    try:
        with open(path, "rb") as f:
            f.seek(where[0])
            return f.read(min(where[1], MAX_SECTION))
    except OSError:
        return b""


def _varint(b: bytes, i: int) -> Tuple[int, int]:
    n = shift = 0
    while i < len(b):
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        shift += 7
        if c < 0x80:
            return n, i
    raise ValueError("truncated varint")


@dataclass
class GoBuild:
    version: str  # 1.25.3
    # (module path, version) of the main module and of each dependency; a replacement wins.
    modules: List[Tuple[str, str]] = field(default_factory=list)


def go_build(data: bytes) -> Optional[GoBuild]:
    """The content of __go_buildinfo, in the format of Go 1.18 and newer; None for another one."""
    if not data.startswith(GO_MAGIC) or len(data) < 33 or not data[15] & 2:
        return None
    try:
        n, i = _varint(data, 32)
        version = data[i:i + n].decode("utf-8")
        n, i = _varint(data, i + n)
        info = data[i:i + n]
    except (ValueError, UnicodeDecodeError):
        return None
    # The module text has a 16-byte sentinel at each end.
    text = info[16:-16].decode("utf-8", "replace") if len(info) > 32 else ""
    modules: List[Tuple[str, str]] = []
    for line in text.split("\n"):
        parts = line.split("\t")
        if parts[0] in ("mod", "dep") and len(parts) >= 3:
            modules.append((parts[1], parts[2]))
        elif parts[0] == "=>" and len(parts) >= 3 and modules:
            # The replacement of the module before it: a module with a version, or a local directory.
            modules[-1] = (parts[1], parts[2]) if parts[2].startswith("v") else (modules[-1][0], "")
    # go1.25.3, or with experiments go1.26.4-X:jsonv2 or "go1.25.3 X:nocoverageredesign".
    m = re.match(r"go(\d+(?:\.\d+)*(?:rc\d+)?)", version)
    if not m:
        return None
    return GoBuild(m[1], [(p, v) for p, v in modules if v.startswith("v")])


def rust_crates(data: bytes) -> Optional[List[Tuple[str, str]]]:
    """(name, version) of the crates.io crates in .dep-v0, without the crate of the binary itself."""
    try:
        # At most MAX_SECTION bytes: a hostile app could have a zlib bomb.
        text = zlib.decompressobj().decompress(data, MAX_SECTION)
        packages = json.loads(text).get("packages")
    except (zlib.error, ValueError, AttributeError):
        return None
    if not isinstance(packages, list):
        return None
    return [(p["name"], p["version"]) for p in packages if isinstance(p, dict) and p.get("source") == "crates.io"
            and not p.get("root") and isinstance(p.get("name"), str) and isinstance(p.get("version"), str)]


def go_symbol_prefix(package: str) -> str:
    """A package path as Go writes it in function names (objabi.PathToPrefix): in the last element a dot
    is %2e (gopkg.in/yaml%2ev3), and control characters, %, ", and non-ASCII are escaped everywhere."""
    slash = package.rfind("/")
    out = []
    for i, c in enumerate(package):
        if c <= " " or (c == "." and i > slash) or c in '%"' or c >= "\x7f":
            out.append("".join(f"%{b:02x}" for b in c.encode()))
        else:
            out.append(c)
    return "".join(out)


def go_has_function(names: bytes, package: str, symbol: str = "") -> bool:
    """True when the function name table of a Go binary has SYMBOL of PACKAGE: a function (Parse), a
    method (Reader.Read), also of a pointer receiver, of a generic type (Pointer[...]), or a generic
    function (Map[...]). Without SYMBOL: any function of the package."""
    prefix = re.escape(go_symbol_prefix(package).encode())
    if not symbol:
        return re.search(b"\0" + prefix + b"\\.", names) is not None
    if "." in symbol:
        typ, method = (re.escape(x.encode()) for x in symbol.split(".", 1))
        # The type arguments can have brackets of their own: Pointer[go.shape.[]uint8].
        pattern = b"\0" + prefix + b"\\.(?:\\(\\*)?" + typ + b"(?:\\[[^\0]*?\\])?\\)?\\." + method + b"[\0\\[]"
    else:
        pattern = b"\0" + prefix + b"\\." + re.escape(symbol.encode()) + b"[\0\\[]"
    return re.search(pattern, names) is not None
