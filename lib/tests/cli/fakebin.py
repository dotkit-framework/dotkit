"""A fake external command for the parity tests: nix, brew, mise, mas, curl, sudo, and the others.

The harness puts one small sh file per command name on PATH. Each one runs
this file with the name of the command and its arguments:

    fakebin.py <tool> <args>...

FAKE_DIR is the directory of the fake tools of a sandbox:
  rules.json  the replies, in order; the first rule that matches replies
  log.jsonl   one line per call: the tool, argv, cwd, some variables, stdin
  state.json  the rules with "once" that are used up

A rule is an object:
  tool     the command name
  re       a regex for the arguments joined with spaces (re.search); "" matches all
  stdout, stderr, exit    the reply (default "", "", 0)
  stdin    true: read stdin to the end and log it
  once     true: the rule matches one time only
  actions  a list of changes before the reply:
           {"write": PATH, "text": T}, {"append": PATH, "text": T},
           {"copy": PATH, "to": PATH}, {"link_after": OPTION, "to": TARGET}
           (a symlink at the argument after OPTION), {"stderr_file": PATH}

@SB in a rule is the sandbox directory. A call without a rule exits 1 with
"fake <tool>: no rule" on stderr: for a lookup that means "not found", and the
log shows the call.
"""

import fcntl
import json
import os
import re
import sys

# The variables that change what a command does. The log keeps them when they are set.
WATCH = ("DOTFILES", "DOTKIT_DIR", "HOMEBREW_NO_AUTO_UPDATE", "HOMEBREW_NO_INSTALL_CLEANUP", "NIX_REMOTE",
         "MISE_DATA_DIR")


def main() -> int:
    tool, argv = sys.argv[1], sys.argv[2:]
    fake = os.environ["FAKE_DIR"]
    sb = os.path.dirname(fake)
    with open(os.path.join(fake, "lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with open(os.path.join(fake, "rules.json"), encoding="utf-8") as f:
            rules = json.load(f)
        state_path = os.path.join(fake, "state.json")
        used = []
        if os.path.exists(state_path):
            with open(state_path, encoding="utf-8") as f:
                used = json.load(f)
        joined = " ".join(argv)
        rule = None
        for i, r in enumerate(rules):
            if r["tool"] == tool and i not in used and re.search(r.get("re", ""), joined):
                rule = r
                if r.get("once"):
                    used.append(i)
                    with open(state_path, "w", encoding="utf-8") as f:
                        json.dump(used, f)
                break
    entry = {"tool": tool, "argv": argv, "cwd": os.getcwd()}
    env = {k: os.environ[k] for k in WATCH if k in os.environ}
    if env:
        entry["env"] = env
    if rule and rule.get("stdin"):
        entry["stdin"] = sys.stdin.read()
    with open(os.path.join(fake, "log.jsonl"), "a", encoding="utf-8") as log:
        fcntl.flock(log, fcntl.LOCK_EX)
        log.write(json.dumps(entry) + "\n")
    if rule is None:
        sys.stderr.write(f"fake {tool}: no rule for: {joined}\n")
        return 1

    def path(p: str) -> str:
        return p.replace("@SB", sb)

    for a in rule.get("actions", []):
        if "write" in a:
            with open(path(a["write"]), "w", encoding="utf-8") as f:
                f.write(path(a["text"]))
        elif "append" in a:
            with open(path(a["append"]), "a", encoding="utf-8") as f:
                f.write(path(a["text"]))
        elif "copy" in a:
            with open(path(a["copy"]), encoding="utf-8") as src, open(path(a["to"]), "w", encoding="utf-8") as dst:
                dst.write(src.read())
        elif "link_after" in a:
            link = argv[argv.index(a["link_after"]) + 1]
            os.makedirs(os.path.dirname(link), exist_ok=True)
            if os.path.lexists(link):
                os.unlink(link)
            os.symlink(path(a["to"]), link)
        elif "stderr_file" in a:
            with open(path(a["stderr_file"]), encoding="utf-8") as f:
                sys.stderr.write(f.read())
    sys.stdout.write(path(rule.get("stdout", "")))
    sys.stderr.write(path(rule.get("stderr", "")))
    return int(rule.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main())
