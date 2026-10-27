"""The scan of dotkit tidy for installer packages: the receipts of pkgutil (TIDY.md, section 8.2).

Read-only, with the rules of migrators/: no writes, and commands only
through ctx.runner.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from ..context import Context

PKGUTIL = "/usr/sbin/pkgutil"

# A bundle is one unit: tidy moves it whole, never the files inside it.
BUNDLES = (".app", ".bundle", ".framework", ".plugin", ".kext", ".systemextension", ".dext", ".prefPane", ".driver",
           ".appex", ".qlgenerator", ".mdimporter", ".saver", ".component", ".vst", ".vst3", ".xpc")

# The parts of a package that only the vendor's uninstaller removes: a path starts with one of these, or has one
# of SYSTEM_INSIDE.
SYSTEM_DIRS = ("/Library/LaunchDaemons/", "/Library/LaunchAgents/", "/Library/Extensions/",
               "/Library/PrivilegedHelperTools/", "/Library/SystemExtensions/", "/Library/DriverExtensions/",
               "/Library/Audio/Plug-Ins/HAL/", "/Library/Security/", "/Library/Filesystems/",
               "/Library/Input Methods/", "/System/")
SYSTEM_INSIDE = ("/Contents/Library/SystemExtensions/", "/Contents/Library/LaunchServices/",
                 "/Contents/Library/LaunchDaemons/", ".kext/", ".dext/", ".systemextension/")

# Units that tidy never moves, even when a receipt lists them.
PROTECTED = ("/System/", "/bin/", "/sbin/", "/usr/bin/", "/usr/sbin/", "/usr/lib/", "/usr/libexec/", "/private/",
             "/opt/homebrew/", "/nix/")


@dataclass
class Receipt:
    id: str
    version: str = ""
    root: str = ""  # the volume and the install location: "/Applications"
    volume: str = "/"
    units: List[str] = field(default_factory=list)  # absolute: the outermost bundles and the plain files
    present: List[str] = field(default_factory=list)  # the units that exist, without the shared ones
    shared: List[str] = field(default_factory=list)  # units that another receipt lists too: they stay
    protected: List[str] = field(default_factory=list)  # units in PROTECTED: they stay
    system_parts: List[str] = field(default_factory=list)  # files of SYSTEM_DIRS and SYSTEM_INSIDE
    mas: bool = False  # an app of it has an App Store receipt
    cask: str = ""  # an installed cask uses the receipt

    @property
    def path(self) -> str:
        """The path of the plan line: the first app that exists, the first unit that exists, or the first unit."""
        apps = [u for u in self.present if u.endswith(".app")]
        return (apps or self.present or self.units or [self.root])[0]


def part_name(path: str) -> str:
    """The short name of a system part: its .kext, .dext, or .systemextension, or the name of the file."""
    for part in path.split("/"):
        if part.endswith((".kext", ".dext", ".systemextension")):
            return part
    return os.path.basename(path)


def unit_of(path: str) -> str:
    """The outermost bundle that has the file, or the file itself."""
    parts = path.split("/")
    for i, part in enumerate(parts[1:], 1):
        if part.endswith(BUNDLES):
            return "/".join(parts[:i + 1])
    return path


def on(volume: str, path: str) -> str:
    """The path as it is on its volume: /Volumes/Data/Library/x -> /Library/x."""
    return path if volume == "/" else "/" + os.path.relpath(path, volume)


def _info(text: str) -> Dict[str, str]:
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition(": ")
        if sep:
            out[key.strip()] = value.strip()
        elif line.endswith(":"):
            out[line[:-1].strip()] = ""
    return out


def scan(ctx: Context) -> List[Receipt]:
    """The receipts of the packages that are not Apple's, with their units."""
    run = ctx.runner.run
    ids = [x.strip() for x in (run([PKGUTIL, "--pkgs"], timeout=60) or "").splitlines()
           if x.strip() and not x.strip().startswith("com.apple.")]
    receipts: List[Receipt] = []
    owners: Dict[str, List[str]] = {}
    for pkg_id in ids:
        info = _info(run([PKGUTIL, "--pkg-info", pkg_id], timeout=60) or "")
        if not info.get("package-id"):
            continue
        volume = os.path.normpath(os.path.join("/", info.get("volume") or "/"))
        root = os.path.normpath(os.path.join(volume, info.get("location") or ""))
        r = Receipt(pkg_id, info.get("version", ""), root, volume=volume)
        for rel in (run([PKGUTIL, "--only-files", "--files", pkg_id], timeout=120) or "").splitlines():
            if not rel.strip():
                continue
            path = os.path.normpath(os.path.join(root, rel))
            on_volume = on(volume, path)
            if on_volume.startswith(SYSTEM_DIRS) or any(s in on_volume for s in SYSTEM_INSIDE):
                if len(r.system_parts) < 3:
                    r.system_parts.append(path)
            unit = unit_of(path)
            if unit not in r.units:
                r.units.append(unit)
        for unit in r.units:
            owners.setdefault(unit, []).append(pkg_id)
        receipts.append(r)
    for r in receipts:
        for unit in r.units:
            if not os.path.lexists(unit):
                continue
            if len(owners[unit]) > 1:
                r.shared.append(unit)
            elif (on(r.volume, unit) + "/").startswith(PROTECTED) or on(r.volume, unit) in ("/Applications", "/Library",
                                                                                           "/usr/local"):
                r.protected.append(unit)
            else:
                r.present.append(unit)
            if unit.endswith(".app") and (Path(unit) / "Contents/_MASReceipt/receipt").is_file():
                r.mas = True
    return receipts


def pkg_owner(receipts: List[Receipt]) -> Dict[str, Receipt]:
    """The receipt of each unit that exists: unit -> receipt."""
    return {u: r for r in receipts for u in r.present + r.shared + r.protected}


def matches(pattern: str, pkg_id: str) -> bool:
    """Homebrew reads a pkgutil ID of a cask as a pattern: com.google.drivefs.*."""
    try:
        return re.fullmatch(pattern, pkg_id) is not None
    except re.error:
        return pattern == pkg_id
