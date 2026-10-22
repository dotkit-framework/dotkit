"""A read-only inventory of this Mac: packages, tools, shell files, and config.

Nothing here writes a file or installs anything. Commands run with a
timeout; a command that fails gives an empty result.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import socket
import stat
from dataclasses import dataclass, field
from pathlib import Path

from . import base as base_mod
from ._compat import tomllib
from . import secrets
from .proc import BREW_ENV, Runner

# Shell files that the base links, and the layer files of yours that the
# base reads after them, in the order zsh and bash read them.
SHELL_FILES = (
    ".zshenv", ".zshenv.local", ".zprofile", ".zprofile.local", ".zshrc", ".zshrc.local",
    ".bash_profile", ".bash_profile.local",
)
# The layer file of each base shell file, for the files above.
SHELL_MAIN = {
    ".zshenv.local": ".zshenv",
    ".zprofile.local": ".zprofile",
    ".zshrc.local": ".zshrc",
    ".bash_profile.local": ".bash_profile",
}
# Shell files that dotkit does not link. They stay and keep working, or
# stop being read: see plan.py.
OTHER_SHELL_FILES = (".bashrc", ".profile", ".bash_login", ".zlogin", ".zlogout")

# Dotfiles in the home directory that a person may want in the repository.
HOME_DOTFILES = (
    ".bashrc", ".vimrc", ".tmux.conf", ".inputrc", ".ideavimrc", ".curlrc", ".wgetrc", ".psqlrc",
    ".irbrc", ".pryrc", ".gitignore_global", ".gitmessage", ".hushlogin",
)

VERSION_MANAGERS = {
    # name: (directories, the mise tool, where the versions are)
    "nvm": ((".nvm",), "node", "versions/node"),
    "fnm": ((".local/share/fnm", "Library/Application Support/fnm", ".fnm"), "node", "node-versions"),
    "volta": ((".volta",), "node", "tools/image/node"),
    "pyenv": ((".pyenv",), "python", "versions"),
    "rbenv": ((".rbenv",), "ruby", "versions"),
    "asdf": ((".asdf",), "", "installs"),
    "sdkman": ((".sdkman",), "java", "candidates/java"),
    "jenv": ((".jenv",), "java", "versions"),
}

SHELL_FRAMEWORKS = {
    "Oh My Zsh": (".oh-my-zsh",),
    "Prezto": (".zprezto",),
    "zinit": (".zinit", ".local/share/zinit"),
    "Powerlevel10k": (".p10k.zsh", "powerlevel10k", ".powerlevel10k"),
    "antigen": (".antigen", "antigen.zsh"),
    "antidote": (".antidote", ".cache/antidote"),
    "Zim": (".zim",),
    "zplug": (".zplug",),
}

# asdf plugin names that mise names differently.
ASDF_TOOLS = {"nodejs": "node", "golang": "go"}

# The default taps of Homebrew. Other homebrew/* taps are old and merged.
DEFAULT_TAPS = {"homebrew/core", "homebrew/cask"}

# Files and directories in ~/.config that hold state or caches, not config.
_STATE_NAMES = re.compile(
    r"(\.(db|sqlite\d?|sqlite-(wal|shm)|log|pid|sock|lock|ldb)$|^(cache|caches|Cache|logs?|history|"
    r"sessions?|state|tmp|node_modules|\.git|IndexedDB|Local Storage|Session Storage|GPUCache|"
    r"Crashpad|blob_storage|Service Worker|Cookies.*|backups?|automatic_backups)$)"
)
_MAX_FILES = 300
_MAX_BYTES = 2_000_000


@dataclass
class Package:
    name: str
    version: str = ""
    where: str = ""  # where it is installed, for the report
    ok: bool = True  # False: it cannot go to a mise backend; note says why
    note: str = ""


@dataclass
class Tap:
    name: str
    remote: str = ""
    custom: bool = False  # the remote is not github.com/<user>/homebrew-<repo>


@dataclass
class VersionManager:
    name: str
    path: str
    tool: str  # the mise tool, "" for asdf (per plugin)
    versions: dict[str, list[str]] = field(default_factory=dict)  # tool -> versions
    default: dict[str, str] = field(default_factory=dict)  # tool -> the global version


@dataclass
class ShellFile:
    name: str  # relative to the home directory
    path: Path
    text: str
    managed: bool = False  # a dotkit or Home Manager link already
    # For a layer file (~/.zshrc.local): the main file reads it.
    read: bool = True


@dataclass
class ConfigFile:
    target: str  # relative to the home directory
    path: Path
    secrets: list[secrets.Secret] = field(default_factory=list)
    same_as_base: bool = False


@dataclass
class OtherConfig:
    rel: str  # relative to the home directory, for example .config/nvim
    is_dir: bool
    adoptable: bool
    reason: str = ""  # why it cannot be adopted
    files: int = 0


@dataclass
class Scan:
    home: str
    user: str
    hostname: str = ""
    login_shell: str = ""
    git_name: str = ""
    git_email: str = ""
    brew: str = ""  # the brew command, "" without Homebrew
    formulae: list[str] = field(default_factory=list)
    casks: list[str] = field(default_factory=list)
    taps: list[Tap] = field(default_factory=list)
    old_taps: list[str] = field(default_factory=list)  # homebrew/* taps other than core and cask
    mas: bool = False
    mas_apps: list[tuple[int, str]] = field(default_factory=list)
    store_apps: list[str] = field(default_factory=list)  # App Store apps, without mas
    npm: list[Package] = field(default_factory=list)
    pipx: list[Package] = field(default_factory=list)
    cargo: list[Package] = field(default_factory=list)
    go: list[Package] = field(default_factory=list)
    has_cargo: bool = False
    has_go: bool = False
    mise_config: Path | None = None
    mise_extra: list[str] = field(default_factory=list)  # other global mise files
    version_managers: list[VersionManager] = field(default_factory=list)
    frameworks: dict[str, str] = field(default_factory=dict)
    shell_files: list[ShellFile] = field(default_factory=list)
    other_shell_files: list[str] = field(default_factory=list)
    config_files: list[ConfigFile] = field(default_factory=list)
    gitconfig: ConfigFile | None = None
    other_config: list[OtherConfig] = field(default_factory=list)
    secret_files: list[str] = field(default_factory=list)
    ghostty_app_support: bool = False
    # Scan steps that failed, with the error. The report lists them.
    warnings: list[str] = field(default_factory=list)


def is_managed(p: Path) -> bool:
    """A link that Home Manager made: it points into the Nix store."""
    try:
        return p.is_symlink() and os.readlink(p).startswith("/nix/store/")
    except OSError:
        return False


def _read(p: Path, limit: int = 4_000_000) -> str:
    """The text of a regular file, or "": never opens a FIFO or a device."""
    try:
        if not stat.S_ISREG(os.stat(p).st_mode) or os.stat(p).st_size > limit:
            return ""
        return p.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return ""


def _secrets_of(p: Path) -> list[secrets.Secret]:
    """The secrets of a file. A file that cannot be checked counts as one with a secret."""
    try:
        return secrets.scan_file(p)
    except secrets.Unscannable as e:
        return [secrets.Secret(0, f"a file that cannot be checked ({e})")]


def _json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _version_key(v: str) -> list:
    return [int(x) if x.isdigit() else x for x in re.split(r"[.\-_+]", v.lstrip("v"))]


class Scanner:
    def __init__(self, home: Path | None = None, runner: Runner | None = None,
                 applications: Path = Path("/Applications"), base: base_mod.Base | None = None,
                 brew_prefix: Path = Path("/opt/homebrew"), env: dict[str, str] | None = None):
        self.home = Path(home or os.environ.get("HOME") or Path.home())
        self.runner = runner or Runner()
        self.applications = applications
        self.base = base or base_mod.Base()
        self.brew_prefix = brew_prefix
        self.env = os.environ if env is None else env

    def _tilde(self, p: Path) -> str:
        s = str(p)
        return "~" + s[len(str(self.home)):] if s.startswith(str(self.home) + "/") else s

    def h(self, rel: str) -> Path:
        return self.home / rel

    def env_path(self, var: str, default: str) -> Path:
        v = self.env.get(var)
        return Path(v).expanduser() if v else self.h(default)

    # --- Homebrew

    def _brew(self, *args: str, timeout: float = 300) -> list[str]:
        out = self.runner.run([self.s.brew, *args], timeout=timeout, env=BREW_ENV)
        return [line.strip() for line in (out or "").splitlines() if line.strip()]

    def scan_brew(self) -> None:
        s = self.s
        s.brew = self.runner.which("brew", ("/opt/homebrew/bin",)) or ""
        if not s.brew:
            return
        s.formulae = self._brew("leaves", "--installed-on-request")
        s.casks = self._brew("list", "--cask", "--full-name", "-1")
        out = self.runner.run([s.brew, "tap-info", "--json", "--installed"], timeout=300, env=BREW_ENV)
        try:
            taps = json.loads(out or "[]")
        except ValueError:
            taps = [{"name": n} for n in self._brew("tap")]
        for t in taps:
            name = t.get("name", "")
            if not name or name in DEFAULT_TAPS:
                continue
            if name.startswith("homebrew/"):
                s.old_taps.append(name)
                continue
            remote = t.get("remote") or ""
            user, _, repo = name.partition("/")
            usual = re.fullmatch(
                rf"https://github\.com/{re.escape(user)}/homebrew-{re.escape(repo)}(\.git)?/?", remote, re.I
            )
            s.taps.append(Tap(name, remote, custom=bool(remote) and not usual))

    # --- App Store

    def scan_app_store(self) -> None:
        s = self.s
        mas = self.runner.which("mas", ("/opt/homebrew/bin",))
        if mas:
            out = self.runner.run([mas, "list"], timeout=60)
            if out is not None:
                s.mas = True
                for line in out.splitlines():
                    m = re.match(r"^\s*(\d+)\s+(.+?)\s+\(([^)]*)\)\s*$", line)
                    if m:
                        s.mas_apps.append((int(m.group(1)), m.group(2).strip()))
                return
        try:
            apps = sorted(self.applications.glob("*.app/Contents/_MASReceipt/receipt"))
        except OSError:
            apps = []
        s.store_apps = [p.parents[2].name.removesuffix(".app") for p in apps]

    # --- Global tools of other package managers

    def _node_module_roots(self) -> list[tuple[Path, str]]:
        roots: list[tuple[Path, str]] = [
            (self.brew_prefix / "lib/node_modules", "Homebrew node"),
            (self.h(".npm-global/lib/node_modules"), "~/.npm-global"),
            (self.h(".local/lib/node_modules"), "~/.local"),
        ]
        prefix = re.search(r"(?m)^\s*prefix\s*=\s*(.+?)\s*$", _read(self.h(".npmrc")))
        if prefix and not is_managed(self.h(".npmrc")):
            p = prefix.group(1).replace("${HOME}", str(self.home)).replace("$HOME", str(self.home))
            roots.append((Path(p).expanduser() / "lib/node_modules", f"npm prefix {prefix.group(1)}"))
        for pattern, label in (
            (".nvm/versions/node/*/lib/node_modules", "nvm"),
            (".local/share/fnm/node-versions/*/installation/lib/node_modules", "fnm"),
            ("Library/Application Support/fnm/node-versions/*/installation/lib/node_modules", "fnm"),
            (".asdf/installs/nodejs/*/lib/node_modules", "asdf"),
            (".local/share/mise/installs/node/*/lib/node_modules", "mise node"),
        ):
            for p in sorted(self.home.glob(pattern)):
                version = p.parts[-4] if label == "fnm" else p.parts[-3]
                roots.append((p, f"{label} {version}"))
        # Keg-only Homebrew nodes, for example node@20.
        for p in sorted(self.brew_prefix.glob("opt/node@*/lib/node_modules")):
            roots.append((p, f"Homebrew {p.parts[-3]}"))
        # The npm on PATH, without running it: npm writes a log file for each
        # command, and the scan writes nothing. Its global packages are in
        # <prefix>/lib/node_modules, and its prefix is the directory above bin.
        for var in ("npm_config_prefix", "NPM_CONFIG_PREFIX"):
            if self.env.get(var):
                roots.append((Path(self.env[var]).expanduser() / "lib/node_modules", f"npm prefix {self.env[var]}"))
        npm = self.runner.which("npm")
        if npm:
            prefix = Path(npm).parent.parent
            roots.append((prefix / "lib/node_modules", f"npm in {self._tilde(prefix)}"))
        seen: set[str] = set()
        unique = []
        for p, label in roots:
            key = os.path.realpath(p)
            if key not in seen and p.is_dir():
                seen.add(key)
                unique.append((p, label))
        return unique

    def scan_npm(self) -> None:
        found: dict[str, Package] = {}

        def add(name: str, version: str, where: str, ok: bool = True, note: str = "") -> None:
            if name in ("npm", "corepack") or name in found:
                return
            found[name] = Package(name, version, where, ok, note)

        for root, label in self._node_module_roots():
            entries = []
            for p in sorted(root.iterdir()):
                if p.name.startswith("@") and p.is_dir() and not p.is_symlink():
                    entries += sorted(p.iterdir())
                elif not p.name.startswith("."):
                    entries.append(p)
            for p in entries:
                meta = _json(p / "package.json")
                if not isinstance(meta, dict) or not meta.get("bin"):
                    continue
                name = meta.get("name") or p.name
                if p.is_symlink():
                    add(name, "", label, False, "a linked local package (npm link)")
                    continue
                add(name, str(meta.get("version", "")), label)
        pnpm_home = self.env_path("PNPM_HOME", "Library/pnpm")
        for manifest in sorted(pnpm_home.glob("global/*/package.json")):
            deps = (_json(manifest) or {}).get("dependencies") or {}
            for name, version in sorted(deps.items()):
                add(name, str(version).lstrip("^~"), "pnpm")
        bun = self.env_path("BUN_INSTALL", ".bun") / "install/global/package.json"
        for name, version in sorted(((_json(bun) or {}).get("dependencies") or {}).items()):
            add(name, str(version).lstrip("^~"), "bun")
        for p in sorted(self.h(".volta/tools/user/packages").glob("*.json")):
            meta = _json(p) or {}
            if meta.get("name"):
                add(meta["name"], str(meta.get("version", "")), "volta")
        self.s.npm = sorted(found.values(), key=lambda p: p.name)

    def scan_python_tools(self) -> None:
        found: dict[str, Package] = {}
        homes = [self.env_path("PIPX_HOME", ".local/pipx"), self.h("Library/Application Support/pipx")]
        for home in homes:
            for meta_path in sorted(home.glob("venvs/*/pipx_metadata.json")):
                meta = _json(meta_path) or {}
                main = meta.get("main_package") or {}
                name = main.get("package") or meta_path.parent.name
                spec = main.get("package_or_url") or name
                ok, note = True, ""
                if re.search(r"[/:@]", spec) and not re.fullmatch(r"[A-Za-z0-9._\-\[\],]+", spec):
                    ok, note = False, f"installed from {spec}"
                injected = sorted((meta.get("injected_packages") or {}).keys())
                if injected:
                    note = "injected packages: " + ", ".join(injected)
                found.setdefault(name, Package(name, main.get("package_version", ""), "pipx", ok, note))
        tools = self.env_path("UV_TOOL_DIR", ".local/share/uv/tools")
        for receipt in sorted(tools.glob("*/uv-receipt.toml")):
            try:
                data = tomllib.loads(receipt.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            reqs = (data.get("tool") or {}).get("requirements") or []
            if not reqs:
                continue
            first = reqs[0] if isinstance(reqs[0], dict) else {"name": str(reqs[0])}
            name = first.get("name") or receipt.parent.name
            ok, note = True, ""
            if any(k in first for k in ("git", "url", "path", "directory", "editable")):
                ok, note = False, "installed from a URL or a directory"
            if len(reqs) > 1:
                note = "with " + ", ".join(r.get("name", "?") for r in reqs[1:] if isinstance(r, dict))
            found.setdefault(name, Package(name, "", "uv tool", ok, note))
        self.s.pipx = sorted(found.values(), key=lambda p: p.name)

    def scan_cargo(self) -> None:
        cargo_home = self.env_path("CARGO_HOME", ".cargo")
        self.s.has_cargo = (cargo_home / "bin/cargo").exists() or bool(self.runner.which("cargo"))
        installs: dict[str, object] = {}
        data = _json(cargo_home / ".crates2.json")
        if isinstance(data, dict):
            installs = data.get("installs") or {}
        else:
            try:
                installs = tomllib.loads((cargo_home / ".crates.toml").read_text(encoding="utf-8")).get("v1", {})
            except (OSError, ValueError):
                installs = {}
        out = []
        for key in sorted(installs):
            m = re.match(r"^(\S+) (\S+) \((.+)\)$", key)
            if not m:
                continue
            name, version, source = m.groups()
            ok = source.startswith(("registry+", "sparse+"))
            out.append(Package(name, version, "cargo install", ok, "" if ok else f"installed from {source}"))
        self.s.cargo = out

    def scan_go(self) -> None:
        go = self.runner.which("go", ("/opt/homebrew/bin", "/usr/local/go/bin"))
        self.s.has_go = bool(go)
        gobin = self.env.get("GOBIN")
        d = Path(gobin) if gobin else self.env_path("GOPATH", "go") / "bin"
        if not d.is_dir():
            return
        out = []
        for p in sorted(d.iterdir()):
            if not p.is_file() or not os.access(p, os.X_OK):
                continue
            module, version = self._go_module(p, go)
            if module:
                out.append(Package(module, version, str(p), True, f"the command {p.name}"))
            else:
                out.append(Package(p.name, "", str(p), False, "no Go module information"))
        self.s.go = out

    def _go_module(self, p: Path, go: str | None) -> tuple[str, str]:
        text = self.runner.run([go, "version", "-m", str(p)], timeout=30) if go else None
        if text:
            m = re.search(r"(?m)^\s*path\s+(\S+)", text)
            v = re.search(r"(?m)^\s*mod\s+\S+\s+(\S+)", text)
            if m:
                return m.group(1), (v.group(1) if v else "")
        # Without go: Go writes the same lines into the binary.
        try:
            with open(p, "rb") as f:
                data = f.read(64_000_000)
        except OSError:
            return "", ""
        m = re.search(rb"\npath\t([^\t\n]+)\nmod\t[^\t\n]+\t([^\t\n]+)", b"\n" + data)
        if m:
            return m.group(1).decode(errors="replace"), m.group(2).decode(errors="replace")
        return "", ""

    # --- mise and version managers

    def scan_mise(self) -> None:
        p = self.h(".config/mise/config.toml")
        if p.is_file() and not is_managed(p):
            self.s.mise_config = p
        for rel in (".tool-versions", ".config/mise/config.local.toml", ".mise.toml"):
            q = self.h(rel)
            if q.is_file() and not is_managed(q):
                self.s.mise_extra.append(rel)
        for q in sorted(self.h(".config/mise/conf.d").glob("*.toml")):
            if q.name != "dotkit.toml" and not is_managed(q):
                self.s.mise_extra.append(str(q.relative_to(self.home)))

    def scan_version_managers(self) -> None:
        for name, (dirs, tool, sub) in VERSION_MANAGERS.items():
            root = next((self.h(d) for d in dirs if self.h(d).is_dir()), None)
            if root is None:
                continue
            vm = VersionManager(name, "~/" + str(root.relative_to(self.home)), tool)
            if name == "asdf":
                for plugin in sorted((root / sub).glob("*")):
                    t = ASDF_TOOLS.get(plugin.name, plugin.name)
                    vm.versions[t] = sorted((v.name for v in plugin.iterdir() if v.is_dir()), key=_version_key)
                for line in _read(self.h(".tool-versions")).splitlines():
                    parts = line.split("#")[0].split()
                    if len(parts) >= 2:
                        vm.default[ASDF_TOOLS.get(parts[0], parts[0])] = parts[1]
            else:
                versions = [v.name for v in (root / sub).glob("*") if v.is_dir() and v.name != "current"]
                if versions:
                    vm.versions[tool] = sorted(versions, key=_version_key)
                default = ""
                if name == "nvm":
                    default = _read(root / "alias/default").strip()
                elif name in ("pyenv", "rbenv", "jenv"):
                    default = _read(root / "version").split("\n")[0].strip()
                elif name == "sdkman":
                    cur = root / sub / "current"
                    default = os.readlink(cur) if cur.is_symlink() else ""
                    default = default.rstrip("/").rsplit("/", 1)[-1]
                elif name == "fnm":
                    alias = root / "aliases/default"
                    default = os.readlink(alias).rstrip("/").split("/")[-2] if alias.is_symlink() else ""
                elif name == "volta":
                    meta = _json(root / "tools/user/platform.json") or {}
                    default = ((meta.get("node") or {}).get("runtime")) or ""
                if default and default != "system":
                    vm.default[tool] = default
            self.s.version_managers.append(vm)

    def scan_frameworks(self) -> None:
        for name, rels in SHELL_FRAMEWORKS.items():
            for rel in rels:
                p = self.h(rel)
                if p.exists() and not is_managed(p):
                    self.s.frameworks[name] = "~/" + rel
                    break

    # --- Files in the home directory

    def scan_shell_files(self) -> None:
        texts: dict[str, str] = {}
        for name in SHELL_FILES:
            p = self.h(name)
            if not (p.is_file() or p.is_symlink()):
                continue
            managed = is_managed(p)
            text = "" if managed else _read(p)
            texts[name] = text
            self.s.shell_files.append(ShellFile(name, p, text, managed))
        for f in self.s.shell_files:
            main = SHELL_MAIN.get(f.name)
            if main and not f.managed:
                # Read before only when the main file sourced it.
                f.read = f.name in texts.get(main, "")
        for name in OTHER_SHELL_FILES:
            p = self.h(name)
            if p.is_file() and not is_managed(p):
                self.s.other_shell_files.append(name)

    def scan_config_files(self) -> None:
        for link in base_mod.LINKS:
            p = self.h(link.target)
            if not p.is_file() or is_managed(p):
                continue
            base_file = self.base.file(link.source)
            same = _read(p) == _read(base_file) if base_file.is_file() else False
            self.s.config_files.append(ConfigFile(link.target, p, _secrets_of(p), same))
        p = self.h(".gitconfig")
        if p.is_file() and not is_managed(p):
            self.s.gitconfig = ConfigFile(".gitconfig", p, _secrets_of(p))
        self.s.ghostty_app_support = self.h("Library/Application Support/com.mitchellh.ghostty/config").is_file()

    def _assess(self, rel: str) -> OtherConfig:
        p = self.h(rel)
        is_dir = p.is_dir()
        if secrets.known_secret_file(rel):
            return OtherConfig(rel, is_dir, False, "holds login tokens")
        if is_managed(p):
            return OtherConfig(rel, is_dir, False, "a Home Manager link")
        if not is_dir:
            try:
                size = p.stat().st_size
            except OSError:
                return OtherConfig(rel, False, False, "cannot be read")
            if size > 256_000:
                return OtherConfig(rel, False, False, "large")
            if _STATE_NAMES.search(p.name):
                return OtherConfig(rel, False, False, "state, not config")
            try:
                if secrets.scan_file(p):
                    return OtherConfig(rel, False, False, "has a secret")
            except secrets.Unscannable as e:
                return OtherConfig(rel, False, False, f"cannot be checked for secrets: {e}")
            return OtherConfig(rel, False, True, files=1)
        if any(f.startswith(rel + "/") for f in secrets.SECRET_FILES):
            return OtherConfig(rel, True, False, "holds login tokens")
        count = size = 0
        for root, dirs, files in os.walk(p, followlinks=False):
            dirs[:] = [d for d in dirs if d != ".DS_Store"]
            for d in dirs:
                if _STATE_NAMES.search(d):
                    return OtherConfig(rel, True, False, "holds state or caches")
            for f in files:
                if f == ".DS_Store":
                    continue
                count += 1
                fp = Path(root, f)
                if _STATE_NAMES.search(f):
                    return OtherConfig(rel, True, False, "holds state or caches")
                try:
                    st = fp.lstat()
                except OSError:
                    return OtherConfig(rel, True, False, "has a file that cannot be read")
                # A link is copied as a link, so its target does not go into the repository.
                if stat.S_ISLNK(st.st_mode):
                    continue
                if not stat.S_ISREG(st.st_mode):
                    return OtherConfig(rel, True, False, "has a socket, a FIFO, or a device")
                size += st.st_size
                if count > _MAX_FILES or size > _MAX_BYTES:
                    return OtherConfig(rel, True, False, "large")
                try:
                    if secrets.scan_file(fp):
                        return OtherConfig(rel, True, False, "has a secret")
                except secrets.Unscannable as e:
                    return OtherConfig(rel, True, False, f"has a file that cannot be checked for secrets ({e})")
        if count == 0:
            return OtherConfig(rel, True, False, "empty")
        return OtherConfig(rel, True, True, files=count)

    def scan_other_config(self) -> None:
        config = self.h(".config")
        if config.is_dir():
            for p in sorted(config.iterdir(), key=lambda q: q.name.lower()):
                if p.name in base_mod.BASE_CONFIG_DIRS or p.name.startswith("."):
                    continue
                self.s.other_config.append(self._assess(".config/" + p.name))
        for name in HOME_DOTFILES:
            if self.h(name).is_file():
                self.s.other_config.append(self._assess(name))
        for rel in secrets.SECRET_FILES:
            if self.h(rel).exists():
                self.s.secret_files.append(rel)

    # --- Identity

    def scan_identity(self) -> None:
        s = self.s
        git = self.runner.which("git", ("/usr/bin",)) or "/usr/bin/git"
        s.git_name = (self.runner.run([git, "config", "--global", "--includes", "--get", "user.name"], timeout=30) or "").strip()
        s.git_email = (self.runner.run([git, "config", "--global", "--includes", "--get", "user.email"], timeout=30) or "").strip()
        out = self.runner.run(["/usr/bin/dscl", ".", "-read", f"/Users/{s.user}", "UserShell"], timeout=30) or ""
        m = re.search(r"UserShell:\s*(\S+)", out)
        s.login_shell = m.group(1) if m else self.env.get("SHELL", "")

    def run(self) -> Scan:
        try:
            user = getpass.getuser()
        except (KeyError, OSError):
            user = os.environ.get("USER", "")
        self.s = Scan(home=str(self.home), user=user, hostname=socket.gethostname().split(".")[0])
        for step in (
            self.scan_identity, self.scan_brew, self.scan_app_store, self.scan_npm, self.scan_python_tools,
            self.scan_cargo, self.scan_go, self.scan_mise, self.scan_version_managers, self.scan_frameworks,
            self.scan_shell_files, self.scan_config_files, self.scan_other_config,
        ):
            try:
                step()
            except Exception as e:  # noqa: BLE001 - one failed step must not stop the others
                self.s.warnings.append(f"{step.__name__.removeprefix('scan_')}: {type(e).__name__}: {e}")
        return self.s
