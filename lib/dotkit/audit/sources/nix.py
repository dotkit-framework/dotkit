"""The Nix packages of the configuration: one `nix eval` gives the source of each package. It builds
nothing.

OSV has no Nix ecosystem. A package from a Git repository is a query of the GIT ecosystem: the repository
and the tag of the release, or the commit. A CVE in the name of a Nixpkgs patch of the package is fixed.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

from ...core import proc
from ...core.nix import Nix
from ..match import HEX40
from ..model import Item, Part, git_query

NAME, LABEL, NOUNS = "nix", "Nix", ("Nix package", "Nix packages")

# The packages of Home Manager and of the system. A wrapper (neovim) has the source in its unwrapped
# package. The expression is part of the command line, which the scenario tests compare.
NIX_EXPR = '''
    sys:
    let
      lib = sys.pkgs.lib;
      users = lib.attrValues sys.config.home-manager.users;
      drvs = lib.concatMap (u: u.home.packages) users ++ sys.config.environment.systemPackages;
      str = x: if builtins.isString x then x else "";
      one =
        d:
        let
          src = d.src or (d.unwrapped.src or { });
          urls = src.urls or [ ];
          v = {
            name = str (d.pname or (d.name or ""));
            version = str (d.version or "");
            url = str (src.url or (if builtins.isList urls && urls != [ ] then builtins.head urls else ""));
            git = str (src.gitRepoUrl or "");
            tag = str (src.tag or "");
            rev = str (src.rev or "");
            homepage = str (d.meta.homepage or "");
            patches = map (p: str (p.name or (baseNameOf (toString p)))) (d.patches or [ ]);
          };
          r = builtins.tryEval (builtins.deepSeq v v);
        in
        if r.success then v else null;
    in
    builtins.filter (x: x != null) (map one drvs)'''

# The repository of a source URL: https://github.com/OWNER/REPO/archive/..., .../releases/download/TAG/...,
# https://github.com/OWNER/REPO.git
_HOSTED = re.compile(r"https://(github\.com|gitlab\.com|codeberg\.org)/([^/]+)/([^/?#]+?)(?:\.git)?(?:[/?#]|$)")
# GitLab archives by the API: https://gitlab.com/api/v4/projects/OWNER%2FREPO/repository/archive.tar.gz?sha=REF
_GITLAB_API = re.compile(r"https://gitlab\.com/api/v4/projects/([^/]+)%2[Ff]([^/]+)/repository/archive[^?]*\?sha=([^&]+)")
# The ref in an archive URL: .../archive/refs/tags/REF.tar.gz or .../archive/REF.tar.gz
_ARCHIVE = re.compile(r"/archive/(?:refs/tags/)?(.+?)\.(?:tar\.gz|tar\.bz2|tar\.xz|tgz|zip)$")
_RELEASE = re.compile(r"/releases/download/([^/]+)/")

# Packages whose source is not on a Git host that OSV knows, by their Nixpkgs pname: the mirror that OSV
# has, and the tag of a version. Each one returns advisories for an old version.
KNOWN_REPOS: Dict[str, Tuple[str, str]] = {
    "git": ("https://github.com/git/git", "v{v}"),
    "nix": ("https://github.com/nixos/nix", "{v}"),
    "zsh": ("https://github.com/zsh-users/zsh", "zsh-{v}"),
    "gnupg": ("https://github.com/gpg/gnupg", "gnupg-{v}"),
    "openssl": ("https://github.com/openssl/openssl", "openssl-{v}"),
    "curl": ("https://github.com/curl/curl", "curl-{v_}"),
    "python3": ("https://github.com/python/cpython", "v{v}"),
    "nodejs": ("https://github.com/nodejs/node", "v{v}"),
    "neovim": ("https://github.com/neovim/neovim", "v{v}"),
}


def tag(template: str, version: str) -> str:
    return template.format(v=version, v_=version.replace(".", "_"))


def item(pkg: dict) -> Optional[Item]:
    """The item of a package of NIX_EXPR. None for a package without a version, such as a wrapper script
    of the configuration."""
    name, version = pkg.get("name") or "", pkg.get("version") or ""
    if not name or not version:
        return None
    patched = frozenset(m.upper() for p in pkg.get("patches") or [] for m in re.findall(r"CVE-\d{4}-\d+", p, re.I))
    out = Item(NAME, name, version, patched=patched)
    if name in KNOWN_REPOS:
        repo, template = KNOWN_REPOS[name]
        out.queries = [git_query(repo, tag(template, version))]
        return out
    url = pkg.get("url") or pkg.get("git") or ""
    ref = pkg.get("tag") or pkg.get("rev") or ""
    if ref.startswith("refs/tags/"):
        ref = ref[len("refs/tags/"):]
    m = _GITLAB_API.match(url)
    if m:
        repo, ref = f"https://gitlab.com/{m[1]}/{m[2]}", ref or m[3]
    else:
        m = _HOSTED.match(url) or _HOSTED.match(pkg.get("homepage") or "")
        if not m:
            out.why = "its source is not in a Git repository that OSV knows"
            return out
        repo = f"https://{m[1]}/{m[2]}/{m[3]}"
        if not ref:
            r = _RELEASE.search(url) or _ARCHIVE.search(url)
            ref = r[1] if r else ""
    if HEX40.fullmatch(ref):
        out.queries = [{"commit": ref}]
    elif ref:
        out.queries = [git_query(repo, ref)]
    else:
        # Only the homepage: the usual tags of a version.
        out.queries = [git_query(repo, f"v{version}"), git_query(repo, version)]
    return out


def items(packages: List[dict]) -> List[Item]:
    """The items of the packages of NIX_EXPR, each name and version once."""
    out, seen = [], set()
    for p in packages:
        i = item(p) if isinstance(p, dict) else None
        if i is not None and (i.name, i.version) not in seen:
            seen.add((i.name, i.version))
            out.append(i)
    return out


def collect(ctx) -> Part:
    env = ctx.env
    r = Nix(env).flake(["eval", "--json", f"{env.flake}#darwinConfigurations.mac", "--apply", NIX_EXPR],
                       stdout=proc.CAPTURE)
    try:
        packages = json.loads(r.out) if r.ok else None
    except ValueError:
        packages = None
    if not isinstance(packages, list):
        return Part(error="Nix: the configuration did not evaluate, so the Nix packages are not checked.")
    return Part(items(packages))
