"""Read an old shell startup file: carry the safe lines, report the rest.

The parser is line based and simple on purpose. It carries:
  - exports and assignments with a plain value (text, $HOME, ~, or a
    variable that an earlier carried line sets),
  - PATH additions of directories that exist and that dotkit does not add,
  - aliases, and functions that do not use a framework or a version manager.
Every other line goes to the report with its line numbers, in a category
with a next step. Lines with a secret go to a local file, never to the
repository.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field

from . import secrets

# Categories of the lines that are not carried, in report order, with the
# next step for each one.
DONE = "Done by dotkit"
FRAMEWORK = "Shell framework"
VERSION_MANAGER = "Version manager"
PROMPT = "Prompt"
COMPLETION = "Completion"
OPTIONS = "Shell options and key bindings"
SOURCED = "Sourced files"
PATHS = "PATH changes"
EXPORTS = "Exports with commands or other variables"
BLOCKS = "Conditionals and blocks"
COMMANDS = "Commands"

CATEGORIES: tuple[tuple[str, str], ...] = (
    (DONE, "Nothing to do: the dotkit base sets this up (Homebrew, completion, fzf, zoxide, "
           "Starship, mise, direnv, suggestions, syntax colors)."),
    (FRAMEWORK, "dotkit has completion, suggestions, syntax colors, and a prompt without a framework. "
                "Add the plugin lines you miss to {zshrc}. Remove the framework directory when you no "
                "longer need it."),
    (VERSION_MANAGER, "mise replaces it, see \"Version managers\" below. mise reads .nvmrc, "
                      ".node-version, .python-version, and .ruby-version files."),
    (PROMPT, "dotkit draws the prompt with Starship (~/.config/starship.toml). To keep your own prompt, "
             "set `dotkit.zsh.integrations.starship.enable = false;` in nix/shell.nix and add the lines "
             "to {zshrc}."),
    (COMPLETION, "dotkit runs compinit. Put fpath additions in config/zsh/zshenv (~/.zshenv.local), "
                 "which zsh reads before compinit, and compdef lines in {zshrc}."),
    (OPTIONS, "dotkit sets its own options (nix/modules/zsh/lib/options.zsh in dotkit). Copy the ones "
              "you want to {zshrc}. The keymap is in nix/shell.nix."),
    (SOURCED, "Check that the file still exists and is still needed, then copy the line to {zshrc}."),
    (PATHS, "The directory does not exist, or the line replaces PATH. Add a directory you still need "
            "to {zshrc} as path=(/the/dir $path)."),
    (EXPORTS, "Copy the line to {zshenv} if you still need it."),
    (BLOCKS, "Copy the block to {zshrc} if you still need it."),
    (COMMANDS, "These run at each shell start. Copy the line to {zshrc} if you still need it."),
)

# Variables that dotkit sets, or that only a framework or a version manager reads.
SKIP_VARS = {
    "PATH": PATHS, "FPATH": COMPLETION, "MANPATH": DONE, "INFOPATH": DONE,
    "HOMEBREW_PREFIX": DONE, "HOMEBREW_CELLAR": DONE, "HOMEBREW_REPOSITORY": DONE,
    "ZSH": FRAMEWORK, "ZSH_THEME": FRAMEWORK, "ZSH_CUSTOM": FRAMEWORK, "ZSH_CACHE_DIR": FRAMEWORK,
    "ZDOTDIR": FRAMEWORK, "plugins": FRAMEWORK, "DISABLE_AUTO_UPDATE": FRAMEWORK,
    "DISABLE_UPDATE_PROMPT": FRAMEWORK, "UPDATE_ZSH_DAYS": FRAMEWORK, "DISABLE_AUTO_TITLE": FRAMEWORK,
    "ENABLE_CORRECTION": FRAMEWORK, "COMPLETION_WAITING_DOTS": FRAMEWORK, "HIST_STAMPS": FRAMEWORK,
    "CASE_SENSITIVE": FRAMEWORK, "HYPHEN_INSENSITIVE": FRAMEWORK, "DISABLE_MAGIC_FUNCTIONS": FRAMEWORK,
    "DISABLE_LS_COLORS": FRAMEWORK, "DISABLE_UNTRACKED_FILES_DIRTY": FRAMEWORK, "ZINIT_HOME": FRAMEWORK,
    "ZINIT": FRAMEWORK, "ZIM_HOME": FRAMEWORK, "ANTIDOTE_HOME": FRAMEWORK,
    "NVM_DIR": VERSION_MANAGER, "PYENV_ROOT": VERSION_MANAGER, "RBENV_ROOT": VERSION_MANAGER,
    "ASDF_DIR": VERSION_MANAGER, "ASDF_DATA_DIR": VERSION_MANAGER, "SDKMAN_DIR": VERSION_MANAGER,
    "VOLTA_HOME": VERSION_MANAGER, "FNM_DIR": VERSION_MANAGER, "JENV_ROOT": VERSION_MANAGER,
    "NODENV_ROOT": VERSION_MANAGER, "GOENV_ROOT": VERSION_MANAGER,
    "JAVA_HOME": DONE, "PS1": PROMPT, "PROMPT": PROMPT, "RPROMPT": PROMPT, "RPS1": PROMPT, "PS2": PROMPT,
}

# (category, pattern). The first match wins. Checked on the whole statement.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (DONE, re.compile(
        r"brew shellenv|starship init|zoxide init|mise activate|direnv hook|fzf --zsh|fzf --bash|"
        r"\.fzf\.(zsh|bash)|fzf/shell/|zsh-autosuggestions|zsh-syntax-highlighting|"
        r"zsh-history-substring-search|zsh-completions|zsh-abbr|\bcompinit\b|bashcompinit|"
        r"nix-daemon\.sh|hm-session-vars|/etc/profiles/per-user|/opt/homebrew/bin/brew\b|"
        r"(source|\.)\s+[\"']?(~|\$HOME|\$\{HOME\})/\.bashrc[\"']?\s*$|"
        r"^\s*typeset\s+-U\s+(path|PATH)(\s+(path|PATH))?\s*$")),
    (FRAMEWORK, re.compile(
        r"oh-my-zsh|\$ZSH\b|\$\{ZSH\}|ZSH_THEME|\bomz\b|prezto|zprezto|\bzinit\b|zi\.zsh|antigen|"
        r"antidote|\bzplug\b|zimfw|\.zim\b|powerlevel10k|powerlevel9k|POWERLEVEL9K|\bp10k\b|"
        r"p10k-instant-prompt|\bplugins=\(|zsh_plugins")),
    (VERSION_MANAGER, re.compile(
        r"\b(nvm|NVM_DIR|pyenv|PYENV_ROOT|rbenv|rvm|asdf|sdkman|SDKMAN|jenv|volta|VOLTA_HOME|fnm|"
        r"nodenv|goenv|chruby|tfenv|conda|mamba|micromamba)\b")),
    (PROMPT, re.compile(r"\b(PS1|PROMPT|RPROMPT|RPS1|promptinit|prompt\s+\w+)\b")),
    (COMPLETION, re.compile(r"\b(compdef|fpath|complete\s+-[A-Za-z]|_comp_options|zstyle\s+':completion)")),
    (OPTIONS, re.compile(r"^\s*(setopt|unsetopt|bindkey|zstyle|autoload|zmodload|shopt|set\s+-o|"
                         r"typeset\s+-U)\b")),
    (SOURCED, re.compile(r"^\s*(source|\.)\s")),
)

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_ASSIGN = re.compile(rf"^\s*(?P<export>export\s+|typeset\s+-x\s+|declare\s+-x\s+)?(?P<name>{_NAME})=(?P<value>.*?)\s*$")
_PATH_ARRAY = re.compile(r"^\s*(?:typeset\s+-U\s+)?path\s*(?P<op>\+?=)\s*\((?P<items>.*)\)\s*$")
_PATH_APPEND_WORD = re.compile(r"^\s*path\+=(?P<item>[^\s(][^\s]*)\s*$")
_ALIAS = re.compile(r"^\s*alias\s+(?P<rest>.+)$")
_FUNC_START = re.compile(r"^\s*(?:function\s+(?P<a>[\w:.-]+)\s*(?:\(\)\s*)?|(?P<b>[\w:.-]+)\s*\(\)\s*)(?P<brace>\{)?")
_GPG_TTY = re.compile(r"""^\s*export\s+GPG_TTY=("?)(\$\(tty\)|`tty`|\$TTY|\$\{TTY\})\1\s*(#.*)?$""")
_HOME_REF = re.compile(r"\$\{HOME\}|\$HOME\b|^~(?=/|$)")
_VAR_REF = re.compile(r"\$\{(" + _NAME + r")\}|\$(" + _NAME + r")")

# PATH entries that dotkit adds or that only a version manager or a
# framework used. Relative to the home directory when they start with ~/.
_PATH_DONE = (
    "/opt/homebrew/bin", "/opt/homebrew/sbin", "~/.local/bin", "/run/current-system/sw/bin",
    "/nix/var/nix/profiles/default/bin", "~/.nix-profile/bin", "~/.local/share/mise/shims",
    "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin",
)
_PATH_VERSION_MANAGERS = re.compile(
    r"/\.(pyenv|rbenv|nvm|nodenv|goenv|asdf|jenv|volta|sdkman|rvm|fnm|tfenv)(/|$)|/fnm(/|$)|/(ana|mini)conda\d?/"
)


@dataclass
class Carried:
    lines: tuple[int, int]
    text: str  # what goes into the repository file
    kind: str  # export, path, alias, function, comment


@dataclass
class SecretLine:
    lines: tuple[int, int]
    name: str
    text: str  # the original line, for the local file only


@dataclass
class Finding:
    lines: tuple[int, int]
    category: str
    text: str
    note: str = ""
    # The text has a secret: the report does not show it.
    secret: bool = False

    @property
    def where(self) -> str:
        a, b = self.lines
        return str(a) if a == b else f"{a}-{b}"


@dataclass
class Result:
    name: str  # for example ".zshrc"
    carried: list[Carried] = field(default_factory=list)
    secrets: list[SecretLine] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    # Variables the carried lines set, for the next file.
    variables: dict[str, str] = field(default_factory=dict)

    def carried_text(self) -> str:
        return "".join(c.text + "\n" for c in self.carried)

    @property
    def statements(self) -> int:
        return sum(1 for c in self.carried if c.kind != "comment")


@dataclass
class _Statement:
    start: int
    end: int
    text: str  # the lines joined with newlines, continuations kept
    block: bool = False


def _strip_quotes_and_comments(text: str) -> str:
    """The text without quoted strings, escaped characters, and comments, for keyword counting."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c in "'\"`":
            j = text.find(c, i + 1)
            while c != "'" and j > 0 and text[j - 1] == "\\":
                j = text.find(c, j + 1)
            if j < 0:
                break
            out.append(" ")
            i = j + 1
            continue
        if c == "#" and (i == 0 or text[i - 1] in " \t;\n"):
            j = text.find("\n", i)
            if j < 0:
                break
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _words(text: str) -> list[str]:
    plain = _strip_quotes_and_comments(text)
    plain = re.sub(r"\$\(", " ", plain)  # a command substitution is not a subshell block
    plain = re.sub(r"\$\{[^}]*\}", " ", plain)
    return re.findall(r"[{}();\n]|&&|\|\||[^\s;&|<>(){}]+", plain)


def _depth_change(text: str) -> tuple[int, int]:
    """How many blocks a line opens minus how many it closes: keywords and braces, then parentheses."""
    kw = paren = 0
    command_start = True
    for w in _words(text):
        if w == "{":
            kw += 1
        elif w == "}":
            kw -= 1
        elif command_start and w in ("if", "case", "do"):
            kw += 1
        elif command_start and w in ("fi", "esac", "done"):
            kw -= 1
        elif w == "(":
            paren += 1
        elif w == ")":
            paren -= 1
        # Reserved words only open/close blocks in command position, not
        # as arguments such as `echo done` in the body of a function.
        command_start = w in (";", "\n", "&&", "||", "{", "}", "(", ")", "then", "else", "do")
    return kw, paren


def _statements(text: str) -> list[_Statement]:
    """Split a file into statements: one line, a line with continuations, or a block."""
    lines = text.splitlines()
    out: list[_Statement] = []
    i = 0
    while i < len(lines):
        start = i
        cur = lines[i]
        # Continuation lines.
        while cur.endswith("\\") and i + 1 < len(lines):
            i += 1
            cur = cur + "\n" + lines[i]
        # Here documents: up to the delimiter.
        m = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", _strip_quotes_and_comments(cur))
        if m:
            delim = m.group(1)
            while i + 1 < len(lines):
                i += 1
                cur += "\n" + lines[i]
                if lines[i].strip() == delim:
                    break
            out.append(_Statement(start + 1, i + 1, cur, block=True))
            i += 1
            continue
        # A case pattern closes a parenthesis that it did not open, so the
        # parentheses count on their own and do not go below zero.
        kw, paren = _depth_change(cur)
        paren = max(0, paren)
        block = kw > 0 or paren > 0
        while (kw > 0 or paren > 0) and i + 1 < len(lines):
            i += 1
            cur = cur + "\n" + lines[i]
            k, p = _depth_change(lines[i])
            kw, paren = kw + k, max(0, paren + p)
        out.append(_Statement(start + 1, i + 1, cur, block=block))
        i += 1
    return out


def _expand(value: str, home: str, known: dict[str, str]) -> str | None:
    """A value with $HOME, ~, and known variables expanded, or None when it uses anything else."""
    v = _HOME_REF.sub(lambda _: home, value)

    def repl(m: re.Match[str]) -> str:
        name = m.group(1) or m.group(2)
        if name in known:
            return known[name]
        raise KeyError(name)

    try:
        v = _VAR_REF.sub(repl, v)
    except KeyError:
        return None
    if re.search(r"[$`\\]", v):
        return None
    return v


def _unquote(value: str) -> str | None:
    """The value of a shell word with simple quotes, or None when it is not one word."""
    value = value.strip()
    if not value:
        return ""
    if value[0] == "'":
        return value[1:-1] if re.fullmatch(r"'[^']*'", value) else None
    if value[0] == '"':
        if re.fullmatch(r'"[^"`\\]*"', value):
            return value[1:-1]
        return None
    value = re.sub(r"\s+#.*$", "", value)
    if re.fullmatch(r"[^\s'\"`\\;|&()<>]*", value):
        return value
    return None


def _home_form(path: str, home: str) -> str:
    """A path for a carried line: $HOME instead of the home directory, quoted."""
    if path == home:
        return '"$HOME"'
    if path.startswith(home + "/"):
        return '"$HOME' + path[len(home):] + '"'
    return shlex.quote(path)


def _path_entry_done(entry: str, home: str) -> bool:
    for d in _PATH_DONE:
        d = home + d[1:] if d.startswith("~") else d
        if entry.rstrip("/") == d:
            return True
    return entry.startswith("/nix/store/") or entry.startswith("/etc/profiles/per-user/")


class Parser:
    """Parses the shell files of one Mac. `exists` checks a directory."""

    def __init__(self, home: str, shell: str = "zsh", exists: Callable[[str], bool] = os.path.isdir):
        self.home = home
        self.shell = shell
        self.exists = exists

    def parse(self, name: str, text: str, known: dict[str, str] | None = None) -> Result:
        res = Result(name)
        known = dict(known or {})
        pending: list[tuple[int, str]] = []  # comment lines right before a statement

        def carry(st: _Statement, out: str, kind: str) -> None:
            for no, comment in pending:
                res.carried.append(Carried((no, no), comment, "comment"))
            pending.clear()
            res.carried.append(Carried((st.start, st.end), out, kind))

        def report(st: _Statement, category: str, note: str = "") -> None:
            pending.clear()
            res.findings.append(Finding((st.start, st.end), category, st.text.rstrip(), note))

        for st in _statements(text):
            stripped = st.text.strip()
            if not stripped:
                pending.clear()
                continue
            if stripped.startswith("#") and "\n" not in stripped:
                if stripped.startswith("#!"):
                    continue
                pending.append((st.start, st.text.rstrip()))
                continue
            self._statement(st, known, res, carry, report)
        # A secret must not reach the repository, also not in a function
        # body, a comment, or the report.
        # A carried statement with a secret goes to the local file, so that
        # it still works; a comment with a secret is dropped.
        kept = []
        for c in res.carried:
            found = secrets.scan_text(c.text)
            if not found:
                kept.append(c)
            elif c.kind == "comment":
                res.findings.append(Finding(c.lines, COMMANDS, "", "a comment with a secret: not carried", True))
            else:
                m = _ASSIGN.match(c.text) if c.kind == "export" else None
                name = m.group("name") if m else f"the {c.kind} on this line, with {found[0].what}"
                res.secrets.append(SecretLine(c.lines, name, c.text))
        res.carried = kept
        res.secrets.sort(key=lambda x: x.lines)
        for f in res.findings:
            if secrets.scan_text(f.text):
                f.text, f.secret = "", True
        res.findings.sort(key=lambda f: f.lines)
        res.variables = dict(known)
        return res

    def _statement(self, st, known, res, carry, report) -> None:
        text = st.text
        logical = text.replace("\\\n", "")
        if not st.block and "\n" not in logical:
            secret = secrets.line_secret(logical)
            m = _ASSIGN.match(logical)
            if secret and m:
                res.secrets.append(SecretLine((st.start, st.end), m.group("name"), text.rstrip()))
                return
            am = _ALIAS.match(logical)
            if secret and am and self._alias_ok(am.group("rest")):
                res.secrets.append(SecretLine((st.start, st.end), f"the alias on this line, with {secret}",
                                              text.rstrip()))
                return
            # Other lines with a secret: a carried function goes to the local
            # file below, and the report does not show a line that is not carried.

        # PATH lines of installers: rustup, uv, pnpm.
        if self._installer_path(st, text, known, carry, report):
            return

        # Functions, before the rules: their bodies can mention anything.
        fm = _FUNC_START.match(text)
        if fm and (st.block or re.search(r"\{.*\}\s*;?\s*$", text)) and not re.match(r"^\s*(if|for|while|case)\b", text):
            body = text
            for category, pattern in _RULES[:3]:
                if pattern.search(body):
                    report(st, category)
                    return
            if text.count("\n") < 200:
                carry(st, text.rstrip(), "function")
                return
            report(st, BLOCKS)
            return

        for category, pattern in _RULES:
            if pattern.search(text):
                if category == PROMPT and _ALIAS.match(text):
                    break
                if category == SOURCED and self._done_source(text):
                    report(st, DONE)
                    return
                report(st, category)
                return

        if st.block or "\n" in text:
            report(st, BLOCKS)
            return

        if _GPG_TTY.match(text):
            carry(st, "export GPG_TTY=$TTY" if self.shell == "zsh" else "export GPG_TTY=$(tty)", "export")
            return

        if self._path(st, text, known, carry, report):
            return

        am = _ALIAS.match(text)
        if am:
            if self._alias_ok(am.group("rest")):
                carry(st, text.strip(), "alias")
            else:
                report(st, COMMANDS)
            return

        m = _ASSIGN.match(text)
        if m:
            name, value = m.group("name"), m.group("value")
            if name in SKIP_VARS:
                report(st, SKIP_VARS[name])
                return
            raw = _unquote(value)
            if raw is not None:
                quoted = value.strip().startswith(("'", '"'))
                expanded = raw if value.strip().startswith("'") else _expand(raw, self.home, known)
                if expanded is not None:
                    known[name] = expanded
                    carry(st, text.strip() if quoted else re.sub(r"\s+#.*$", "", text.strip()), "export")
                    return
            report(st, EXPORTS)
            return

        if re.match(r"^\s*export\s+" + _NAME + r"\s*$", text):
            report(st, EXPORTS)
            return
        report(st, COMMANDS)

    def _installer_path(self, st, text, known, carry, report) -> bool:
        """The PATH lines that installers add, as plain PATH lines. False for other lines."""
        home = r"[\"']?(?:~|\$HOME|\$\{HOME\})"
        if re.fullmatch(rf"\s*(?:\.|source)\s+{home}/\.local/bin/env[\"']?\s*(#.*)?", text):
            report(st, DONE)  # uv: ~/.local/bin, which dotkit has
            return True
        dirs = []
        if re.fullmatch(rf"\s*(?:\.|source)\s+{home}/\.cargo/env[\"']?\s*(#.*)?", text):
            dirs = ["$HOME/.cargo/bin"]  # rustup
        m = re.fullmatch(
            r'\s*case\s+"?:\$\{?PATH\}?:"?\s+in\s+\*"?:(?P<dir>[^":]+):"?\*\)\s*;;\s*'
            r'\*\)\s*export\s+PATH="?(?P<again>[^":]+):\$\{?PATH\}?"?\s*;;\s*esac\s*', text)
        if m and m.group("dir") == m.group("again"):
            dirs = [m.group("dir")]  # pnpm and others: add the directory once
        if not dirs:
            return False
        line = f'export PATH="{dirs[0]}:$PATH"'
        return self._path(st, line, known, carry, report)

    def _done_source(self, text: str) -> bool:
        return bool(re.search(r"\.zshrc\.local|\.zshenv\.local|\.zprofile\.local|\.bashrc\b", text))

    def _alias_ok(self, rest: str) -> bool:
        try:
            words = shlex.split(rest, comments=True, posix=True)
        except ValueError:
            return False
        if not words:
            return False
        opts = [w for w in words if w.startswith("-")]
        defs = [w for w in words if not w.startswith("-")]
        if any(o not in ("-g", "-s", "--") for o in opts):
            return False
        # Every word is name=value, and the line has no ; & | after the quotes.
        if not defs or not all(re.match(r"^[^=\s]+=", d) for d in defs):
            return False
        tail = _strip_quotes_and_comments(rest)
        if re.search(r"[;&|<>`]|\$\(", tail):
            return False
        # A command in double quotes runs when the alias is defined, at each start.
        return not re.search(r'"[^"]*(\$\(|`)[^"]*"', rest)

    def _path(self, st, text, known, carry, report) -> bool:
        """Handles PATH lines. Returns False when the line is not one."""
        entries: list[tuple[str, bool]] = []  # (raw entry, prepend)
        m = _ASSIGN.match(text)
        if m and m.group("name") == "PATH":
            raw = _unquote(m.group("value"))
            if raw is None:
                report(st, PATHS)
                return True
            parts = raw.split(":")
            refs = [i for i, p in enumerate(parts) if p in ("$PATH", "${PATH}")]
            if len(refs) != 1:
                report(st, PATHS, "the line replaces PATH")
                return True
            entries = [(p, i < refs[0]) for i, p in enumerate(parts) if i != refs[0] and p]
        else:
            am = _PATH_ARRAY.match(text)
            wm = _PATH_APPEND_WORD.match(text)
            if am:
                try:
                    items = shlex.split(am.group("items"), comments=True)
                except ValueError:
                    report(st, PATHS)
                    return True
                if am.group("op") == "+=":
                    entries = [(i, False) for i in items]
                else:
                    refs = [i for i, p in enumerate(items) if p in ("$path", "${path[@]}", "$PATH")]
                    if len(refs) != 1:
                        report(st, PATHS, "the line replaces PATH")
                        return True
                    entries = [(p, i < refs[0]) for i, p in enumerate(items) if i != refs[0]]
            elif wm:
                entries = [(wm.group("item"), False)]
            else:
                return False

        keep: list[tuple[str, bool]] = []
        dropped: list[str] = []
        for raw, prepend in entries:
            path = _expand(raw, self.home, known)
            if path is None:
                dropped.append(f"{raw} uses another variable")
                continue
            if _path_entry_done(path, self.home):
                continue
            if _PATH_VERSION_MANAGERS.search(path):
                dropped.append(f"{raw} is of a version manager")
                continue
            if not self.exists(path):
                dropped.append(f"{raw} does not exist")
                continue
            keep.append((path, prepend))
        if not keep and not dropped:
            report(st, DONE)
            return True
        if dropped:
            report(st, PATHS, "not carried: " + ", ".join(dropped))
        if keep:
            lines = []
            front = [p for p, pre in keep if pre]
            back = [p for p, pre in keep if not pre]
            if self.shell == "zsh":
                if front:
                    lines.append("path=(" + " ".join(_home_form(p, self.home) for p in front) + " $path)")
                if back:
                    lines.append("path+=(" + " ".join(_home_form(p, self.home) for p in back) + ")")
            else:
                def joined(ps):
                    return ":".join(_home_form(p, self.home).strip('"') for p in ps)
                if front:
                    lines.append(f'PATH="{joined(front)}:$PATH"')
                if back:
                    lines.append(f'PATH="$PATH:{joined(back)}"')
            carry(st, "\n".join(lines), "path")
        return True


def hint(category: str, zshrc: str = "config/zsh/zshrc", zshenv: str = "config/zsh/zshenv") -> str:
    for cat, text in CATEGORIES:
        if cat == category:
            return text.format(zshrc=zshrc, zshenv=zshenv)
    return ""
