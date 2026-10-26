"""The readers of nix/taps.txt and nix/mas.txt (nix/read-taps.nix, nix/read-mas.nix), evaluated by Nix,
and the Brewfile that nix-darwin builds from the lists.

They need nix and the inputs of flake.lock. Without nix (the sandbox of
`nix flake check`), they are skipped; CI runs them in the check job.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from dotkit_migrate import writer
from dotkit_migrate.model import AdoptOptions
from tests import fake, test_golden

NIX = shutil.which("nix")
RUBY = "/usr/bin/ruby" if os.access("/usr/bin/ruby", os.X_OK) else None


def nix_string(s: str) -> str:
    return json.dumps(s).replace("${", "\\${")


def nix_paths(files: list[Path]) -> str:
    return "[ " + " ".join(f"(/. + {nix_string(str(p))})" for p in files) + " ]"


def nix_eval(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([NIX, "eval", "--impure", *args], capture_output=True, text=True, timeout=600)


def eval_lists(taps: list[Path], mas: list[Path]) -> dict:
    """homebrew.taps and homebrew.masApps, as nix/darwin.nix sets them from these files."""
    root = fake.ROOT / "nix"
    expr = (f"lib: {{ taps = import (/. + {nix_string(str(root / 'read-taps.nix'))}) lib {nix_paths(taps)}; "
            f"masApps = import (/. + {nix_string(str(root / 'read-mas.nix'))}) lib {nix_paths(mas)}; }}")
    out = nix_eval("--json", "--inputs-from", str(fake.ROOT), "nixpkgs#lib", "--apply", expr)
    if out.returncode != 0:
        raise AssertionError(f"nix eval failed: {out.stderr}")
    return json.loads(out.stdout)


def eval_brewfile(**lists: list[Path]) -> subprocess.CompletedProcess:
    """homebrew.brewfile of a Mac from the dotkit module, with these dotkit.lists."""
    settings = " ".join(f"{k} = {nix_paths(v)};" for k, v in lists.items())
    return eval_mac(f"{{ dotkit.lists = {{ {settings} }}; }}", "brewfile")


def eval_mac(module: str, attr: str, *args: str) -> subprocess.CompletedProcess:
    """config.homebrew.<attr> of a Mac from the dotkit module and this module."""
    expr = f"""
      let
        f = builtins.getFlake {nix_string("git+file://" + str(fake.ROOT))};
        user = {{ name = "example"; home = "/Users/example"; dotfiles = "/Users/example/.dotfiles"; }};
        mac = f.inputs.nix-darwin.lib.darwinSystem {{
          specialArgs = {{ inherit user; }};
          modules = [ f.darwinModules.default ({module}) ];
        }};
      in mac.config.homebrew.{attr}
    """
    return nix_eval(*(args or ("--raw",)), "--option", "warn-dirty", "false", "--expr", expr)


@unittest.skipUnless(NIX, "no nix")
class NixListTests(unittest.TestCase):
    def setUp(self):
        self.dir = fake.tempdir()
        self.addCleanup(fake.remove, self.dir)

    def write(self, name: str, text: str) -> Path:
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_readers(self):
        taps = self.write("taps.txt", (
            "# Homebrew taps.\n"
            "hashicorp/tap\n"
            "\n"
            "  acme/tools   git@gitlab.com:acme/homebrew-tools.git  # not on GitHub\n"
        ))
        more_taps = self.write("more-taps.txt", (
            "hashicorp/tap https://example.com/other.git\n"
            "acme/tools\n"
            "other/tap\t# a comment\n"
        ))
        mas = self.write("mas.txt", (
            "# App Store apps.\n"
            "497799835 Xcode\n"
            "1289583905   Pixelmator Pro  \n"
            "1234567890 C# Toolkit\n"
            "  # 1 Not an app\n"
            "1274495053 Notes \u270f\ufe0f\n"
            "1500000001\tCaf\u00e9 \"Pro\" ${x}\n"
            "\n"
            "409183694 Keynote\n"
            "409183695 Keynote\n"
        ))
        more_mas = self.write("more-mas.txt", "1 Xcode\n2 Pages\n")
        self.assertEqual(eval_lists([taps, more_taps], [mas, more_mas]), {
            "taps": [
                "hashicorp/tap",
                {"name": "acme/tools", "clone_target": "git@gitlab.com:acme/homebrew-tools.git"},
                "other/tap",
            ],
            "masApps": {
                "Xcode": 497799835,
                "Pixelmator Pro": 1289583905,
                "C\\# Toolkit": 1234567890,
                "Notes \u270f\ufe0f": 1274495053,
                "Caf\u00e9 \\\"Pro\\\" ${x}": 1500000001,
                "Keynote": 409183694,
                "Pages": 2,
            },
        })

    def test_the_plan_evaluates(self):
        """The lists that setup writes for the kitchen-sink Mac of the goldens."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        runner, env = test_golden.kitchen_sink(h)
        p = fake.session(h, runner, env, check=test_golden.check_syntax)
        files = p.build(test_golden.kitchen_sink_choices(p, str(h.home / ".dotfiles"))).files
        taps = self.write("taps.txt", files["nix/taps.txt"])
        mas = self.write("mas.txt", files["nix/mas.txt"])
        self.assertEqual(eval_lists([taps], [mas]), {
            "taps": ["hashicorp/tap", {"name": "acme/tools", "clone_target": "git@gitlab.com:acme/homebrew-tools.git"}],
            "masApps": {
                "Xcode": 497799835,
                "Pixelmator Pro": 1289583905,
                "C\\# Toolkit": 1234567890,
                "Notes \u270f\ufe0f": 1274495053,
                "Caf\u00e9 \\\"Pro\\\"": 1500000001,
                "Keynote": 409183694,
                "Keynote (409183695)": 409183695,
            },
        })

    def test_a_name_written_by_hand(self):
        """dotkit migrate gives a scanned app a label when nix/darwin.nix has its name, and the Mac evaluates."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        repo = h.home / ".dotfiles"
        h.write(".dotfiles/nix/mas.txt", (fake.ROOT / "templates/default/nix/mas.txt").read_text())
        darwin = h.write(".dotfiles/nix/darwin.nix",
                         '{ dotkit.lists.masApps = [ ./mas.txt ]; homebrew.masApps."Keynote" = 1; }\n')
        module = f"import (/. + {nix_string(str(darwin))})"
        out = eval_mac(module, "masApps", "--json")
        self.assertEqual(out.returncode, 0, out.stderr)
        declared = {"masApps": json.loads(out.stdout)}
        self.assertEqual(declared["masApps"], {"Keynote": 1})
        result = fake.session(h, fake.brew_runner(mas="409183694 Keynote (14.2)\n")).adopt(repo, declared,
                                                                                              AdoptOptions())
        self.assertEqual(result.output, ["App Store apps -> nix/mas.txt:", "  409183694 Keynote (409183694)",
                                         "App Store apps, added under another name:",
                                         "  `Keynote` (ID 409183694): another app has this name, so nix/mas.txt "
                                         "lists it as `Keynote (409183694)`."])
        writer.apply_edits(result, repo)
        out = eval_mac(module, "masApps", "--json")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), {"Keynote": 1, "Keynote (409183694)": 409183694})
        # Without the label, the build would have two values for homebrew.masApps.Keynote.
        (repo / "nix/mas.txt").write_text("409183694 Keynote\n")
        self.assertNotEqual(eval_mac(module, "masApps", "--json").returncode, 0)

    def test_empty_and_template_files(self):
        t = fake.ROOT / "templates/default/nix"
        self.assertEqual(eval_lists([t / "taps.txt"], [t / "mas.txt"]), {"taps": [], "masApps": {}})
        self.assertEqual(eval_lists([], []), {"taps": [], "masApps": {}})

    def test_bad_lines_stop_the_evaluation(self):
        for name, text in (("taps", "acme/tools https://x extra\n"), ("taps", "notatap\n"),
                           ("taps", "# taps\n\nacme/tools https://x/\"y\n"), ("taps", "acme/tools https://x\\y\n"),
                           ("taps", "acme/to\"ols\n"), ("mas", "Xcode 497799835\n"), ("mas", "497799835\n")):
            with self.subTest(name=name, text=text):
                f = self.write(f"{name}.txt", text)
                line = len(text.splitlines())
                with self.assertRaisesRegex(AssertionError, f"{name}.txt line {line}: .* is not a"):
                    eval_lists([f] if name == "taps" else [], [f] if name == "mas" else [])

    def test_brewfile(self):
        """App Store names reach the Ruby Brewfile escaped: as text, and no Ruby code runs."""
        names = ["Caf\u00e9 \"Pro\"", "Some #{`touch $TMP/pwned`}", "Back\\slash", "#@ivar #$0", "C# Editor"]
        mas = self.write("mas.txt", "".join(f"{i} {n}\n" for i, n in enumerate(names, 1)))
        out = eval_brewfile(masApps=[mas])
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = [line for line in out.stdout.splitlines() if line.startswith("mas ")]
        self.assertEqual(lines, [
            'mas "Back\\\\slash", id: 3',
            'mas "C\\# Editor", id: 5',
            'mas "Caf\u00e9 \\"Pro\\"", id: 1',
            'mas "Some \\#{`touch $TMP/pwned`}", id: 2',
            'mas "\\#@ivar \\#$0", id: 4',
        ])
        if not RUBY:
            return
        env = dict(os.environ, TMP=str(self.dir))
        for line in lines:
            m = re.fullmatch(r'mas (".*"), id: (\d+)', line)
            out = subprocess.run([RUBY, "-e", f"print {m.group(1)}"], capture_output=True, text=True, env=env,
                                 timeout=60)
            self.assertEqual((out.returncode, out.stdout), (0, names[int(m.group(2)) - 1]), out.stderr)
        self.assertFalse((self.dir / "pwned").exists(), "the Brewfile ran Ruby code")

    def test_a_bad_brew_name_stops_the_evaluation(self):
        for option in ("brews", "casks"):
            with self.subTest(option=option):
                f = self.write(f"{option}.txt", 'wget\nbad"name\n')
                out = eval_brewfile(**{option: [f]})
                self.assertNotEqual(out.returncode, 0)
                self.assertIn(f"dotkit.lists.{option}: 'bad\"name' is not a Homebrew name.", out.stderr)


if __name__ == "__main__":
    unittest.main()
