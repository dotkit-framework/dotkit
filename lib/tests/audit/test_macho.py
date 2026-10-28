"""audit/macho.py (BEHAVIOR.md, Au13): the sections of a Mach-O file, Go build info, Rust .dep-v0, and the
function names of a Go binary."""

from __future__ import annotations

import unittest

from dotkit.audit import macho
from tests import fake
from tests.audit import machofake as mf


class MachoTests(unittest.TestCase):
    def setUp(self):
        self.dir = fake.tempdir()
        self.addCleanup(fake.remove, self.dir)

    def write(self, name: str, data: bytes):
        p = self.dir / name
        p.write_bytes(data)
        return p

    def test_sections(self):
        p = self.write("tool", mf.macho({".dep-v0": b"abc", "__gopclntab": b"\0x\0"}))
        s = macho.sections(p)
        self.assertEqual(set(s), {".dep-v0", "__gopclntab"})
        self.assertEqual(macho.read_section(p, s[".dep-v0"]), b"abc")

    def test_universal_takes_arm64(self):
        data = mf.universal([(mf.CPU_X86_64, mf.macho({"x86": b"intel"}, mf.CPU_X86_64)),
                             (mf.CPU_ARM64, mf.macho({"arm": b"apple"}))])
        p = self.write("fat", data)
        s = macho.sections(p)
        self.assertEqual(list(s), ["arm"])
        self.assertEqual(macho.read_section(p, s["arm"]), b"apple")

    def test_not_macho(self):
        for name, data in (("script", b"#!/bin/sh\necho hi\n"), ("empty", b""), ("short", b"\xcf\xfa\xed\xfe\0")):
            with self.subTest(name=name):
                self.assertEqual(macho.sections(self.write(name, data)), {})
        self.assertEqual(macho.sections(self.dir / "missing"), {})

    def test_go_build(self):
        info = mf.go_buildinfo("go1.26.4-X:jsonv2", [("mod", "example.com/tool", "(devel)"),
                                                     ("dep", "golang.org/x/net", "v0.57.0"),
                                                     ("dep", "golang.org/x/sys", "v0.30.0"),
                                                     ("=>", "golang.org/x/sys", "v0.31.0"),
                                                     ("dep", "example.com/local", "v1.0.0"),
                                                     ("=>", "../local", "")])
        build = macho.go_build(info)
        self.assertEqual(build.version, "1.26.4")
        self.assertEqual(build.modules, [("golang.org/x/net", "v0.57.0"), ("golang.org/x/sys", "v0.31.0")])
        self.assertIsNone(macho.go_build(b"\xff Go buildinf:" + bytes([8, 0]) + b"\0" * 32))  # before Go 1.18
        self.assertIsNone(macho.go_build(b"other"))

    def test_rust_crates(self):
        data = mf.rust_deps([{"name": "rg", "version": "15.2.0", "source": "local", "root": True},
                             {"name": "regex", "version": "1.11.0", "source": "crates.io"},
                             {"name": "mine", "version": "0.1.0", "source": "git"}])
        self.assertEqual(macho.rust_crates(data), [("regex", "1.11.0")])
        self.assertIsNone(macho.rust_crates(b"not zlib"))

    def test_go_has_function(self):
        names = mf.go_functions(["golang.org/x/crypto/ssh.(*Client).Dial", "net/http.ListenAndServe",
                                 "example.com/x.Map[...]", "golang.org/x/crypto/ssh.Marshal"])
        rows = [("golang.org/x/crypto/ssh", "Client.Dial", True), ("golang.org/x/crypto/ssh", "Dial", False),
                ("net/http", "ListenAndServe", True), ("example.com/x", "Map", True),
                ("golang.org/x/crypto/ssh", "", True), ("golang.org/x/crypto/openpgp", "", False),
                ("golang.org/x/crypto/ssh", "Marshal", True), ("golang.org/x/crypto/ss", "", False)]
        for package, symbol, want in rows:
            with self.subTest(package=package, symbol=symbol):
                self.assertEqual(macho.go_has_function(names, package, symbol), want)

    def test_go_names_of_packages_with_a_dot_and_of_generic_types(self):
        """Go writes a dot in the last element of a package path as %2e; a method of a generic type has the
        type arguments after the type."""
        names = mf.go_functions(["gopkg.in/yaml%2ev3.Unmarshal", "sync/atomic.(*Pointer[go.shape.[]uint8]).Load",
                                 "example.com/x.List[go.shape.int].Len"])
        self.assertEqual(macho.go_symbol_prefix("gopkg.in/yaml.v3"), "gopkg.in/yaml%2ev3")
        rows = [("gopkg.in/yaml.v3", "Unmarshal", True), ("gopkg.in/yaml.v3", "", True),
                ("sync/atomic", "Pointer.Load", True), ("sync/atomic", "Pointer.Store", False),
                ("example.com/x", "List.Len", True)]
        for package, symbol, want in rows:
            with self.subTest(package=package, symbol=symbol):
                self.assertEqual(macho.go_has_function(names, package, symbol), want)

    def test_rust_crates_size_limit(self):
        """A zlib bomb gives at most MAX_SECTION bytes, which are not JSON."""
        import zlib
        from unittest import mock
        with mock.patch.object(macho, "MAX_SECTION", 1000):
            self.assertIsNone(macho.rust_crates(zlib.compress(b"[" + b" " * 100000 + b"]")))


if __name__ == "__main__":
    unittest.main()
