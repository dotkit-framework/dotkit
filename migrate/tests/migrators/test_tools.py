"""What the tool migrators share (migrators/tools.py): the to-dos of the tools that are not carried."""

import unittest

from dotkit_migrate import registry
from dotkit_migrate._compat import tomllib
from dotkit_migrate.migrators import config_files
from tests import fake
from tests.test_session import pick, used_mac

MISE = ".config/mise/config.toml"


class ToolsTests(unittest.TestCase):
    def test_tools_not_in_mise_keep_their_intros(self):
        """Default choices: npm is on, but pnpm is never carried; dotkit has bat already."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        p = fake.session(h, used_mac(h))
        r = p.build(p.defaults(str(h.home / ".dotfiles"))).report
        npm = r.index("They stay where they are. To add one to config/mise.toml:\n")
        has = r.index("dotkit has these tools already, so they are not added.\n")
        self.assertLess(npm, r.index("`pnpm` (nvm v20.11.0): dotkit has no global pnpm"))
        self.assertLess(r.index("`pnpm` (nvm v20.11.0): dotkit has no global pnpm"), has)
        self.assertLess(has, r.index("`bat` (cargo install): dotkit has bat (from Nix). Old copy: `cargo uninstall bat`"))
        self.assertNotIn("mise builds cargo tools from source", r)
        self.assertEqual(r.count("### Tools that are not in mise"), 1)


    def mac(self, mise=None):
        """npm, pipx, cargo, and Go tools (cargo and go are not installed), nvm with node 20 and 18, and
        ~/.tool-versions; your mise config.

        Two Go binaries have one module, one has no module information, and a
        pipx tool comes from a URL.
        """
        h = fake.Home()
        self.addCleanup(h.cleanup)
        for name in ("typescript", "prettier"):
            h.json(f".nvm/versions/node/v20.11.0/lib/node_modules/{name}/package.json",
                   {"name": name, "version": "1.0.0", "bin": {name: "x"}})
        h.mkdir(".nvm/versions/node/v18.20.0")
        h.write(".nvm/alias/default", "20\n")
        h.json(".local/pipx/venvs/ruff/pipx_metadata.json",
               {"main_package": {"package": "ruff", "package_version": "0.4.0", "package_or_url": "ruff"}})
        h.json(".local/pipx/venvs/mytool/pipx_metadata.json",
               {"main_package": {"package": "mytool", "package_or_url": "git+https://example.com/mytool.git"}})
        h.json(".cargo/.crates2.json", {"installs": {"tokei 12.1.2 (registry+https://x)": {}}})
        for name in ("hey", "hey2"):
            p = h.home / "go/bin" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"\x00binary\npath\tgithub.com/rakyll/hey\nmod\tgithub.com/rakyll/hey\tv0.1.4\th1:x=\n")
            p.chmod(0o755)
        h.write("go/bin/mystery", "#!/bin/sh\n", mode=0o755)
        h.write(".tool-versions", "nodejs 18.20.0\n")
        if mise is not None:
            h.write(MISE, mise)
        p = registry.Session(fake.context_of(h, fake.brew_runner()), strict=True)
        c = p.defaults(str(h.home / ".dotfiles"))
        pick(c, "cargo:tokei", "go:github.com/rakyll/hey", "runtime:node")
        return p, c

    def test_a_kept_mise_config_gets_to_dos(self):
        """Kept in place, mise does not read config/mise.toml: it stays the template, each tool is a to-do."""
        p, c = self.mac('[tools]\n"npm:prettier" = "latest"\n')
        c.options[f"file:{MISE}"] = "keep"
        self.assertEqual(config_files.mise_target(p.scan(), c), "kept")
        self.assertEqual(config_files.mise_dest(p.scan(), c), "~/.config/mise/config.toml")
        result = p.build(c)
        self.assertNotIn("config/mise.toml", result.files, "the template's file, unchanged")
        self.assertIsNone(result.links[MISE])
        # One item per tool, and one for the Go module of two binaries; the cargo and go lines after their items.
        self.assertIn("### mise\n\n" + config_files.KEPT_INTRO + "\n\n"
                      "- `typescript` (nvm v20.11.0): `mise use -g npm:typescript`\n"
                      "- `ruff` (pipx): `mise use -g pipx:ruff`\n"
                      "- `tokei` (cargo install): `mise use -g cargo:tokei`\n"
                      "- mise needs cargo for these: `mise use -g rust`.\n"
                      "- `github.com/rakyll/hey` (~/go/bin/hey): `mise use -g go:github.com/rakyll/hey`\n"
                      "- mise needs go for these: `mise use -g go`.\n"
                      "- `node` (nvm): `mise use -g node@20` (also installed: 18.20.0)\n\n", result.report)
        self.assertEqual(result.report.count("`mise use -g go:github.com/rakyll/hey`"), 1)
        # npm:prettier is in your config: nothing about it.
        self.assertNotIn("prettier", result.report)
        self.assertFalse([s for s in result.summary if "config/mise.toml" in s and "->" in s], result.summary)
        for text in ("Tools that moved to mise", "Old copy", "has these versions now", "With mise:"):
            self.assertNotIn(text, result.report)
        self.assertIn("nvm (~/.nvm): node v18.20.0, v20.11.0 (default 20). Then remove its lines", result.report)
        # The texts that name the mise config name yours.
        dest = "~/.config/mise/config.toml"
        for text in (f"They stay where they are. To add one to {dest}:\n",
                     f"`mytool` (pipx): installed from git+https://example.com/mytool.git. Add it to {dest} by hand.",
                     f"`mystery` (~/go/bin/mystery): no Go module information. Add it to {dest} by hand.",
                     f"mise still reads these files. Move their tools to {dest}.\n\n- ~/.tool-versions\n"):
            self.assertIn(text, result.report)
        for text in ("To add one to config/mise.toml", "Add it to config/mise.toml", "Move their tools to config/"):
            self.assertNotIn(text, result.report)
        self.assertEqual("\n".join(result.summary[-3:-1]),
                         "link are renamed to <name>.before-dotkit. mise reads your ~/.config/mise/config.toml,\n"
                         "which stays in place; config/mise.toml of this repository is not read.")
        self.assertNotIn("mise installs the tools of config/mise.toml.", "\n".join(result.summary))

    def test_no_mise_config_and_a_layered_one_go_to_the_repository(self):
        """Without a config of yours, or with yours carried as "layer", the tools go to config/mise.toml.

        The nvm node 20 is offered and added, though the base has node = "24": that is only a default.
        """
        for mise in (None, '[tools]\n"npm:prettier" = "latest"\n'):
            with self.subTest(mise=mise):
                p, c = self.mac(mise)
                self.assertEqual(config_files.mise_target(p.scan(), c), None if mise is None else "repository")
                self.assertEqual(config_files.mise_dest(p.scan(), c), "config/mise.toml")
                result = p.build(c)
                tools = tomllib.loads(result.files["config/mise.toml"])["tools"]
                self.assertEqual({k: tools[k] for k in ("npm:typescript", "npm:prettier", "pipx:ruff", "cargo:tokei",
                                                        "go:github.com/rakyll/hey", "node")},
                                 {"npm:typescript": "latest", "npm:prettier": "latest", "pipx:ruff": "latest",
                                  "cargo:tokei": "latest", "go:github.com/rakyll/hey": "latest",
                                  "node": ["20", "18.20.0"]})
                self.assertIn("1 pipx tool -> config/mise.toml", result.summary)
                self.assertIn("runtime versions of nvm -> config/mise.toml", result.summary)
                self.assertIn("config/mise.toml has these versions now", result.report)
                self.assertIn("Old copy: `npm uninstall -g typescript`", result.report)
                self.assertIn("- mise needs cargo for these: `mise use -g rust`.", result.report)
                self.assertIn("- mise needs go for these: `mise use -g go`.", result.report)
                self.assertNotIn(config_files.KEPT_INTRO, result.report)
                self.assertIn("2 npm tools -> config/mise.toml" if mise is None else "1 npm tool -> config/mise.toml",
                              result.summary)
                for text in ("They stay where they are. To add one to config/mise.toml:\n",
                             "Add it to config/mise.toml by hand.",
                             "mise still reads these files. Move their tools to config/mise.toml.\n"):
                    self.assertIn(text, result.report)
                self.assertNotIn("~/.config/mise/config.toml by hand", result.report)
                self.assertEqual(result.summary[-2],
                                 "link are renamed to <name>.before-dotkit. mise installs the tools of "
                                 "config/mise.toml.")

if __name__ == "__main__":
    unittest.main()
