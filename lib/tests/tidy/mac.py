"""A fake Mac for the tests of dotkit tidy: apps with an Info.plist, casks in the bulk file, and brew answers."""

from __future__ import annotations

import json
import plistlib
from pathlib import Path

from unittest import mock

from dotkit.migrate.tidy import apps, clis
from tests import fake

BULK = "Library/Caches/Homebrew/api/internal/packages.arm64_sequoia.jws.json.payload"


class TidyMac:
    def __init__(self, test):
        self.h = fake.Home()
        test.addCleanup(self.h.cleanup)
        # /usr/local/bin of the fake Mac.
        self.usr_local = self.h.root / "usr-local-bin"
        self.usr_local.mkdir()
        patch = mock.patch.object(clis, "USR_LOCAL_BIN", self.usr_local)
        patch.start()
        test.addCleanup(patch.stop)
        self.bulk: dict = {}  # token -> the raw cask of the bulk file
        self.info: dict = {}  # token -> the cask of brew info --json=v2
        self.installed: list = []
        self.commands: dict = {}
        self.paths = {"brew": fake.BREW}
        self.procs: list = []
        self.write_bulk = True
        self.pkg_ids: list = ["com.apple.pkg.Something"]

    def app(self, name: str, bid: str = "", version: str = "1.0", build: str = "", where: Path | None = None,
            mas: int = 0, plist: bool = True) -> Path:
        d = (where or self.h.apps) / name
        (d / "Contents").mkdir(parents=True)
        if plist:
            info = {"CFBundleIdentifier": bid, "CFBundleShortVersionString": version,
                    "CFBundleVersion": build or version}
            with open(d / "Contents/Info.plist", "wb") as f:
                plistlib.dump({k: v for k, v in info.items() if v}, f)
        if mas:
            (d / "Contents/_MASReceipt").mkdir()
            (d / "Contents/_MASReceipt/receipt").write_bytes(b"x")
            self.commands[f"mdls -raw -name kMDItemAppStoreAdamID {d}"] = f"{mas}"
        return d

    def cask(self, token: str, apps=(), quit=(), zap=(), pkgutil=(), delete=(), target: str = "",
             version: str = "1.0", auto_updates: bool = False, pkg: bool = False, installed: str = "") -> None:
        raw: list = []
        u = {}
        if quit:
            u[":quit"] = list(quit)
        if pkgutil:
            u[":pkgutil"] = list(pkgutil)
        if delete:
            u[":delete"] = list(delete)
        if u:
            raw.append([":uninstall", u])
        if apps:
            raw.append([":app", list(apps)] + ([{":target": target}] if target else []))
        if pkg:
            raw.append([":pkg", [f"{token}.pkg"]])
        if zap:
            raw.append([":zap", {":trash": list(zap)}])
        self.bulk[token] = {"version": version, "raw_artifacts": raw}
        if auto_updates:
            self.bulk[token]["auto_updates"] = True
        arts: list = []
        if u:
            arts.append({"uninstall": [{k[1:]: v for k, v in u.items()}]})
        if apps:
            arts.append({"app": list(apps) + ([{"target": target}] if target else [])})
        if pkg:
            arts.append({"pkg": [f"{token}.pkg"]})
        self.info[token] = {"token": token, "version": version, "auto_updates": True if auto_updates else None,
                            "installed": installed or None, "artifacts": arts}
        if installed:
            self.installed.append(self.info[token])

    def receipt(self, pkg_id: str, files, location: str = "Applications", version: str = "1.0") -> None:
        """A receipt of pkgutil. Its volume is the root of the fake Mac, so its location "Applications" is the
        Applications directory of the fake Mac."""
        self.pkg_ids.append(pkg_id)
        self.commands[f"pkgutil --pkg-info {pkg_id}"] = (f"package-id: {pkg_id}\nversion: {version}\n"
                                                         f"volume: {self.h.root}/\nlocation: {location}\n"
                                                         "install-time: 1\n")
        self.commands[f"pkgutil --only-files --files {pkg_id}"] = "".join(f + "\n" for f in files)

    def runner(self) -> fake.FakeRunner:
        if self.write_bulk:
            p = self.h.home / BULK
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('{"protected":"x"}\n' + json.dumps({"formulae": {}, "casks": self.bulk}))
            (p.parents[1] / "cask_names.txt").write_text("".join(t + "\n" for t in sorted(self.bulk)))
        commands = {"brew info --json=v2 --installed --cask": json.dumps({"formulae": [], "casks": self.installed}),
                    "ps -axo comm=": "".join(p + "\n" for p in self.procs),
                    "pkgutil --pkgs": "".join(i + "\n" for i in self.pkg_ids)}
        for token, info in self.info.items():
            commands[f"brew info --json=v2 --cask {token}"] = json.dumps({"formulae": [], "casks": [info]})
        commands.update(self.commands)
        return fake.FakeRunner(commands, dict(self.paths))

    def context(self, runner: fake.FakeRunner | None = None):
        return fake.context_of(self.h, runner or self.runner())

    def scan(self) -> apps.TidyFacts:
        return apps.scan(self.context())

    def found(self, t: apps.TidyFacts, name: str) -> apps.App:
        return next(a for a in t.apps if a.name == name)
