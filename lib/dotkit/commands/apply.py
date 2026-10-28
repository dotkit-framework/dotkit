"""dotkit apply: Nix, Homebrew, and mise (BEHAVIOR.md, rows A)."""

from __future__ import annotations

import os

from ..core import nix, proc, ui
from ..core.env import Env

# The Homebrew of the Mac. The preview runs it by this path, as nix-darwin does.
BREW = "/opt/homebrew/bin/brew"
REPORT_END = "Run `brew bundle cleanup --force` to make these changes."
DOWNLOADS = "Would `brew cleanup`:"


def apply(env: Env, dry_run: bool) -> int:
    n = nix.Nix(env)
    if dry_run:
        code = n.build_system()
        if code:
            return code
        ui.out("Nix package changes:")
        code = proc.run(["nix", "store", "diff-closures", "/run/current-system", str(env.result)]).code
        if code:
            return code
        code = preview_brew_cleanup(env, n)
        if code:
            return code
        code = proc.mise(env.home, ["install", "--dry-run"]).code
        if code:
            return code
        ui.note("No change. Run dotkit apply to apply.")
        return 0
    code = n.switch()
    if code:
        return code
    return proc.mise(env.home, ["install"]).code


def preview_brew_cleanup(env: Env, n: nix.Nix) -> int:
    """Shows what `brew bundle cleanup` removes for the built Brewfile, when activation uninstalls
    (homebrew.onActivation.cleanup is not "none"). Returns an exit code (BEHAVIOR.md, A7 and A8)."""
    if not (os.path.isfile(BREW) and os.access(BREW, os.X_OK)):
        return 0
    # A failed evaluation gives no text, which is not "none": the next evaluation stops with its error.
    if n.eval_raw("darwinConfigurations.mac.config.homebrew.onActivation.cleanup").value == "none":
        return 0
    # Use the candidate Brewfile, not the active HOMEBREW_BUNDLE_FILE.
    brewfile = env.repo / ".build/Brewfile"
    r = n.brewfile()
    brewfile.write_text(r.out, encoding="utf-8")
    if not r.ok:
        return r.code
    # Without redirected input, Homebrew can prompt and then do the cleanup.
    cleanup = proc.run([BREW, "bundle", "cleanup", f"--file={brewfile}"], env={"HOMEBREW_NO_AUTO_UPDATE": "1"},
                       stdin=proc.DEVNULL, stdout=proc.CAPTURE)
    output, status = cleanup.value, cleanup.code
    # Exit 1 also reports real errors. Accept it only after a complete cleanup report.
    if output.rsplit("\n", 1)[-1] == REPORT_END:
        # The suggested command would use the active Brewfile, not the candidate.
        output = output[:-len(REPORT_END)]
        if status == 1:
            status = 0
        # The report ends with the old downloads that `brew cleanup` deletes. The apply deletes them too,
        # but they are not packages: do not show them.
        if output.startswith(DOWNLOADS):
            output = ""
        elif "\n" + DOWNLOADS in output:
            output = output[:output.index("\n" + DOWNLOADS)]
    if output:
        ui.out("Homebrew removes:\n" + (output[:-1] if output.endswith("\n") else output))
    if status:
        raise ui.die(f"the Homebrew cleanup preview failed with exit code {status}.")
    return 0
