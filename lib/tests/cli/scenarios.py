"""The parity scenarios: one run of the dotkit command each, with the rows of PARITY.md that it covers.

The names of tools that dotkit add must not find on this Mac start with dkt-:
existing_command looks in the real Nix profile and /opt/homebrew/bin.
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

from tests.cli import fixtures as fx
from tests.cli.harness import REPO, Scenario

S = Scenario
REPO_LOCK = "@SB/repo/nix/flake.lock"
CHECKOUT_LOCK = "@SB/checkout/flake.lock"
NEW_REPO_LOCK = fx.lock({"dotkit": (1761000000, "ddddddd4444444")}, {"nixpkgs": ["dotkit", "nixpkgs"]})
NEW_CHECKOUT_LOCK = fx.lock({"nixpkgs": (1761100000, "eeeeeee5555555"), "nix-darwin": (1760000000, "bbbbbbb2222222")})
SAME_REPO_LOCK = json.loads(REPO["repo/nix/flake.lock"])


def real_brew(present: bool):
    """Skip unless /opt/homebrew/bin/brew is (present) or is not there: apply -n runs it when it is."""
    def check() -> Optional[str]:
        there = os.access("/opt/homebrew/bin/brew", os.X_OK)
        if there != present:
            return "/opt/homebrew/bin/brew is " + ("missing" if present else "installed")
        return None
    return check


def no_real_mas() -> Optional[str]:
    return "/opt/homebrew/bin/mas is installed" if os.access("/opt/homebrew/bin/mas", os.X_OK) else None


def bare(name: str, cask=None, formula=None, nix=None, mise=None, npm=None, pypi=None) -> List[dict]:
    """The six lookups of a bare name. Each argument is the rules of a source, or None for "not found"."""
    rules: List[dict] = []
    for found in (cask, formula, nix, mise, npm, pypi):
        rules += found or []
    if nix is None:
        rules += fx.nix_no_pkg(name)
    return rules


def start() -> List[S]:
    return [
        S("start-no-dotfiles", ["--help"], no_dotfiles=True, rows=["G1"]),
        S("start-no-dotfiles-add", ["add", "x"], no_dotfiles=True, rows=["G1"]),
        S("start-dotfiles-missing", ["apply"], env={"DOTFILES": "@SB/nope"}, rows=["G2"], deviation="DV3"),
        S("start-dotkit-dir-missing", ["apply"], env={"DOTKIT_DIR": "@SB/nope"}, rows=["G3"], deviation="DV3"),
        S("start-dotkit-dir-missing-add", ["add", "x"], env={"DOTKIT_DIR": "@SB/nope"}, rows=["G3"],
          deviation="DV3"),
        S("start-dotfiles-resolved", ["rollback"], env={"DOTFILES": "@SB/repo/../repo"}, rules=fx.switch_ok(),
          rows=["G5", "H4"]),
    ]


def dispatch() -> List[S]:
    out = [
        S("help-none", [], rows=["C1"]),
        S("help-short", ["-h"], rows=["C1"]),
        S("help-long", ["--help"], rows=["C1"]),
        S("help-word", ["help"], rows=["C1"]),
        S("unknown-command", ["frobnicate"], rows=["C7"]),
        S("complete-inputs", ["__complete", "inputs"], rows=["C3"]),
        S("complete-inputs-dotkit-dir", ["__complete", "inputs"], dotkit_dir=True, rows=["C3"]),
        S("complete-tools-user", ["__complete", "tools-user"], rows=["C4", "N1"], deviation="DV4"),
        S("complete-tools-all", ["__complete", "tools-all"], rows=["C4", "N2"], deviation="DV4"),
        S("complete-tools-no-mas", ["__complete", "tools-user"], files={"repo/nix/mas.txt": None}, rows=["N3"],
          deviation="DV4"),
        S("complete-tools-sorted", ["__complete", "tools-user"], rows=["N1"], deviation="DV4",
          files={"repo/nix/packages.txt": "zz\n# c\nAa\n\nb-b  # x\nb_b\n",
                 "repo/nix/brews.txt": "b.b\nAa\n", "repo/nix/casks.txt": "@x\n"}),
        S("complete-none", ["__complete"], rows=["C5"]),
        S("complete-bad", ["__complete", "frob"], rows=["C5"]),
    ]
    for old in ("switch", "build", "diff", "update", "brew", "tools", "changes", "where", "gc"):
        out.append(S(f"old-name-{old}", [old], rows=["C6"]))
    return out


def options() -> List[S]:
    return [
        S("option-help-after-command", ["apply", "-h"], rows=["O2"]),
        S("option-help-after-argument", ["apply", "extra", "-h"], rows=["O2"]),
        S("option-unknown-before-help", ["clean", "-z", "-h"], rows=["O2", "O4"]),
        S("option-unknown", ["apply", "-x"], rows=["O4"]),
        S("option-unknown-long", ["apply", "--dry"], rows=["O4"]),
        S("option-double-dash", ["clean", "--", "-5d"], rows=["O3", "O7"]),
        S("option-plan-needs-file", ["tidy", "--plan"], rows=["O5"]),
        S("option-plan-missing-dir", ["tidy", "-n", "--plan", "nope/plan.txt"],
          rules=fx.declared_homebrew() + [{"tool": "dotkit-migrate", "re": ""}], rows=["O5"], deviation="B1"),
        S("option-plan-missing-dir-fixed", ["tidy", "-n", "--plan", "nope/plan.txt", "-h"], rows=["O5"]),
        S("option-plan-not-tidy", ["migrate", "--plan", "p.txt"], rows=["O6"]),
        S("option-all-not-migrate", ["tidy", "-a"], rows=["O6"]),
        S("option-all-long-not-migrate", ["apply", "--all"], rows=["O6"]),
        S("option-history-dry-run", ["history", "-n"], rows=["O6"]),
        S("option-rollback-yes", ["rollback", "--yes"], rows=["O6"]),
        S("option-apply-yes", ["apply", "-y"], rows=["O7"]),
        S("option-apply-dry-run-long", ["apply", "--dry-run", "x"], rows=["O1", "A1"]),
        S("option-checks-before-arguments", ["history", "x", "-n"], rows=["O4"]),
        S("option-mixed", ["migrate", "-a", "--yes", "--dry-run"], rows=["O1"],
          rules=fx.declared_homebrew() + [{"tool": "dotkit-migrate", "re": ""}]),
    ]


def apply() -> List[S]:
    return [
        S("apply-args", ["apply", "x"], rows=["A1"]),
        S("apply", ["apply"], rules=fx.apply_ok(), rows=["A2"]),
        S("apply-build-fails", ["apply"], rules=fx.build(fails=True), rows=["A3"]),
        S("apply-switch-fails", ["apply"], rules=fx.build() + fx.switch_fails() + fx.switch_ok(), rows=["A4"]),
        S("apply-mise-fails", ["apply"], rules=fx.build() + fx.switch_ok() + fx.mise_fails("install"),
          rows=["A5"]),
        S("apply-dry-run", ["apply", "-n"], skip=real_brew(True), rows=["A6", "A7"],
          rules=fx.build() + fx.diff_closures() + fx.cleanup_mode("none") + fx.mise_ok("install --dry-run")),
        S("apply-dry-run-no-brew", ["apply", "-n"], skip=real_brew(False), rows=["A6", "A7"],
          rules=fx.build() + fx.diff_closures() + fx.mise_ok("install --dry-run")),
        S("apply-dotkit-dir", ["apply"], dotkit_dir=True, rows=["A9", "A10", "G4"],
          rules=fx.drv_path() + fx.build() + fx.switch_ok() + fx.mise_ok("install")),
        S("apply-dry-run-dotkit-dir", ["apply", "-n"], dotkit_dir=True, skip=real_brew(True),
          rows=["A6", "A9", "A10"],
          rules=fx.drv_path() + fx.build() + fx.diff_closures()
          + [{**fx.cleanup_mode("none")[0], "stderr": "warning: not writing lock file of flake 'x':\n"
              "• Added input 'y':\n  → 'z'\nreal warning\n"}] + fx.mise_ok("install --dry-run")),
    ]


def upgrade() -> List[S]:
    nix_rules = fx.flake_update(REPO_LOCK, NEW_REPO_LOCK) + fx.build() + fx.diff_closures()
    brew_rules = fx.brew(r"^update --quiet$") + fx.outdated(
        [("wget", ["1.24.5"], "1.25.0"), ("notdeclared", ["1"], "2")],
        [("zed", ["1.25.2", "1.25.1"], "1.25.3"), ("ghostty", ["1.2.0"], "1.2.1")])
    mise_rules = fx.mise_outdated({"node": ("22.1.0", "22.2.0"), "npm:cowsay": ("1.5.0", "1.6.0")})
    return [
        S("upgrade-unknown-source", ["upgrade", "frob"], rows=["U1"]),
        S("upgrade-unknown-source-dotkit-dir", ["upgrade", "frob"], dotkit_dir=True, rows=["U1"]),
        S("upgrade-nix-dry-run", ["upgrade", "-n", "nix"], rules=nix_rules,
          rows=["U4", "U5", "U9", "U10", "U11"]),
        S("upgrade-nix-up-to-date", ["upgrade", "nix"], rules=fx.flake_update(REPO_LOCK, SAME_REPO_LOCK),
          rows=["U5", "U8"]),
        S("upgrade-nix-yes", ["upgrade", "-y", "nix"], rules=nix_rules + fx.switch_ok(), rows=["U13", "U14"]),
        S("upgrade-nix-no-terminal", ["upgrade", "nix"], rules=nix_rules, rows=["U12", "U15", "Q1"]),
        S("upgrade-nix-tty-no", ["upgrade", "nix"], rules=nix_rules, tty="n\n", rows=["U12"]),
        S("upgrade-nix-tty-yes", ["upgrade", "nix"], rules=nix_rules + fx.switch_ok(), tty="y\n",
          rows=["U12", "U13"]),
        S("upgrade-nix-update-fails", ["upgrade", "nix"], rules=fx.flake_update(REPO_LOCK, {}, fails=True),
          rows=["U4"]),
        S("upgrade-nix-switch-fails", ["upgrade", "-y", "nix"], rules=nix_rules + fx.switch_fails(),
          rows=["U13"], deviation="B2"),
        S("upgrade-nix-switch-stops", ["upgrade", "-y", "nix"], rules=nix_rules + fx.switch_fails() + fx.switch_ok(),
          rows=["U13"]),
        S("upgrade-nix-activate-fails", ["upgrade", "-y", "nix"], rows=["U13"],
          rules=nix_rules + [{"tool": "sudo", "re": "darwin-rebuild", "exit": 1, "stderr": "error: activation\n"}]
          + fx.switch_ok()),
        S("upgrade-nix-build-fails", ["upgrade", "-y", "nix"],
          rules=fx.flake_update(REPO_LOCK, NEW_REPO_LOCK) + fx.build(fails=True), rows=["U16"]),
        S("upgrade-input-name", ["upgrade", "-n", "dotkit"], rules=nix_rules, rows=["U1", "U4"]),
        S("upgrade-nix-and-input", ["upgrade", "-n", "nix", "dotkit"], rules=nix_rules, rows=["U3"]),
        S("upgrade-dotkit-dir", ["upgrade", "-n", "nix"], dotkit_dir=True, rows=["U4", "U5"],
          rules=fx.flake_update(REPO_LOCK, NEW_REPO_LOCK, pattern=r"^flake update --flake path:")
          + fx.flake_update(CHECKOUT_LOCK, NEW_CHECKOUT_LOCK, pattern=r"^flake update --flake git\+file:")
          + fx.drv_path() + fx.build() + fx.diff_closures()),
        S("upgrade-dotkit-dir-bare-input", ["upgrade", "-n", "nixpkgs"], dotkit_dir=True, rows=["U1", "U4"],
          rules=fx.flake_update(CHECKOUT_LOCK, NEW_CHECKOUT_LOCK, pattern=r"^flake update nixpkgs --flake git\+")
          + fx.drv_path() + fx.build() + fx.diff_closures()),
        S("upgrade-dotkit-dir-yes", ["upgrade", "-y", "nix"], dotkit_dir=True, rows=["U14"],
          rules=fx.flake_update(REPO_LOCK, NEW_REPO_LOCK, pattern=r"^flake update --flake path:")
          + fx.flake_update(CHECKOUT_LOCK, NEW_CHECKOUT_LOCK, pattern=r"^flake update --flake git\+file:")
          + fx.drv_path() + fx.build() + fx.diff_closures() + fx.switch_ok()),
        S("upgrade-brew-dry-run", ["upgrade", "-n", "brew"], rules=brew_rules, rows=["U6", "U9", "U11"]),
        S("upgrade-brew-yes", ["upgrade", "--yes", "brew"], rows=["U13"],
          rules=brew_rules + fx.brewfile() + fx.brew(r"^bundle install --file=-$", stdin=True)),
        S("upgrade-brew-update-fails", ["upgrade", "brew"], rows=["U6"],
          rules=fx.brew(r"^update", "Updating Homebrew...\n", stderr="fatal: unable to access\n", exit=1)),
        S("upgrade-mise-yes", ["upgrade", "-y", "mise"], rules=mise_rules + fx.mise_ok("upgrade$"),
          rows=["U7", "U13"]),
        S("upgrade-mise-fails", ["upgrade", "mise"], rules=fx.mise_fails("outdated"), rows=["U7"]),
        S("upgrade-all-dry-run", ["upgrade", "-n"], rules=nix_rules + brew_rules + mise_rules,
          rows=["U2", "U9"]),
        S("upgrade-all-up-to-date", ["upgrade", "-n"], rows=["U2", "U8"],
          rules=fx.flake_update(REPO_LOCK, SAME_REPO_LOCK) + fx.brew(r"^update --quiet$") + fx.outdated()
          + fx.mise_outdated({})),
    ]


def clean() -> List[S]:
    dry = [{"tool": "sudo", "re": "^nix-collect-garbage"}] + fx.brew(r"^cleanup --dry-run$",
                                                                     "Would remove: x (1MB)\n") \
        + fx.mise_ok("prune --dry-run$")
    wet = [{"tool": "sudo", "re": "^nix-collect-garbage"}] + fx.brew(r"^cleanup$") + fx.mise_ok("prune --yes$")
    return [
        S("clean-too-many", ["clean", "1d", "2d"], rows=["K1"]),
        S("clean-bad-age", ["clean", "2w"], rows=["K2"]),
        S("clean-dry-run", ["clean", "-n"], rules=dry, rows=["K2", "K3"]),
        S("clean-dry-run-age", ["clean", "-n", "30d"], rules=dry, rows=["K2", "K3"]),
        S("clean-no-terminal", ["clean"], rules=wet, rows=["K4"]),
        S("clean-tty-no", ["clean", "7d"], rules=wet, tty="n\n", rows=["K4", "Q1"]),
        S("clean-tty-yes-upper", ["clean"], rules=wet, tty="Y\n", rows=["K5", "Q1"]),
        S("clean-tty-yes-word", ["clean"], rules=wet, tty="yes\n", rows=["Q1"]),
        S("clean-yes", ["clean", "-y"], rules=wet, rows=["K5"]),
    ]


def recovery() -> List[S]:
    return [
        S("history-args", ["history", "x"], rows=["H1"]),
        S("rollback-args", ["rollback", "x"], rows=["H3"]),
        S("rollback", ["rollback"], rules=fx.switch_ok(), rows=["H4"]),
        S("rollback-fails", ["rollback"], rules=[{"tool": "sudo", "re": "", "exit": 1}], rows=["H4"]),
    ]


def wizard() -> List[S]:
    ok = fx.declared_homebrew() + [{"tool": "dotkit-migrate", "re": "", "stdout": "wizard output\n"}]
    return [
        S("migrate-args", ["migrate", "x"], rows=["M1"]),
        S("tidy-args", ["tidy", "x"], rows=["M1"]),
        S("migrate", ["migrate"], rules=ok, rows=["M2", "M3"]),
        S("migrate-flags", ["migrate", "-n", "-y", "--all"], rules=ok, rows=["M3"]),
        S("migrate-exit-status", ["migrate"], rows=["M3"],
          rules=fx.declared_homebrew() + [{"tool": "dotkit-migrate", "re": "", "stderr": "Stopped.\n", "exit": 3}]),
        S("migrate-eval-fails", ["migrate"], rules=fx.nix_eval_fails(), rows=["M5"]),
        S("migrate-dotkit-dir", ["migrate"], dotkit_dir=True, rows=["M2", "A10"],
          rules=[{**fx.declared_homebrew()[0], "stderr": "warning: not writing modified lock file of flake 'p':\n"
                  "• Updated input 'dotkit':\n    'a'\n  → 'b'\n"}] + ok[1:]),
        S("tidy", ["tidy", "-n", "--plan", "plan.txt"], rules=ok, rows=["M4", "O5"]),
        S("tidy-yes", ["tidy", "-y"], rules=ok, rows=["M4"]),
    ]


def pkg_parse() -> List[S]:
    return [
        S("pkg-help-add", ["add", "--help"], rows=["O2"]),
        S("pkg-help-remove", ["remove", "-h"], rows=["O2"]),
        S("pkg-help-info", ["info", "x", "--help"], rows=["O2"]),
        S("pkg-help-list", ["list", "-h"], rows=["O2"]),
        S("pkg-no-tools-add", ["add"], rows=["O6"]),
        S("pkg-no-tools-add-dry-run", ["add", "-n"], rows=["O6"]),
        S("pkg-no-tools-remove", ["remove"], rows=["O6"]),
        S("pkg-no-tools-info", ["info", "-y"], rows=["O6", "O6"]),
        S("pkg-unknown-option", ["add", "-z", "x"], rows=["O4", "C2"]),
        S("pkg-top-level-option", ["add", "--all", "x"], rows=["C2"]),
        S("pkg-unknown-option-list", ["list", "--frob"], rows=["O4"]),
        S("pkg-double-dash", ["info", "--", "-n"], rows=["O4", "R8"]),
        S("pkg-options-accepted", ["list", "-f", "-n", "-y", "nix"], rows=["O6"]),
    ]


def listing() -> List[S]:
    apps = fx.mas_apps({"Keynote": 409183694, 'A \\"q\\" \\\\ \\#': 5, "Zz": 7})
    return [
        S("list", ["list"], rules=apps, rows=["L2", "L3", "L4", "L5", "L6"]),
        S("list-order", ["list", "mise", "nix", "nix"], rows=["L2"]),
        S("list-bad", ["list", "nix", "frob"], rows=["L1"]),
        S("list-brew-eval-fails", ["list", "brew"], rules=fx.nix_eval_fails(), rows=["L7"]),
        S("list-odd-files", ["list", "nix", "mise"], rows=["L5", "L6"],
          files={"repo/nix/packages.txt": "# comment\n\n  indented  # c\r\nlast-no-newline",
                 "repo/config/mise.toml": '[tools] # c\n# c\n  spaced = "1"\nplain = "2"  # pinned\n'
                                          '"quoted:key" = "3"\ntable = { version = "4.5", os = ["macos"] }\n'
                                          'noversion = { os = ["macos"] }\nmany = ["22", "20"]\n'
                                          '[other]\nnot = "a tool"\n[tools.python]\nversion = "3.13"\n'}),
        S("list-invalid-toml", ["list", "mise"], rows=["L5"], files={"repo/config/mise.toml": '[tools]\nx = \n'}),
    ]


def info() -> List[S]:
    rg = bare("ripgrep", nix=fx.nix_pkg("ripgrep", "15.1.0", "Fast grep", bin="dkt-rg"),
              mise=fx.mise_tool("ripgrep", "aqua:BurntSushi/ripgrep", "15.1.0", "Fast grep"),
              formula=fx.formula("ripgrep", "15.1.0", "Search tool")) + fx.compare("15.1.0", "15.1.0", 0)
    return [
        S("info-declared-base", ["info", "ripgrep"], rules=rg, unordered=True, rows=["I1", "I2", "I4", "D2"]),
        S("info-nothing", ["info", "dkt-nothing"], rules=bare("dkt-nothing"), unordered=True, rows=["I1", "I5"]),
        S("info-mixed", ["info", "dkt-nothing", "nix:jq"], rows=["I5", "D2"],
          rules=bare("dkt-nothing") + fx.nix_pkg("jq", "1.8.1", "JSON processor", bin="dkt-jq"), unordered=True),
        S("info-command-mise", ["info", "mise:dkt-tool"], rows=["I3", "X1", "X2"],
          env={"MISE_DATA_DIR": "@SB/home/mise"},
          files={"home/mise/installs/dkt-tool/1.2.0/bin/dkt-tool": "#!/bin/sh\n"},
          rules=fx.mise_tool("dkt-tool", "aqua:o/dkt-tool", "1.2.0") + fx.nix_no_pkg("dkt-tool")
          + fx.mise_which("dkt-tool", "@SB/home/mise/installs/dkt-tool/1.2.0/bin/dkt-tool")),
        S("info-mas", ["info", "mas:409183694"], rules=fx.mas(), rows=["D1", "R5", "I2"]),
        S("info-mise-key", ["info", "dkt-hunk"], rules=bare("dkt-hunk"), unordered=True, rows=["D2", "I1"]),
        S("info-mise-npm-key", ["info", "@dkt-ai/dkt-code"], rows=["D2", "R7"],
          rules=fx.npm("@dkt-ai/dkt-code", "2.1.304", "Code", {"dkt-claude": "cli.js"})),
        S("info-prefixed-matches-other-list", ["info", "nix:dkt-z"], files={"repo/nix/casks.txt": "dkt-z\n"},
          rules=fx.nix_no_pkg("dkt-z"), rows=["D2"]),
    ]


def mas_info(name: Optional[str]) -> dict:
    obj = {"adamID": 497799835, "bundleID": "com.apple.dt.Xcode", "version": "27.1"}
    if name is not None:
        obj["name"] = name
    return obj


def add_prefix() -> List[S]:
    return [
        S("add-nix-dry-run", ["add", "-n", "nix:dkt-hello"], rules=fx.nix_pkg("dkt-hello", "2.12", "Hello"),
          rows=["R1", "Ad8", "Ad11", "Pc1"]),
        S("add-nix-attr-path", ["add", "-n", "nix:python3Packages.dkt-x"],
          rules=fx.nix_pkg("python3Packages.dkt-x", "1", "", bin=""), rows=["R1", "R18"]),
        S("add-nix-invalid", ["add", "-n", "nix:9bad"], rows=["R1", "Ad3", "Ad9"]),
        S("add-nix-missing", ["add", "-n", "nix:dkt-none"], rules=fx.nix_no_pkg("dkt-none"), rows=["R1"]),
        S("add-nix-eval-fails", ["add", "-n", "nix:dkt-hello"], rules=fx.nix_eval_fails(), rows=["R1"]),
        S("add-brew-formula", ["add", "-n", "brew:dkt-hello"], rows=["R2", "R18"],
          rules=fx.formula("dkt-hello", "2.12.3", "Hello\tworld\n") + fx.nix_pkg("dkt-hello", bin="dkt-hi")),
        S("add-brew-cask-app", ["add", "-n", "brew:dkt-app"], rules=fx.cask("dkt-app", "1.2", "App", app="A.app"),
          rows=["R2", "R21"]),
        S("add-brew-none", ["add", "-n", "brew:dkt-none"], rows=["R2"]),
        S("add-brew-invalid", ["add", "-n", "brew:.x"], rows=["R2"]),
        S("add-cask-cli", ["add", "-n", "cask:dkt-cli"], rules=fx.cask("dkt-cli", "0.1", "CLI", binary="dkt-c"),
          rows=["R3", "R21"]),
        S("add-cask-pkg", ["add", "-n", "cask:dkt-pkg"], rules=fx.cask("dkt-pkg", "3", "Installer", pkg=True),
          rows=["R3", "R21"]),
        S("add-cask-none", ["add", "-n", "cask:dkt-none"], rows=["R3"]),
        S("add-mise", ["add", "-n", "mise:dkt-tool"], rows=["R4", "R18", "R21"],
          rules=fx.mise_tool("dkt-tool", "aqua:o/dkt-tool asdf:x/y", "1.2.0", "A\ntool")
          + fx.nix_pkg("dkt-tool", bin="dkt-t")),
        S("add-mise-no-latest", ["add", "-n", "mise:dkt-tool"], rows=["R4", "R21"],
          rules=fx.mise_tool("dkt-tool", "aqua:o/dkt-tool", None) + fx.nix_no_pkg("dkt-tool")),
        S("add-mise-asdf-only", ["add", "-n", "mise:dkt-tool"], rows=["R4"],
          rules=fx.mise_tool("dkt-tool", "asdf:x/dkt-tool vfox:y/dkt-tool")),
        S("add-spec-npm", ["add", "-n", "npm:dkt-cow"], rows=["R6", "R19"],
          rules=fx.mise_spec("npm:dkt-cow", "1.6.0") + fx.npm("dkt-cow", "1.6.0", "Cow", {"dkt-cowsay": "cli.js"})),
        S("add-spec-npm-no-bin", ["add", "-n", "npm:dkt-lib"], rows=["R19"],
          rules=fx.mise_spec("npm:dkt-lib", "1.0.0") + fx.npm("dkt-lib", "1.0.0", "Lib")),
        S("add-spec-github", ["add", "-n", "github:owner/dkt-tool"], rows=["R6", "R18"],
          rules=fx.mise_spec("github:owner/dkt-tool", "0.3.0", "From GitHub") + fx.nix_pkg("dkt-tool", bin="dkt-x")),
        S("add-spec-version", ["add", "-n", "npm:@scope/dkt-pkg@1.2.3"], rows=["R19", "R20"],
          rules=fx.mise_spec("npm:@scope/dkt-pkg@1.2.3", "1.2.3")
          + fx.npm("@scope/dkt-pkg", "1.2.3", "", "dkt-bin")),
        S("add-spec-missing", ["add", "-n", "cargo:dkt-crate"], rows=["R6"]),
        S("add-scoped", ["add", "-n", "@scope/dkt-pkg"], rows=["R7", "R21"],
          rules=fx.npm("@scope/dkt-pkg", "2.0.0", "Scoped", {"dkt-s": "a.js", "dkt-a": "b.js"})),
        S("add-scoped-no-bin", ["add", "-n", "@scope/dkt-pkg"], rules=fx.npm("@scope/dkt-pkg", "2.0.0"),
          rows=["R7"]),
        S("add-invalid-name", ["add", "-n", "dkt/x"], rows=["R8"]),
    ]


def add_mas() -> List[S]:
    return [
        S("add-mas-taken", ["add", "-n", "mas:1234"], rules=fx.mas(), rows=["Ad5", "R5", "F3"]),
        S("add-mas-taken-yes", ["add", "-y", "mas:1234"], rules=fx.mas() + fx.apply_ok(), rows=["Ad5", "Ad15"]),
        S("add-mas-two-same-name", ["add", "-n", "mas:361285480", "mas:1234"], rows=["Ad5", "F3"],
          files={"repo/nix/mas.txt": ""}, rules=fx.mas()),
        S("add-mas-both-taken", ["add", "-n", "mas:1234"], rows=["Ad5"],
          files={"repo/nix/mas.txt": "409183694 Keynote\n5 Keynote (1234)\n"}, rules=fx.mas()),
        S("add-mas-three", ["add", "-y", "mas:361285480", "mas:1234", "mas:55"], rows=["Ad5", "Ad15", "F3"],
          files={"repo/nix/mas.txt": ""}, rules=fx.mas() + fx.build_once() + fx.apply_ok()),
        S("add-mas-two-words", ["add", "-n", "mas:1289583905"], rules=fx.mas(), rows=["R5"]),
        S("add-mas-not-installed", ["add", "-n", "mas:497799835"], rules=fx.mas({497799835: mas_info("Xcode")}),
          rows=["R5"]),
        S("add-mas-no-name", ["add", "-n", "mas:497799835"], rules=fx.mas({497799835: mas_info(None)}),
          rows=["R5"]),
        S("add-mas-unknown", ["add", "-n", "mas:1"], rules=fx.mas(), rows=["R5"]),
        S("add-mas-invalid", ["add", "-n", "mas:012"], rows=["R5"]),
        S("add-mas-not-installed-mas", ["add", "-n", "mas:1234"], files={"bin/mas": None}, skip=no_real_mas,
          rows=["R5"]),
        S("add-mas-no-list", ["add", "-n", "mas:1234"], files={"repo/nix/mas.txt": None}, rules=fx.mas(),
          rows=["Ad5"]),
        S("add-mas-declared", ["add", "-n", "mas:409183694"], rules=fx.mas(), rows=["Ad2", "D1"]),
    ]


def add_bare() -> List[S]:
    n = "dkt-tool"

    def a(name, rules, rows, argv=None):
        return S(name, argv or ["add", "-n", n], rules=rules, unordered=True, rows=rows)

    nix = fx.nix_pkg(n, "1.2.0", "From Nixpkgs")
    mise = fx.mise_tool(n, "aqua:o/dkt-tool", "1.2.0", "From mise")
    return [
        a("add-bare-app", bare(n, cask=fx.cask(n, "3", "App", app="T.app"), nix=nix), ["R11", "R9", "Pc1"]),
        a("add-bare-app-and-formula", bare(n, cask=fx.cask(n, app="T.app"), formula=fx.formula(n)), ["R10"]),
        a("add-bare-nix-current", bare(n, nix=nix, mise=mise, formula=fx.formula(n, "1.2.0"))
          + fx.compare("1.2.0", "1.2.0", 0), ["R12", "Pc1"]),
        a("add-bare-nix-newer", bare(n, nix=fx.nix_pkg(n, "1.10.0"), mise=fx.mise_tool(n, "aqua:o/t", "1.9.0"))
          + fx.compare("1.10.0", "1.9.0", 1), ["R12", "R17"]),
        a("add-bare-nix-older", bare(n, nix=fx.nix_pkg(n, "1.2.0"), mise=fx.mise_tool(n, "aqua:o/t", "v1.3.0"))
          + fx.compare("1.2.0", "1.3.0", -1), ["R12", "R17"]),
        a("add-bare-nix-older-rc", bare(n, nix=fx.nix_pkg(n, "2.0.0-rc1"), mise=fx.mise_tool(n, "aqua:o/t", "2.0.0"))
          + fx.compare("2.0.0-rc1", "2.0.0", 1), ["R17"]),
        a("add-bare-compare-fails", bare(n, nix=nix, mise=mise), ["R17"]),
        a("add-bare-nix-no-version", bare(n, nix=fx.nix_pkg(n, ""), mise=fx.mise_tool(n, "aqua:o/t", None)),
          ["R12"]),
        a("add-bare-nix-only", bare(n, nix=nix, pypi=fx.pypi(n, "1.0", "Py")), ["R13"]),
        a("add-bare-mise-only", bare(n, mise=mise), ["R13", "R18"]),
        a("add-bare-mise-asdf-only", bare(n, mise=fx.mise_tool(n, "asdf:x/y"), formula=fx.formula(n)),
          ["R14", "R21"]),
        a("add-bare-formula", bare(n, formula=fx.formula(n, "4.0", "Formula")), ["R14", "R18"]),
        a("add-bare-cask-cli", bare(n, cask=fx.cask(n, "5", "CLI cask", binary="dkt-c")), ["R14"]),
        a("add-bare-npm-only", bare(n, npm=fx.npm(n, "1.0.0", "npm", {n: "x.js"}), pypi=fx.pypi(n)), ["R15"]),
        a("add-bare-npm-no-bin", bare(n, npm=fx.npm(n, "1.0.0", "npm")), ["R16", "R21"]),
        a("add-bare-none", bare(n), ["R16"]),
        a("add-bare-nix-fails", fx.nix_eval_fails() + bare(n, nix=[], mise=mise), ["R9"]),
    ]


def add_checks() -> List[S]:
    nix_a = fx.nix_pkg("dkt-a", "1.0", "A")
    return [
        S("add-declared", ["add", "jq", "ripgrep", "nix:zed", "dkt-hunk", "@dkt-ai/dkt-code"],
          rows=["Ad2", "Ad10", "D2"]),
        S("add-duplicate", ["add", "-n", "nix:dkt-a", "nix:dkt-a"], rules=nix_a, rows=["Ad1"]),
        S("add-same-entry", ["add", "-n", "nix:dkt-a", "dkt-a"], rules=nix_a + bare("dkt-a", nix=nix_a),
          unordered=True, rows=["Ad4"]),
        S("add-existing-command-mise", ["add", "-n", "nix:dkt-a"], rows=["Ad6", "Ad9", "X1", "X2"],
          env={"MISE_DATA_DIR": "@SB/home/mise"},
          files={"home/mise/installs/dkt-a/2.0/bin/dkt-a": "#!/bin/sh\n"},
          rules=nix_a + fx.mise_which("dkt-a", "@SB/home/mise/installs/dkt-a/2.0/bin/dkt-a")),
        S("add-existing-command-default-mise-dir", ["add", "-n", "nix:dkt-a"], rows=["X2"],
          files={"home/.local/share/mise/installs/dkt-a/2.0/bin/dkt-a": "#!/bin/sh\n"},
          rules=nix_a + fx.mise_which("dkt-a", "@SB/home/.local/share/mise/installs/dkt-a/2.0/bin/dkt-a")),
        S("add-existing-command-nix", ["add", "-n", "nix:dkt-a"], rows=["X2"],
          rules=nix_a + fx.mise_which("dkt-a", "/nix/store/00000000000000000000000000000000-dkt-a-1.0/bin/dkt-a")),
        S("add-existing-command-cellar", ["add", "-n", "nix:dkt-a"], rows=["X2"],
          rules=nix_a + fx.mise_which("dkt-a", "/opt/homebrew/Cellar/dkt-a/1.0/bin/dkt-a")),
        S("add-existing-command-cask", ["add", "-n", "nix:dkt-a"], rows=["X2"],
          rules=nix_a + fx.mise_which("dkt-a", "/opt/homebrew/Caskroom/dkt-a/1.0/dkt-a")),
        S("add-existing-command-other", ["add", "-n", "nix:dkt-a"], rows=["X2"],
          files={"home/bin/real-a": "#!/bin/sh\n"}, env={},
          rules=nix_a + fx.mise_which("dkt-a", "@SB/home/bin/real-a")),
        S("add-force", ["add", "-n", "-f", "nix:dkt-a"], rows=["Ad6"],
          rules=nix_a + fx.mise_which("dkt-a", "/opt/homebrew/Cellar/dkt-a/1.0/bin/dkt-a")),
        S("add-two-same-command", ["add", "-n", "nix:dkt-a", "brew:dkt-b"], rows=["Ad7", "Ad9"],
          rules=nix_a + fx.formula("dkt-b") + fx.nix_pkg("dkt-b", bin="dkt-a")),
        S("add-errors-and-plans", ["add", "-n", "nix:dkt-a", "nix:9bad", "brew:dkt-none"], rules=nix_a,
          rows=["Ad3", "Ad9"]),
    ]


def add_apply() -> List[S]:
    nix_a = fx.nix_pkg("dkt-a", "1.0", "A")
    return [
        S("add-yes-nix", ["add", "-y", "nix:dkt-a"], rules=nix_a + fx.build_once() + fx.apply_ok(),
          rows=["Ad13", "Ad15", "F1"]),
        S("add-yes-cask-no-newline", ["add", "-y", "cask:dkt-app"], rows=["Ad15", "F1"],
          rules=fx.cask("dkt-app", app="A.app") + fx.build_once() + fx.apply_ok()),
        S("add-yes-formula", ["add", "-y", "brew:dkt-f"], rows=["Ad15"],
          rules=fx.formula("dkt-f") + fx.nix_no_pkg("dkt-f") + fx.build_once() + fx.apply_ok()),
        S("add-yes-mise", ["add", "-y", "mise:dkt-tool"], rows=["Ad14", "Ad15"],
          rules=fx.mise_tool("dkt-tool", "aqua:o/t") + fx.nix_no_pkg("dkt-tool") + fx.mise_ok("use -g dkt-tool$")),
        S("add-yes-mixed", ["add", "-y", "npm:dkt-cow", "nix:dkt-a", "pipx:dkt-py", "brew:dkt-f"],
          rows=["Ad12", "Ad13", "Ad14", "Ad15"],
          rules=fx.mise_spec("npm:dkt-cow") + fx.npm("dkt-cow", bin={"dkt-cow": "c.js"}) + nix_a
          + fx.mise_spec("pipx:dkt-py") + fx.nix_no_pkg("dkt-py") + fx.formula("dkt-f") + fx.nix_no_pkg("dkt-f")
          + fx.build_once() + fx.apply_ok() + fx.mise_ok("use -g ")),
        S("add-yes-bare-pipx", ["add", "-y", "pipx:dkt-py"], rows=["Ad15"],
          rules=fx.mise_spec("pipx:dkt-py") + fx.nix_no_pkg("dkt-py") + fx.mise_ok("use -g ")),
        S("add-no-terminal", ["add", "nix:dkt-a"], rules=nix_a, rows=["Ad12", "Q1"]),
        S("add-tty-no", ["add", "nix:dkt-a"], rules=nix_a, tty="n\n", rows=["Ad12"]),
        S("add-tty-yes", ["add", "nix:dkt-a", "brew:dkt-f"], tty="y\n", rows=["Ad12", "Ad13"],
          rules=nix_a + fx.formula("dkt-f") + fx.nix_no_pkg("dkt-f") + fx.build_once() + fx.apply_ok()),
        S("add-build-fails", ["add", "-y", "nix:dkt-a"], rules=nix_a + fx.build(fails=True), rows=["Ad13"]),
        S("add-apply-fails", ["add", "-y", "nix:dkt-a"], rows=["Ad13"], rules=nix_a + fx.build() + fx.switch_fails()),
        S("add-mise-use-fails", ["add", "-y", "mise:dkt-tool", "npm:dkt-cow"], rows=["Ad14"],
          rules=fx.mise_tool("dkt-tool", "aqua:o/t") + fx.nix_no_pkg("dkt-tool") + fx.mise_spec("npm:dkt-cow")
          + fx.npm("dkt-cow", bin={"dkt-cow": "c.js"}) + fx.mise_fails("use -g dkt-tool")),
        S("add-mas-yes", ["add", "-y", "mas:1289583905"], rules=fx.mas() + fx.build_once() + fx.apply_ok(),
          rows=["Ad15"]),
    ]


def picker() -> List[S]:
    """The picker of add (PARITY.md, Pk and DV2): a bare name in a terminal. The list is questionary: the arrow
    keys move, Enter selects; below the candidates, "Skip this tool" and "Stop, and change nothing"."""
    n = "dkt-tool"
    xterm = {"TERM": "xterm-256color"}
    nix_mise = bare(n, nix=fx.nix_pkg(n, "1.2.0", "From Nixpkgs"), mise=fx.mise_tool(n, "aqua:o/t", "1.2.0", "mise")) \
        + fx.compare("1.2.0", "1.2.0", 0)
    down, up, enter = "\x1b[B", "\x1b[A", "\r"

    def p(name, rules, keys, rows, argv=None, env=xterm, then="", **kw):
        """keys go to the list once it shows; then is the answer to "Add the tool?"."""
        typed = [("(↑/↓, Enter)", keys)] + ([("Add the", then)] if then else [])
        return S(name, argv or ["add", n], rules=rules, tty=typed, env=env, unordered=True, rows=rows,
                 no_terminal=True, **kw)

    return [
        p("pick-default", nix_mise, enter, ["Pk1", "Pk2", "Pk4", "Pk5"], then="n\r"),
        p("pick-other", nix_mise + fx.mise_ok("use -g dkt-tool$"), down + enter, ["Pk3", "Pk5"], then="y\r"),
        # j: mise, down: Skip, k: mise, up: nix.
        p("pick-keys", nix_mise, f"j{down}k{up}" + enter, ["Pk3"], then="n\r"),
        p("pick-skip", nix_mise, down + down + enter, ["Pk3"]),
        p("pick-skip-one", nix_mise + fx.nix_pkg("dkt-a", "1.0", "A"), down + down + enter, ["Pk3"],
          argv=["add", n, "nix:dkt-a"], then="n\r"),
        # The list wraps: up from the first row is "Stop, and change nothing".
        p("pick-stop", nix_mise, up + enter, ["Pk3"]),
        p("pick-ctrl-c", nix_mise, "\x03", ["Pk7"]),
        p("pick-app-and-formula", bare(n, cask=fx.cask(n, "3", "App", app="T.app"), formula=fx.formula(n, "4", "CLI")),
          up + enter, ["Pk4", "Pk5"], then="n\r"),
        p("pick-app-and-formula-enter", bare(n, cask=fx.cask(n, "3", "App", app="T.app"),
                                             formula=fx.formula(n, "4", "CLI")), enter, ["Pk4"]),
        p("pick-registry-only", bare(n, npm=fx.npm(n, "1.0.0", "npm", {n: "x.js"}), pypi=fx.pypi(n, "2.0", "Py"))
          + fx.nix_no_pkg(n), up + up + enter, ["Pk2", "Pk4", "Pk5"], then="n\r"),
        p("pick-installed", nix_mise + fx.mise_which(n, "/opt/homebrew/Cellar/dkt-tool/1.0/bin/dkt-tool"),
          down + enter, ["Pk5"]),
        S("pick-numbered", ["add", n], rules=nix_mise, tty="x\n9\n2\nn\n", env={"TERM": "dumb"}, unordered=True,
          rows=["Pk6"]),
        S("pick-numbered-default", ["add", n], rules=nix_mise, tty="\nn\n", env={}, unordered=True, rows=["Pk6"]),
        S("pick-numbered-end-of-input", ["add", n], rules=nix_mise, tty="\x04", env={"TERM": "dumb"}, unordered=True,
          rows=["Pk6"]),
        S("pick-not-one-source", ["add", n], rules=bare(n, nix=fx.nix_pkg(n, "1.2.0", "From Nixpkgs")), tty="n\n",
          env=xterm, unordered=True, rows=["Pk1"]),
        S("pick-not-dry-run", ["add", "-n", n], rules=nix_mise, tty="", env=xterm, unordered=True, rows=["Pk1"]),
        S("pick-not-yes", ["add", "-y", n], rules=nix_mise + fx.build_once() + fx.apply_ok(), tty="", env=xterm,
          unordered=True, rows=["Pk1"]),
        S("pick-not-nix-failed", ["add", n], rules=fx.nix_eval_fails() + nix_mise, tty="", env=xterm, unordered=True,
          rows=["Pk1"]),
        S("pick-not-none", ["add", n], rules=bare(n), tty="", env=xterm, unordered=True, rows=["Pk1"]),
    ]


def remove() -> List[S]:
    ok = fx.build_once() + fx.apply_ok()
    return [
        S("remove-nix", ["remove", "-y", "jq"], rules=ok, rows=["Rm9", "F2"]),
        S("remove-dry-run", ["remove", "-n", "jq", "zed"], rows=["Rm5", "Rm7"]),
        S("remove-base", ["remove", "ripgrep"], rows=["Rm2", "Rm6"]),
        S("remove-base-and-repo", ["remove", "fd"], files={"repo/nix/packages.txt": "jq\nfd\n"}, rows=["Rm2"]),
        S("remove-missing", ["remove", "dkt-x"], rows=["Rm3"]),
        S("remove-prefix-other-list", ["remove", "nix:zed"], rows=["Rm1", "Rm3"]),
        S("remove-prefix-files", ["remove", "brew:dkt-x", "cask:dkt-x", "mas:1", "npm:dkt-x"], rows=["Rm1", "Rm3"]),
        S("remove-multiple", ["remove", "jq"], files={"repo/nix/brews.txt": "jq\n"}, rows=["Rm4"]),
        S("remove-multiple-prefix", ["remove", "-n", "nix:jq"], files={"repo/nix/brews.txt": "jq\n"}, rows=["Rm1"]),
        S("remove-cask", ["remove", "-y", "zed"], rows=["Rm9"],
          rules=ok + fx.brew(r"^list --cask zed$", "zed\n") + fx.brew(r"^uninstall --cask zed$")),
        S("remove-formula-not-installed", ["remove", "-y", "dkt-f"], files={"repo/nix/brews.txt": "dkt-f\n"},
          rules=ok, rows=["Rm9"]),
        S("remove-uninstall-fails", ["remove", "-y", "dkt-f"], files={"repo/nix/brews.txt": "dkt-f\n"},
          rules=ok + fx.brew(r"^list --formula dkt-f$") + fx.brew(r"^uninstall", exit=1, stderr="Error: busy\n"),
          rows=["Rm9"]),
        S("remove-mas", ["remove", "-y", "mas:409183694"], rules=ok, rows=["Rm9"]),
        S("remove-mise", ["remove", "-y", "dkt-hunk"], rules=fx.mise_ok("unuse -g "), rows=["Rm10"]),
        S("remove-mise-fails", ["remove", "-y", "npm:cowsay", "dkt-hunk"], rules=fx.mise_fails("unuse -g npm:cowsay"),
          rows=["Rm10"]),
        S("remove-comments", ["remove", "-y", "jq"], rules=ok, rows=["F2"],
          files={"repo/nix/packages.txt": "# CLIs\njq # JSON\njqq\n# jq\nlast"}),
        S("remove-two-lines", ["remove", "-y", "jq"], rows=["Rm4", "L6"],
          files={"repo/nix/packages.txt": "# CLIs\njq # JSON\n  jq\n"}),
        S("remove-no-terminal", ["remove", "jq"], rows=["Rm8"]),
        S("remove-tty-no", ["remove", "jq"], tty="n\n", rows=["Rm8"]),
        S("remove-duplicate", ["remove", "-n", "jq", "nix:jq"], rows=["Rm5"]),
        S("remove-build-fails", ["remove", "-y", "jq"], rules=fx.build(fails=True), rows=["Ad13", "Rm9"]),
    ]


def all_scenarios() -> List[S]:
    out = (start() + dispatch() + options() + apply() + upgrade() + clean() + recovery() + wizard()
           + pkg_parse() + listing() + info() + add_prefix() + add_mas() + add_bare() + add_checks() + add_apply()
           + picker() + remove())
    names = [s.name for s in out]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ValueError(f"scenario names used more than once: {dup}")
    return out


SCENARIOS = all_scenarios()
