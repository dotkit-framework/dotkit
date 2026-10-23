# Architecture of the migration wizard

This is the reference for the migration wizard in `migrate/`: the target
design, the contracts between its parts, and the steps that get the current
code there. Line references (`plan.py:768`) point to the code at commit
`d91296a`. They show where today's logic lives, so it can be moved and not
rewritten.

## 1. Purpose and constraints

The wizard scans a Mac that is already in use and writes a new dotfiles
repository from `templates/default`: package lists, mise tools, links,
carried shell lines, and `MIGRATION.md` (what was carried over and what needs
a person). `dotkit migrate` (`dotkit-migrate adopt`) uses the same scan to
add hand-installed packages to an existing repository.

More migrations are coming: more tools and their config files, version
managers, zsh frameworks, App Store apps, other package sources. Each one
must be a new module plus data, not edits spread over the code.

Hard constraints:

- **Python and dependencies:** only the standard library, on the macOS Python
  of the Command Line Tools (3.9). It runs as `/usr/bin/python3 -I -B`
  (`dotkit-migrate`, `run.py`). No `match`, no `dataclass(slots=…, kw_only=…)`,
  no `zip(strict=…)`, no `X | Y` outside annotations (use `Optional`/`Union`
  at runtime), no `ExceptionGroup`. `tomllib` comes from `_compat` (vendored
  tomli on 3.9/3.10). `tests/test_start.py` checks the grammar.
- **Writes:** the wizard reads the Mac and writes only the new repository
  (setup) or files inside the existing repository (adopt). It never installs,
  uninstalls, or deletes anything on the Mac. Removals are report text only.
- **Secrets:** a detected secret never goes into a file that Git tracks, and
  the report never shows one.
- **Atomic writes:** setup creates the repository completely or not at all.
  Adopt changes all its files or none.
- **Dry run:** `--dry-run` shows exactly the plan that a real run would write.
- **Partial failure:** a bug in one migrator does not stop the migration
  (section 7).
- **No compatibility with earlier dotkit versions:** the new wizard,
  `dotkit migrate`, the template, and the nix modules work only with
  repositories in the layout described here. Nothing reads or converts older
  formats, for example `homebrew.taps` and `homebrew.masApps` entries that an
  earlier wizard wrote into `nix/darwin.nix`.

## 2. Current state, and what moves where

The flow today: `Scanner.run()` → `Scan` (one dataclass for every source) →
`Planner` (`groups`, `file_items`, `adoptable`, `defaults`) → the curses
wizard or the plain prompts edit `Choices` → `Planner.build()` → `_Build`
(about 700 lines that map every source and render every file) → `Plan` →
`write.write()`. Adopt is separate: `adopt.missing/describe/apply`.

These parts are good and stay: `proc.Runner` and `FakeRunner`; `base.py` and
its tables, which tests check against `nix/home.nix`; `rcparse.py` and
`secrets.py` (pure functions); the atomic write in `write.py:25-65`; the
final secret guards (`plan.py:854-898`); the rule that screens only change
choices.

| Today | Target |
|---|---|
| `scan.py` `Scan` fields and `scan_*` steps (`scan.py:137-171`, `643-651`) | one `scan()` per migrator in `migrators/*.py`, each returning its own facts dataclass |
| `VERSION_MANAGERS`, `SHELL_FRAMEWORKS`, `ASDF_TOOLS` (`scan.py:46-71`); default version readers (`scan.py:493-506`); the nvm/fnm/asdf globs of the npm scan (`scan.py:297-303`); `SKIP_VARS`, `_RULES`, `_PATH_VERSION_MANAGERS` (`rcparse.py:61-121`); the sdkman/jenv rule (`plan.py:127`, `476`); pnpm (`plan.py:121`, `418`, `adopt.py:68`); cargo/go "built from source" (`plan.py:175-178`, `443-446`, `adopt.py:70-71`) | `catalog.py` (data) |
| `Planner.groups/file_items/adoptable/defaults` (`plan.py:162-236`) | `pages()` of each migrator; `Session` (`registry.py`) |
| `Group`, `FileItem`, `Choices` (`plan.py:28-66`) | `Page`, `Item`, `Choices` (`model.py`) |
| `_Build.homebrew/app_store/tools/version_managers/shell/config_files/other_config/secret_files/misc` (`plan.py:339-764`) | `plan()` of `homebrew`, `appstore`, `node_tools`, `python_tools`, `built_tools`, `runtimes`, `shell`, `frameworks`, `config_files`, `other_config`, `logins` |
| shared state `self.brews`, `self.casks`, `self.mise_lines`, `self.mise_text` (`plan.py:314-316`, `717`) and `render()` (`plan.py:768-848`); `insert_tools`, `nix_string`, `nix_name`, `toml_key`, `_merge_mise` (`plan.py:274-299`, `1014-1017`) | typed documents (`docs.py`) |
| the `homebrew.taps` and `homebrew.masApps` anchors that `render()` fills in `nix/darwin.nix` (`plan.py:792-809`), and adopt's "add them by hand" lines (`adopt.py:88-93`) | `nix/taps.txt` and `nix/mas.txt`, read by the nix module, written through `ListDoc` |
| `guard_copies`, `guard_files`, report masking (`plan.py:844-898`) | `writer.py` |
| `link_targets`, `moved_aside`, `links_in_the_way`, `summary` (`plan.py:900-1002`) | `checks.py` |
| `write.write` (`write.py:25-65`) | `writer.write_new` |
| `adopt.missing/describe/apply` (`adopt.py:31-110`) | `migrator.adopt()` + `Declared` + `writer.apply_edits` |
| `tilde`, `expand`, `repo_error` (`plan.py:69-96`); `_read`, `_json`, `is_managed`, `_version_key` (`scan.py:174-208`) | `context.py` |
| `wizard.py` screens (`wizard.py:137-374`) and `prompts.py:28-86` | `ui/screens.py`, `ui/curses_ui.py`, `ui/plain.py` |
| `report.ORDER` (`report.py:10-29`) | `report.SECTIONS`, checked by a test |
| monkeypatch of `cli.scan.Scanner` (`tests/main_child.py:24-28`) | `main(argv, make_context=None)` |

## 3. Layers and package layout

```
migrate/
  dotkit-migrate, run.py           start script, unchanged
  ARCHITECTURE.md                  this file
  dotkit_migrate/
    __main__.py                    CLI only: arguments, mode, exit codes, stderr lines
    context.py                     Context: home, env, runner, base, applications, brew_prefix;
                                   read-only file helpers; tilde/expand/repo_error
    catalog.py                     DATA: version managers, frameworks, tool rules
    model.py                       Item, Page, Choices, Facts, Identity, Failure, Plan
    docs.py                        Docs, Txn, and the document types
    declared.py                    Declared: what the base, the repository, and your mise config provide
    registry.py                    MIGRATORS (ordered tuple) and Session
    writer.py                      render, secret guard, write_new, apply_edits
    checks.py                      links in the way, files moved aside, blockers, summary
    report.py                      ReportDoc rendering, SECTIONS
    migrators/
      __init__.py                  the Migrator base class
      homebrew.py  appstore.py  node_tools.py  python_tools.py  built_tools.py
      runtimes.py  shell.py  frameworks.py  config_files.py  other_config.py
      logins.py  leftovers.py
    ui/
      screens.py                   Screen specs from the Session's pages; no curses
      curses_ui.py                 widgets and the screen loop
      plain.py                     the same screens as line prompts
    rcparse.py  secrets.py  proc.py  base.py  _compat.py  _vendor/
  tests/                           unit, migrator, document, golden, invariant, UI and PTY tests
```

Rules:

- `migrators/` import only `context`, `catalog`, `model`, `docs` (through
  the `Txn` they get), `declared`, `rcparse`, `secrets`, `base`, `_compat`
  (for `tomllib`), `report` (only the text helper `plural`), and their own
  helpers. They never import `ui/`, `writer`, `proc`, `subprocess`, or
  `curses`, and never write a file (section 6). Constants that a scan
  passes to the runner, such as `BREW_ENV`, are data in `catalog.py`.
- `ui/` imports only `model`, `registry` (the Session), and the path helpers
  from `context`.
- Only `writer.py` writes to disk.
- `MIGRATORS` is the single source of order: scan order, page order, plan
  order, and the order of the "Carried over" rows.

```python
MIGRATORS = (
    homebrew, appstore, node_tools, python_tools, built_tools, runtimes,
    shell, frameworks, config_files, other_config, logins, leftovers,
)
```

Core, not migrators: the repository path, the zsh keymap (written by the
Session to `ShellNixDoc`), `nix/user.nix`, the identity scan (section 4.2)
and the "Login shell" to-do, which reads only the identity, the scan
warnings, `checks.py`, and the writer.

These modules of today do not exist in the target, and nothing keeps their
names alive: `plan.py`, `scan.py`, `adopt.py`, `write.py`, `wizard.py`,
`prompts.py`. Their code moves to the modules above, and their tests move
with it (section 13).

## 4. Contracts

### 4.1 Migrator

```python
class Migrator:
    key: str = ""                        # stable id: facts key, Txn owner, test name, Failure.migrator
    title: str = ""                      # human name for problems and warnings
    needs: Tuple[str, ...] = ()          # facts it cannot work without (section 7.3)
    sections: Tuple[str, ...] = ()       # report sections it writes; each must be in report.SECTIONS

    def scan(self, ctx: Context) -> object:
        """Read-only. Return this migrator's facts dataclass. Handle expected
        problems (a command fails, OSError, a file does not parse) by keeping
        what can be read and adding to facts.warnings. Never write."""

    def pages(self, facts: Facts) -> List[Page]:
        """The questions. Depends only on facts. Item defaults are the --yes behavior."""
        return []

    def plan(self, facts: Facts, choices: Choices, txn: Txn) -> None:
        """Pure: add to the documents and the report through txn. May read the
        template and the base (facts.base), and files that the scan found.
        Runs no command; the one exception is facts.check_syntax."""

    def adopt(self, facts: Facts, declared: Declared, txn: Txn, opts: AdoptOptions) -> None:
        """`dotkit migrate`: add what the repository lacks. Default: nothing."""

    def manual_steps(self, ctx: Context) -> str:
        """What a person does by hand when this migrator failed. Must describe
        what actually happens without it (plain-template behavior)."""
```

A migrator's `scan` sees only the `Context`. Other migrators' facts are read
in `pages`, `plan`, and `adopt`, when every scan has finished. So scan order
never matters. `pages`, `plan`, and `adopt` never get the `Context`: it has
the runner and the environment, which belong to the scan. What they need
from outside comes through `Facts` (section 4.2).

Pure helpers of one migrator that others call take facts and choices, for
example `config_files.layered_mise(facts, choices)` and
`config_files.merged_sources(facts, choices)` (the files merged into one
repository file). They must handle missing facts (`None`).

### 4.2 Facts and Identity

```python
@dataclass
class Identity:                          # core scan, today's scan_identity (scan.py:628-635)
    home: str; user: str; hostname: str = ""; login_shell: str = ""
    git_name: str = ""; git_email: str = ""

class Facts:
    identity: Identity
    warnings: List[str]                  # expected problems, from every migrator
    base: Base                           # set by the Session: the dotkit base (read-only data)
    check_syntax: Optional[Callable[[str, str], bool]]   # set by the Session; None: no check
    def get(self, key: str) -> Optional[object]   # None if that scan failed or was skipped
```

`base` and `check_syntax` are the only things from outside the facts that
`pages`, `plan`, and `adopt` use. `check_syntax(shell, text)` runs `zsh -n` or
`bash -n` in production and is a fake in the tests.

Each migrator module has a typed accessor, for example
`def facts_of(f: Facts) -> Optional[BrewFacts]`. A reader must handle `None`.

The identity scan is isolated like a migrator. If it fails, the defaults are
`$USER` and an empty hostname. An empty user name is a blocker, because
`nix/user.nix` needs it.

### 4.3 Pages, items, choices

One page per group. Each page is its own screen, with items selected one by
one.

```python
@dataclass
class Item:
    id: str                              # stable and prefixed, see the table below
    label: str
    detail: str = ""                     # shown for the highlighted row
    default: bool = True                 # checklist pages
    options: Tuple[str, ...] = ()        # choices pages, e.g. ("layer", "replace", "dotkit", "keep")
    default_option: str = ""
    option_text: Dict[str, str] = field(default_factory=dict)   # today's OPTION_TEXT (plan.py:51-57)

@dataclass
class Page:
    key: str
    title: str                           # names the source: "Homebrew formulae"
    dest: str                            # where selected items go: "nix/brews.txt"
    intro: str
    kind: str                            # "checklist" | "choices"
    items: List[Item]
    note: str = ""                       # e.g. "off by default: mise builds them from source"
```

| Migrator | Page key | Kind | Item ids | Default |
|---|---|---|---|---|
| homebrew | `formulae` | checklist | `brew:<name>` | on |
| homebrew | `casks` | checklist | `cask:<name>` | on |
| homebrew | `taps` | checklist | `tap:<owner/repo>` | on |
| appstore | `mas` | checklist | `mas:<id>` | on |
| node_tools | `npm` | checklist | `npm:<package>` | on |
| python_tools | `pipx` | checklist | `pipx:<package>` | on |
| built_tools | `cargo`, `go` | checklist | `cargo:<crate>`, `go:<module>` | off |
| runtimes | `runtimes` | checklist | `runtime:<mise tool>` | off |
| shell | `shell` | checklist | `shell:<file>`, one item per shell file, e.g. `shell:.zshrc` | on |
| config_files | `files` | choices | `file:<target>` | per item (today's `file_items`, `plan.py:189-226`) |
| other_config | `other` | checklist | `other:<rel>` | off |

The shell page has one item per file, not one per statement. A carried
statement can depend on an earlier one (variables that rcparse tracks,
`rcparse.py:335-383`), so leaving out a single statement could break later
ones.

Items that cannot be carried (redundant Homebrew copies, `ok=False`
packages, pnpm) are not items. They go to the report, as today. A file with a
secret gets a restricted option list (`keep`, `dotkit`; `.gitconfig` with a
secret: `keep` only).

```python
@dataclass
class Choices:                           # plain data; JSON for tests and wizard_child.py
    repo: str
    keymap: str = "emacs"
    picked: Dict[str, Set[str]] = field(default_factory=dict)   # page key -> selected item ids
    options: Dict[str, str] = field(default_factory=dict)       # item id -> option (choices pages)
    def selected(self, page: str, item_id: str) -> bool: ...
    def option(self, item_id: str) -> str: ...
    @classmethod
    def defaults(cls, repo: str, pages: List[Page]) -> "Choices": ...
```

An item that is on by default and that you deselect goes to "Not carried,
by your choice". It stays installed or in place, and dotkit does not manage
it. Each item there gives its next step, as the to-do of that kind did
before: `dotkit add brew:<name>`, `dotkit add cask:<name>`, `dotkit add
mas:<id>`, the line for nix/taps.txt, `mise use -g <spec>`, or, for a shell
file, how many lines could be carried (and the names of lines with secrets,
never their values). For Homebrew formulae and casks, the intro adds that
with `homebrew.onActivation.cleanup = "uninstall"` activation would
uninstall them. An item that is off by default (cargo, Go, runtimes, other
config) and that stays off keeps the to-do of its kind, which already says
why it is off. These to-dos replace the old texts for a whole group that
was off ("They stay tapped…", "Add the ones you want…", "You chose not to
carry shell lines…", and the section "Homebrew packages that are not in the
lists").

A shell file that has only lines with secrets is an item too, on by
default: its lines go to `local/` (section 6).

The variables that rcparse tracks pass from one shell file to the next
(`.zshenv` → `.zprofile` → `.zshrc`, each with its `.local` file). A file
that is not carried passes on nothing: the next file starts with the
variables known before the skipped file. An export or PATH line that uses
a variable of a skipped file is then reported, not carried. Aliases and
functions are carried as written, as before; rcparse does not check the
variables in them (a known limit: an alias in double quotes expands such a
variable when the shell defines it). The page labels count from the full
chain; the plan parses again with the chain of the selected files (rcparse
is pure and cheap). The "Not carried, by your choice" item of a deselected
shell file also uses the full chain, so it says what the page offered.

### 4.4 Documents and transactions

Migrators change the output only through documents. A document is one output
file. It is created on first use from base text: the template file in setup,
or the repository's current file in adopt. It knows how to merge, how to
check an entry, and how to render.

```python
class Docs:
    def __init__(self, mode: str, template: Path, repo: Path,
                 home: str, host: str, strict: bool): ...   # mode: "setup" | "adopt"
    def begin(self, owner: str) -> "Txn": ...      # asserts that no other transaction is open
    # Core only, never on a Txn:
    def drop(self, rel: str) -> None: ...          # the copy guard: remove a copy, its file, and the links to it
    def render(self) -> Tuple[Dict[str, str], Dict[str, str]]: ...   # (tracked files, local/ files) as text;
                                                   # writer.render wraps it
    conflicts: List[Conflict]                      # reported under "Problems during the migration" from step 11

class Txn:
    # The same accessors as Docs. Each returns a view on the committed document.
    def list(self, path: str) -> ListView: ...
    def mise(self) -> MiseView: ...
    def links(self) -> LinksView: ...              # setup only
    def shell_rc(self, main: str) -> ShellRcView: ...   # setup only; main in base.SHELL_LAYERS
    def file(self, path: str) -> FileView: ...     # setup only
    def shell_nix(self) -> ShellNixView: ...       # setup only; owner "core" only (the keymap)
    copies: CopiesView                             # setup only
    report: ReportView
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
```

The rules for every view operation:

1. **Check the entry at `add()`.** A bad entry raises `ValueError`, inside
   the migrator's transaction, so it is isolated to that migrator. Rendering
   committed entries therefore never fails.
2. **Resolve against committed plus pending entries.** What is already in the
   document's base text wins. Then the first writer in `MIGRATORS` order
   wins.
3. **The same entry again is a no-op** that returns `False`.
4. **A different value for the same key or target is a conflict.** It is
   recorded with both owners. In strict mode it raises. Otherwise the first
   value stays and the conflict appears under "Problems during the
   migration".
   Repository paths follow a stricter rule. A path is taken by an earlier
   copy, by a fixed document path (the list files, `config/mise.toml`,
   `nix/home.nix`, `nix/shell.nix`, `nix/user.nix`, the shell files), by
   anything under `local/`, or by a whole file (`FileDoc`) that has text.
   `Copies.add` on a taken path makes no copy and returns `False`, with no
   conflict: the migrator handles it (for example `other_config` keeps the
   file in place and adds a to-do). A whole file set on a path that a copy
   takes is a conflict. From step 6, paths overlap too: a path is taken when
   it is inside a copy, or when it would contain a taken path. This compares
   path parts (`config/bash2` is not inside `config/bash`), and it does not
   look at the file system, so a file copy is treated like a directory copy.
   Parts are compared the way APFS compares names by default: without case
   and without Unicode normalization differences (the key of a part is
   `NFD(casefold(NFD(part)))`), so `config/Zsh` is the template's
   `config/zsh`. The files of the template are taken paths too.
   `Copies.taken(rel)` returns `None`, or `(relation, path)`: `("is", rel)`
   when a copy, a document, or a template file has the path; `("inside",
   outer)` when it is inside the copied directory `outer`; `("contains",
   inner)` when a copy there would contain `inner`, the first taken path in
   path order.
5. **`add()` returns `True` or `False` right away**, so the migrator writes
   correct report rows ("carried", "already in your mise config").
6. **Nothing reaches the committed document before `commit()`.** That
   includes report rows and copies. `commit()` only appends entries that
   were already checked, so it cannot fail (an assert enforces it).
   `rollback()` drops the pending entries.

There is no priority table. Base text first, then `MIGRATORS` order, is the
whole rule. If a real case ever needs a later migrator to win, add an
explicit `priority=` argument to that one view method.

The document types:

| Type | Files | Operations | Checks at add | Adopt mode |
|---|---|---|---|---|
| `ListDoc` | `nix/brews.txt`, `nix/casks.txt`, `nix/taps.txt`, `nix/mas.txt` | `add(entry, comment="") -> bool`, `names()`, `added()` | brews/casks: one word without `#`; taps: `owner/repo` and an optional URL without spaces; mas: digits, a space, then a non-empty name | yes: appends after the existing text, with one `# From <host>, see MIGRATION.md.` comment per run |
| `MiseDoc` | `config/mise.toml` | `add_tool(key, value, comment="") -> bool`, `has_tool(key)`, `set_base(text)`, `set_setting(key, value) -> bool`, `replace_setting(key, value, expect) -> bool`, `added()` | key: non-empty, no newline; value: str, list of str, or a flat table of str/int/bool | yes (`add_tool` only) |
| `LinksDoc` | `nix/home.nix` `dotkit.links` (anchor `    ".zshrc.local" = "config/zsh/zshrc";\n`, `plan.py:834`) | `link(target, source_or_None) -> bool` | target relative to home, no `..`; source a repository path or None | no |
| `ShellRcDoc` | `config/zsh/zshenv`, `config/zsh/zprofile`, `config/zsh/zshrc`, `config/bash/bash_profile`, and their `local/<name>.<shell>` | `add_part(header, text)`, `add_secret(text)`, `ensure()` (write the file even when empty) | main in `SHELL_LAYERS` | no |
| `ShellNixDoc` | `nix/shell.nix` | `set_keymap(value)`, written by the Session | `emacs` or `vi` | no |
| `FileDoc` | a whole file with one owner: `nix/user.nix`, a merged `config/gitconfig` | `set(text)` | the path is not owned by another document | no |
| `Copies` | files and directories copied into the repository | `add(rel, src) -> bool`, `False` when `rel` is taken (rule 4); `taken(rel) -> Optional[Tuple[str, str]]`, `(relation, path)` (rule 4) | `src` absolute and inside the home directory; `rel` relative | no |
| `ReportDoc` | `MIGRATION.md`, the summary | `carried(what, where)`, `todo(section, intro, items)`, `blocker(msg)` | `section` in `report.SECTIONS` | yes (adopt output) |

`MiseDoc` details:

- `set_base(text)` is used by `config_files` when your mise config is carried
  as "layer": your file replaces the template text (today's `_merge_mise`,
  `plan.py:1014-1017`). Keys in it count as existing text.
- **The order of `set_base` and `add_tool` does not matter.** `config_files`
  comes after the tool migrators in `MIGRATORS`, so `set_base` usually runs
  last. The tool migrators therefore never rely on the document to skip the
  tools of your mise config. They ask a pure helper:
  `config_files.layered_mise(facts, choices)` until step 12, and
  `Declared.mise_key` from step 12. `MiseDoc` keeps tool entries apart from
  the base text and renders them into the final base. If `set_base` brings a
  key that a committed entry already has, that is a conflict (strict mode
  raises).
- **Settings come after `set_base`.** Unlike tool entries, setting edits
  work on the base text, so `set_base` raises once a setting was edited
  (`test_settings_come_after_set_base`). Only `config_files`, which calls
  `set_base`, edits the settings that come from your config, right after
  it.
- `add_tool` inserts at the end of `[tools]`, or creates `[tools]`
  (today's `insert_tools`, `plan.py:286-299`). Render checks that the result
  parses with `tomllib`. If it does not, the render falls back to the base
  text and lists the tools as a to-do (today's `plan.py:814-821`).
- **Settings are values, not merged lists.** mise merges `[settings]` key by
  key, and an array in a higher layer replaces the whole array
  (`minimum_release_age_excludes` is the one key that mise appends).
  `MiseDoc` never merges arrays on its own.
- `set_setting(key, value)` adds a key only when it is absent: into the
  existing `[settings]` table, or into a new one at the end.
- `replace_setting(key, value, expect)` changes a key that exists only when
  its parsed value equals `expect`, and only when it is a single line
  `key = [...]` inside a `[settings]` table. A dotted key at the top level,
  an inline `settings = {…}`, or a multi-line value are refused. After the
  edit, `tomllib` parses the result, and it must equal the original data with
  only that key changed. Otherwise the edit is undone. It returns `False` when
  it changes nothing, and the caller then writes a to-do.

`ShellRcDoc` details: the header is the template `config/zsh/zshrc`, or the
fixed headers of `plan.py:566-577`. Parts follow with their `# From ~/<file>.`
line. Secrets go to `local/<name>.<shell>` (mode 0600, directory 0700), with
the `source` block in front (`plan.py:559-564`). `ensure()` covers a layer
file that exists but carries nothing: the repository file still has to exist,
so that the link moves the old file aside (`plan.py:578-586`). The `syntax`
check (`check_syntax`, `zsh -n`/`bash -n`) is done by the `shell` migrator
before it adds the text, as today (`plan.py:524-538`).

### 4.5 Declared

One de-duplication rule for setup and adopt.

```python
class Declared:
    @classmethod
    def for_setup(cls, base: Base, facts: Facts) -> "Declared": ...
    @classmethod
    def for_repo(cls, base: Base, repo: Path, nix_declared: dict, facts: Facts) -> "Declared": ...
    def brew(self, name: str, kind: str) -> Optional[str]   # what provides it, e.g. "ripgrep (dotkit, from Nix)",
                                                             # "jq (your mise config)"; None: nothing does
    def mise_key(self, key: str) -> bool
    def tool(self, name: str) -> bool                        # via BREW_ALIASES and mise_short
    def tap(self, name: str) -> bool
    def mas_id(self, app_id: int) -> bool
    def mas_name(self, name: str) -> bool                    # a name that is taken: the raw names of
                                                             # nix/mas.txt, plus keys written by hand into
                                                             # homebrew.masApps (section 12.3)
```

`Declared` contains:

- **always:** the base lists and the base mise tools, plus the tools of your
  `~/.config/mise/config.toml`, whether it is carried as "layer" or kept in
  place (in both cases mise still reads it);
- **`for_repo` only:** the repository's `nix/packages.txt`, `nix/brews.txt`,
  `nix/casks.txt`, `nix/taps.txt`, `nix/mas.txt` and `config/mise.toml`, plus
  the `taps` and `masApps` that `dotkit` evaluates from the configuration
  (`dotkit:308-310`). The evaluated values already include `nix/taps.txt`
  and `nix/mas.txt`, and any entries that someone wrote into `homebrew.*` by
  hand. Adopt only ever writes to the two list files.

A Homebrew formula is skipped when `brew()` returns something. The report
says what provides it and how to remove the Homebrew copy. This replaces
`Planner.brew_split` (`plan.py:110-118`) and `adopt.missing`
(`adopt.py:31-74`).

The `appstore` migrator gives an app the `<name> (<id>)` label (section
12.3) when `Declared.mas_name` or the `nix/mas.txt` document already has its
name. The `homebrew` migrator checks a tap with the same functions that
`ListDoc` uses at add time. Until step 12, `plan.py` keeps `tap_lines` and
`mas_lines` for adopt, and they call those `docs.py` functions.

### 4.6 Failure

```python
@dataclass
class Failure:
    migrator: str            # "runtimes", or "identity"
    title: str
    phase: str               # "scan" | "pages" | "plan" | "adopt" | "skipped" | "conflict"
    error: str               # "KeyError: 'versions'": at most 200 characters, the home as ~, masked (section 6)
    where: str               # the last traceback frame inside dotkit_migrate: "migrators/runtimes.py:88 in plan"
    manual: str              # Migrator.manual_steps(ctx)
    reason: str = ""         # for "skipped": "Shell lines failed"
```

### 4.7 Session

```python
class Session:
    def __init__(self, ctx: Context, migrators=MIGRATORS, strict: bool = False,
                 check_syntax: Optional[Callable[[str, str], bool]] = None): ...
    def scan(self) -> Facts                          # isolated per migrator; cached
    def pages(self) -> List[Page]                    # isolated per migrator; cached
    def defaults(self, repo: str) -> Choices
    def build(self, choices: Choices) -> Plan        # fresh Docs on each call
    def adopt(self, repo: Path, nix_declared: dict, opts: AdoptOptions) -> AdoptResult
    failures: List[Failure]
```

`build()`:

1. Create `Docs("setup")`.
2. Run each migrator's `plan` in its own transaction (section 7).
3. Write the core documents: `nix/user.nix`, the keymap.
4. `writer.render` and the plan-time guard.
5. `checks.py`.
6. The summary and `MIGRATION.md`.

The wizard calls `build()` each time the review screen opens, and
`__main__` calls it again before writing. Each call is deterministic for the
same choices.

### 4.8 Plan and the writer

```python
@dataclass
class Plan:
    repo: Path; keymap: str; home: str
    files: Dict[str, str]           # repository path -> text, tracked
    copies: Dict[str, Path]         # repository path -> source
    local: Dict[str, str]           # local/* -> text, untracked
    links: Dict[str, Optional[str]] # for the summary and checks
    move_links: List[str]           # for scripts/setup: .build/links-in-way
    blockers: List[str]
    failures: List[Failure]
    summary: List[str]
    report: str
```

`writer.py`:

- `render(docs) -> Plan` (`Plan` is in `model.py`). The keymap comes from
  `ShellNixDoc`, which core sets through `Txn.shell_nix()`. Inside
  `render`, in this order:
  1. the copy guard (section 6), which drops a copy with a secret through
     `Docs.drop`, so that its links never reach `nix/home.nix`;
  2. the documents render;
  3. the file guard over the rendered tracked files;
  4. if `local` is not empty, `.gitignore` gets the `/local/` block
     (`plan.py:839-841`).

  There is no separate public guard. After `render`, `checks.py` runs, and
  then the report and the summary are rendered and masked (`mask_lines`):
  in `_Build.render` until step 8, then in `Session.build`.
- `write_new(plan, template)` for setup:
  1. copy the template into a temp directory next to the repository;
  2. add the copies, the files, and `local/`;
  3. write `.build/links-in-way`;
  4. verify the staged tree (section 6);
  5. `git init`;
  6. rename the directory to the repository path.

  If anything fails, the temp directory is removed (today's `write.py`).
  The removal also works when a copy brought read-only directories (mode
  0555): on a permission error it makes the parent directory writable
  (0o700), and the path itself when it is a directory, and tries again
  (`onexc` on Python 3.12 and newer, `onerror` on 3.9); if the directory
  still remains, a note on stderr names it.
- `apply_edits(result, repo)` for adopt:
  1. write every changed file as a sibling temp file;
  2. verify them;
  3. `os.replace` them one by one;
  4. if one replace fails, write back the old texts kept in memory.

`checks.py` holds today's `link_targets`, `moved_aside`, `links_in_the_way`
and `summary` (`plan.py:900-1002`). It works on `LinksDoc` and the file
system, and adds the blockers (`~/.config` is a link) and `move_links`. It is
core: it runs whatever the migrators did.

## 5. Report

`MIGRATION.md` keeps today's shape (`report.py:70-103`): "Carried over"
(one row per accepted contribution, in `MIGRATORS` order), then "To do" with
sections in `report.SECTIONS` order. `SECTIONS` is the one ordered list of
titles. A section that is not in it raises in strict mode, and a test checks
every migrator's `sections`. The order:

1. Problems during the migration
2. Secrets
3. Logins
4. Config files
5. Shell lines (and its subsections in `rcparse.CATEGORIES` order)
6. Other shell files
7. Shell frameworks
8. Login shell
9. Version managers
10. Tools that moved to mise
11. Tools that are not in mise
12. mise
13. Leftovers that can still take effect
14. Homebrew copies of dotkit tools
15. Not carried, by your choice
16. Homebrew taps
17. App Store apps
18. Other config
19. Other config that stays out of the repository
20. Scan warnings

A command in the report that names a path quotes it for the shell, with a
leading `~/` outside the quotes so that the shell still expands it:
`rm -rf ~/'Library/Application Support/fnm'`. A path without special
characters stays as it is (`rm ~/go/bin/hey`). One helper builds these
paths for every migrator.

The summary (the review screen, `--dry-run`, the plain prompts) starts with
the problems line when there are failures (section 7.4), then today's lines
(`plan.py:978-1002`).

## 6. Secrets

Secrets are handled at five points:

1. **Detection during the scan.** Every config file the scan finds gets its
   `secrets`, including `~/.config/mise/config.toml`. Planning no longer
   calls `secrets.has_secret` (today's `plan.py:220`, `707`).
2. **Routing in the migrators.** `ShellRcDoc.add_secret` routes secret
   statements (rcparse's `SecretLine`) to `local/`. A config file with a
   secret gets only `keep`/`dotkit`. `other_config` does not offer an entry
   with a secret.
3. **The plan-time guard (core, inside `writer.render`).** Today's `guard_copies`
   and `guard_files` run over every tracked rendered file and every copy
   source. A shell file is split by statement, never in the middle of one.
   Other files are masked line by line. Then the rendered report and adopt's
   output are masked line by line (`mask_lines`, in `plan.py` until step 6
   moves it to `writer.py`). From step 6, the summary is masked the same way. The guard changes the plan and reports what it did, so
   dry-run and the review screen show its result.
4. **Write-time verification (core, in `writer`).** Before the rename or
   `os.replace`, scan the staged tracked files (copies included; `local/`,
   `.build/`, and `.git` excluded, because Git does not track them). It uses
   exactly the checks of the plan-time guard (`secrets.scan_text` for files,
   `secrets.has_secret` for copied files and template files, the same
   strictness, links inside a copied directory and `.DS_Store` skipped), so
   it can only find what differs from what the guard saw, for example a file
   that changed between the scan and the copy. It changes nothing. A find raises
   `SecretInRepository` (the file and the kind of secret, never the value),
   which aborts the write and leaves nothing behind. A directory that the
   verification cannot read also aborts it: it fails closed. `__main__` prints
   "dotkit setup: <message>. Nothing was written." (or "dotkit migrate: …")
   and exits 2; `--debug` (from step 11) adds the traceback.
5. **Failure text.** `Failure.error` and the `--debug` traceback pass through
   `secrets.line_secret` line by line. A line with a secret becomes "(this
   line has a secret; not shown)".

Rules that keep this enforceable:

- **Migrators cannot write files.** An AST test over `migrators/` fails on
  `open(…)` with a write or append mode, `.write_text`, `.write_bytes`,
  `shutil.copy*`, `shutil.copytree`, `os.replace`, `os.rename`, `os.remove`,
  `os.unlink`, and `subprocess`, and on an import of `proc`, `subprocess`,
  `writer`, `ui`, or `curses` (section 3). Commands run only through
  `ctx.runner` (read-only by contract).
- **The guard and the verification fail closed.** If either raises, the run
  stops and nothing is written (section 7.2).
- **A property test:** a home directory with a `fake.TOKENS` value in every
  file that any migrator reads must give no token in `Plan.files`, the copied
  sources, the report, or the summary.

## 7. Errors

### 7.1 Expected problems

A command that fails or times out (`Runner.run` returns `None`), an
`OSError` on a file, or a file that does not parse is not a bug. The
migrator handles it, keeps what it could read, and adds a line to
`facts.warnings`. The report lists the line under "Scan warnings".

### 7.2 Crashes: isolated per migrator

An unexpected `Exception` in a migrator's `scan`, `pages`, `plan`, or
`adopt` is a crash. `KeyboardInterrupt` and `SystemExit` are not caught. When
a migrator crashes:

- **in `scan`:** its facts object and its warnings are thrown away, and
  `facts.get(key)` returns `None`;
- **in `pages`:** it has no pages, and its `plan` does not run (without pages
  there are no defaults and no choices);
- **in `plan` or `adopt`:** its transaction is rolled back, so it contributes
  nothing. No half of its entries, report rows, or copies stay.
- **in every case:** one `Failure` is recorded, and the rest of the run
  continues.

```python
for m in self.migrators:
    if m.key in self.excluded:          # failed earlier, or skipped (7.3)
        continue
    txn = docs.begin(m.key)
    try:
        m.plan(facts, choices, txn)
        txn.commit()
    except Exception as e:
        txn.rollback()
        self.fail(m, "plan", e)         # records the Failure; re-raises in strict mode
```

There is no fallback. A failed migrator means plain-template behavior for
its part, and its `manual_steps` text says exactly what that is (section
7.5).

Core code is not isolated: the documents' rendering, the writer, the secret
guard, the verification, `checks.py`, and the UI frontends. There is no safe
partial result for them, so an exception there stops the run with nothing
written. The write is atomic, so nothing is half done. A UI exception is
shown after curses has restored the terminal.

### 7.3 Migrators that depend on others

A migrator that reads other facts through `facts.get()` must handle `None`
and do less. For example, `frameworks` without the shell facts leaves out the
line counts. A migrator that cannot work without another lists it in
`needs`. If that one failed, it does not run, and the Session records
`Failure(phase="skipped", reason="<title> failed")`. `needs` is only for
skipping. It never changes the order.

### 7.4 What the user sees

- **MIGRATION.md**, first section, "Problems during the migration":

  > **Runtime versions of version managers**: failed while planning:
  > `KeyError: 'versions'` (migrators/runtimes.py:88). Nothing of it is in
  > this repository. By hand: … (manual_steps). Details: `dotkit-migrate
  > setup --dry-run --debug`. Please report it.

- **Summary**, first lines: "Problems: 1 part of the migration failed:
  runtimes (planning). It is left out; MIGRATION.md says what to do."
- **stderr**, in every mode including `--yes`: the same short line after
  the scan, and after the final build.
- **`--debug`** (or `DOTKIT_MIGRATE_DEBUG=1`) also prints the masked
  traceback to stderr. Nothing is written outside the repository, so dry-run
  still changes nothing.
- **The UI:** section 8.
- **Exit code:** 0 when migrators failed. Setup goes on to build; the
  failures are on stderr, in the summary, and in MIGRATION.md. Exit codes for
  other errors stay as today (`__main__.py`: 2 for a bad path, a missing
  terminal, or blockers; 1 for quit).
- **`dotkit migrate`:** adds what the working migrators found, and prints
  "Could not check: runtimes (…); nothing of it was added."

### 7.5 `manual_steps` for `shell` and `config_files`

These two must describe what the template does without them. That behavior
comes from `nix/home.nix:118-158`, `templates/default/nix/home.nix`, and
Home Manager's `backupFileExtension = "before-dotkit"` (`flake.nix:84-88`).
Generate the file lists from `base.SHELL_LAYERS` and `base.LINKS`, so the
text stays true.

`shell`:

> Nothing of your shell files is carried. dotkit links its own ~/.zshenv,
> ~/.zprofile, ~/.zshrc, and ~/.bash_profile, and the template's
> config/zsh/zshrc as ~/.zshrc.local. The first apply renames your files at
> these paths to <name>.before-dotkit. Your ~/.zshenv.local,
> ~/.zprofile.local, and ~/.bash_profile.local stay in place if they exist,
> and dotkit's files source them, also if your old files did not. Check what
> they run (a framework, a version manager, PATH changes). They stay outside
> the repository. Copy the lines you need from the .before-dotkit files to
> config/zsh/ and config/bash/ of this repository, and keep lines with
> secrets out of files that Git tracks.

`config_files`:

> None of your config files is carried or kept in place. dotkit links its
> own files at ~/.config/git/config, ~/.config/git/ignore,
> ~/.config/ghostty/config, ~/.config/herdr/config.toml,
> ~/.config/starship.toml, ~/.config/uv/uv.toml, ~/.npmrc, ~/.gemrc,
> ~/.bunfig.toml, ~/.editorconfig, and ~/Library/Preferences/pnpm/config.yaml,
> and links config/mise.toml of this repository as ~/.config/mise/config.toml.
> The first apply renames your files at these paths to <name>.before-dotkit.
> They stay on disk, outside the repository, and the tools no longer read
> them. The tools of your old mise config are not in config/mise.toml. If a
> file with a login, such as ~/.npmrc with a token, was renamed, log in again
> or copy the token by hand; do not put it into Git. ~/.gitconfig stays in
> place, and Git still reads it after the dotkit settings. Copy the settings
> you need from the .before-dotkit files.

### 7.6 Strict mode

Strict mode re-raises where the error happens: crashes, conflicts, unknown
report sections. It is on in all unit tests (`fake.session(...)` defaults to
`strict=True`; the isolation tests turn it off explicitly), and in the CI e2e
job (`DOTKIT_MIGRATE_STRICT=1` in `.github/workflows/ci.yml`). There is also
a hidden `--strict` flag. Production runs are never strict.

## 8. UI

Screens are data. `ui/screens.py` builds a list of `Screen(kind, id, title,
intro, note, dest, lines, rows, get, set, detail)` from the Session (`note`
and `dest` come from the page; `lines` is the review text). The curses and plain
frontends render the same screens.

The sequence:

1. Where the repository goes (`text`).
2. The zsh keymap (`radio`).
3. One screen per page, in `MIGRATORS` order. Pages without items are
   skipped.
4. The plan (`review`).

The header shows `step/total` (today's `wizard.py:128-130`) for the screens
that are shown.

| Widget | Today's code | Keys |
|---|---|---|
| `TextInput` | `UI.repo` (`wizard.py:137-184`) | type, Backspace, Ctrl+U, Enter, Esc quits |
| `Radio` | `UI.keymap` (`wizard.py:186-214`) | ↑↓/space select, Enter, b back |
| `CheckList` | `UI.toggles` (`wizard.py:216-255`) | ↑↓/j k move, space toggles one item, `a` all or none, Enter next, b back, q quit; the detail line shows `Item.detail`, the page note shows under the intro |
| `ChoiceList` | `UI.files` (`wizard.py:276-320`) | ↑↓ move, space/→/l next option, ←/h previous, Enter, b |
| `Pager` | `UI.review` (`wizard.py:338-364`) | ↑↓ scroll, Enter create (or print), b back |

The input handling, the escape-sequence fix (`wizard.py:68-103`), colors, and
the minimum size stay as they are.

Plain prompts (`ui/plain.py`): a checklist page prints its numbered items
and their state. The answer is Enter (keep), numbers or ranges to toggle
(`1 3-5`), `a` (all), or `n` (none), one answer per page; an answer that
does not parse asks again. A choices page asks per item, as the old
`prompts.py:54-65` did. The plain prompts have no way back; end of input
quits, and nothing is written. `--plain` and an unusable terminal
(`wizard.available`, `wizard.py:397-417`) select it.

Problems in the UI:

- **A migrator whose scan or pages failed** has no screen. The first screen
  shows one warning line under its intro: "1 part of the scan failed:
  Runtime versions. It is left out; the plan screen and MIGRATION.md say
  more." The plain prompts print the same line before the first question.
- **A migrator whose plan failed** shows up on the review screen, which
  calls `build()`. A **Problems** block at the top, in the warning color,
  lists each failure and its phase, and says that its choices have no
  effect. `b` goes back. If the crash depends on a choice, changing that
  choice and returning rebuilds the plan, and the problem can go away.
- **Skipped dependents** are listed in the same block.
- The review lists "Not carried, by your choice: N" for each checklist page
  with N > 0, after the summary. N counts the items that were on by default
  and that you deselected, the same rule as the report section.
- **`--yes` and `--dry-run`** have no screens. Defaults come from
  `Choices.defaults`, and problems go to stderr and to the printed summary.

## 9. Testing

| Kind | Where | What it proves |
|---|---|---|
| Migrator unit | `tests/migrators/test_<key>.py` | `scan` on `fake.Home` + `FakeRunner` gives the expected facts; `pages` gives the expected items and defaults; `plan` on an in-memory `Docs` gives the expected entries and report rows; `adopt` against a `Declared` |
| Document | `tests/test_docs.py` | per type: load, add, render; conflicts; `add()` checks; `render()` never raises on accepted entries; `MiseDoc.replace_setting` refusals and the parse-compare undo; `commit()`/`rollback()`; a second `begin()` asserts |
| Writer and checks | `tests/test_writer.py`, `tests/test_checks.py` | guard behavior (today's `test_guard`, `test_final_guard_keeps_secret_shell_statements_whole`); write-time verification aborts with nothing left; atomic adopt with a fault in the second replace; links in the way (today's tests) |
| Golden plans | `tests/test_golden.py`, `tests/golden/*.json` | the full `Plan` (all fields; normalized: in `Path` values the home directory is `~`; in text it is `<HOME>` and the rest of the temp directory is `<TMP>`, so a `~` in text means the code wrote `~` itself; fixed user, date, and hostname; fake tokens as their placeholders; a fake `check_syntax`) for an empty Mac, `used_mac` (`tests/test_plan.py:27-56`), and a kitchen-sink Mac; the adopt output and the edited files. `DOTKIT_MIGRATE_REGOLD=1` rewrites them. A golden diff is how every behavior change is reviewed |
| Registry invariants | `tests/test_registry.py` | unique page keys and item-id prefixes; `sections` ⊆ `SECTIONS`; an empty home gives no warnings and no contributions; the AST no-write test; the secret property test (section 6) |
| Isolation | `tests/test_isolation.py` | for every migrator M and every phase P, M.P raising (for `plan`: after real work, so it has pending entries) gives the same plan as the registry without M, plus one `Failure`, plus `skipped` entries for its `needs` dependents; strict mode re-raises; a token in an exception message is not in the report, summary, stderr, or `--debug` output; a core failure (a document render, the guard) gives no repository directory |
| UI flow | `tests/test_ui_flow.py` with a `ScriptedFrontend` that answers screens by id and item id | page order, skipped empty pages, defaults, per-item selection, the warning line, the Problems block |
| PTY | `tests/test_wizard_pty.py`, `tests/wizard_child.py` | curses mechanics: keys, keypad sequences, resize, small window, quit, the plain prompts with answer counts taken from the Session; one test where `wizard_child.py` adds a migrator that crashes, and the wizard still finishes |
| Start and grammar | `tests/test_start.py` | `-I -B` isolation; the Python 3.9 grammar |
| e2e | `tests/e2e/migrated.sh` in CI | a used macOS runner, strict mode; each new migration adds a fixture to the e2e job and a check here |

The standard checks for every step (called STD below):

```sh
cd migrate && /usr/bin/python3 -B -m unittest discover -s tests -t . -v
cd migrate && nix shell --inputs-from .. nixpkgs#python3 nixpkgs#nixfmt -c python3 -B -m unittest discover -s tests -t . -v
nix build .#checks.aarch64-darwin.migrate
# On a real Mac, read-only. Take "before" at the start of the step, the same day; the path must not exist:
migrate/dotkit-migrate setup --dry-run --yes --repo ~/.dotkit-dryrun-probe > /tmp/dk-after.txt 2>&1
diff /tmp/dk-before.txt /tmp/dk-after.txt
```

## 10. How to add a migrator

1. **Catalog:** add entries to `catalog.py` if the migration is a family
   (a version manager, a framework, a package source rule). rcparse picks up
   new words, variables, and PATH markers from it.
2. **Module:** add `migrators/<key>.py` with a facts dataclass, `scan`,
   `pages` (stable, prefixed item ids; safe defaults), `plan`, `adopt` if
   `dotkit migrate` should add it, `manual_steps`, and `sections`.
3. **Outputs:** reuse document types. Add a document type to `docs.py` only
   for a new kind of output file, with its add-time checks and its tests.
4. **Report:** add new section titles to `report.SECTIONS`, in their place.
5. **Registry:** add the migrator to `MIGRATORS` in its place. That is its
   page order and its row order in "Carried over".
6. **Secrets:** content that can hold a secret is routed by the migrator
   (`add_secret`, restricted options). Never copy a file the scan did not
   check.
7. **Tests:** `tests/migrators/test_<key>.py`; extend the kitchen-sink
   fixture and regenerate the goldens (review the diff); the invariant and
   isolation tests cover it without new code.
8. **e2e:** add a fixture to the CI e2e job and a check in
   `tests/e2e/migrated.sh`.
9. **Docs:** update the README table "On the Mac / In the repository".

## 11. Leftovers report

The wizard never removes anything. Most leftovers are dead wood:

- dotkit links its own `~/.zshenv`, `~/.zprofile`, `~/.zshrc`, and
  `~/.bash_profile`, so the old files (renamed `.before-dotkit`) are not
  read.
- rcparse never carries framework or version-manager lines (`rcparse.py:61-99`,
  `118-121`).
- So `~/.nvm`, `~/.pyenv`, `~/.rbenv`, `~/.asdf`, `~/.volta`, `~/.sdkman`,
  `~/.oh-my-zsh`, and the like just sit on disk.

The `leftovers` migrator (last in `MIGRATORS`, report only) lists what can
still take effect, under "Leftovers that can still take effect".

How PATH works, for mise 2026.10.3, from its source and a local test:

- **Interactive zsh.** `mise activate` runs in the framework init
  (`nix/modules/zsh/default.nix:99-101`), and `~/.zshrc.local` is sourced
  after it (`config/zsh/zshrc`, end). With `activate_aggressive = false`
  (the default), mise's hook-env keeps directories that were added to PATH
  *after* activation in front of mise's tool directories. Directories that
  were on PATH *before* activation go behind mise's tools at the first
  prompt. That includes all of `DOTKIT_TOOL_PATH` (`nix/home.nix:92-101`:
  the Nix profiles, `~/.local/bin`, `/opt/homebrew/bin`, `/opt/homebrew/sbin`).
  Source: `src/cli/hook_env.rs`, `build_path_operations`, around L338-523.
  - So a carried PATH line in `config/zsh/zshrc` (`~/.zshrc.local`) that adds
    a **new** directory, such as `~/.npm-global/bin`, shadows mise's tools.
  - A carried line that only prepends a directory that was already on PATH
    is put back behind mise's tools.
  - Leftover binaries in `~/.local/bin` or `/opt/homebrew/bin` do **not**
    shadow mise in interactive zsh.
  - Lines carried into `config/zsh/zshenv` and `config/zsh/zprofile` run
    before activation, and `config/zsh/zprofile` also re-sorts PATH after
    them, so they go behind.
- **bash.** `config/bash/bash_profile` sources `~/.bash_profile.local`
  before `mise activate`, so carried bash lines go behind mise's tools.
  `~/.bashrc` is sourced after activation, in interactive bash.
- **GUI apps and launchd agents.** Their PATH is fixed:
  `DOTKIT_TOOL_PATH`, then the mise shims, then the system directories
  (`nix/darwin.nix:29-30`). There, a leftover binary with the same name in
  `~/.local/bin`, `/opt/homebrew/bin`, or `/opt/homebrew/sbin` **wins** over
  the mise shim.
- **Not checked:** nested shells, and the order relative to direnv's hook.
  The report text must not make claims about them.

What the migrator reports:

1. **Carried PATH lines that add a new directory.** For each one in
   `config/zsh/zshrc`, list the file and line. "New" means not in this set:
   the `DOTKIT_TOOL_PATH` entries (a constant in `base.py`, checked by
   `test_base` against `nix/home.nix`), `rcparse._PATH_DONE`
   (`rcparse.py:113-117`), the system directories, and the directories
   carried into `config/zsh/zshenv` or `config/zsh/zprofile`.
2. **Shadowed tools.** If such a directory, or `~/.local/bin`,
   `/opt/homebrew/bin`, or `/opt/homebrew/sbin`, contains a command with the
   same name as a tool that moved to mise in this run, warn. Name the
   context:
   - for a carried new directory: "interactive shells";
   - for a toolPath directory: "GUI apps and launchd agents; interactive
     shells use mise's".

   Suggested fixes, in order:
   - drop the line;
   - move it to config/zsh/zprofile (`~/.zprofile.local`), which runs before
     mise starts;
   - uninstall the leftover binary (`pipx uninstall ruff`,
     `npm uninstall -g typescript`, `brew uninstall node`);
   - as a last resort, `activate_aggressive = true` in `[settings]` of
     config/mise.toml. It acts only on full hook-env runs, and it does not
     change GUI apps.
3. **Shell files that dotkit does not replace and that still run:**
   `~/.bashrc` (interactive bash, after `mise activate`), `~/.zlogin` and
   `~/.zlogout` (zsh reads them), `~/.profile` (`sh -l`). Parse them with
   rcparse in report-only mode, and list their framework, version-manager,
   and new-PATH lines as "still runs". Installers for nvm, sdkman, and conda
   write to `~/.bashrc`.
4. **mise files that mise still reads:** `~/.tool-versions` (always read,
   whatever the idiomatic settings) and `~/.config/mise/conf.d/*.toml`. They
   override the dotkit defaults for everything under `~`.
5. **Undeclared Homebrew packages,** including deselected ones:
   - they stay installed and on PATH;
   - `dotkit upgrade brew` upgrades only the declared ones (`dotkit:250-256`),
     so they go stale;
   - with `homebrew.onActivation.cleanup = "uninstall"`, activation
     uninstalls them (for casks, the apps);
   - the scan sees only `brew leaves --installed-on-request`
     (`scan.py:244`), so dependency formulae are on PATH but not listed.
6. **A reference check next to every removal command:** "Before `rm -rf
   ~/.pyenv`, check that nothing points into it: LaunchAgents, IDE
   interpreter settings, git hooks, crontab (`grep -rl ~/.pyenv
   ~/Library/LaunchAgents`)."

The facts this needs:

- `Package.commands`: the command names of each package. npm: `bin` in
  `package.json` (`scan.py:345-352`); pipx: the apps in `pipx_metadata.json`;
  uv: the receipt's entry points; cargo: `bins` in `.crates2.json`; Go: the
  file name; runtimes: `node`, `npm`, `npx`, `python3`, `python`, `pip3`,
  `ruby`, `gem`.
- The `shell` scan runs rcparse (pure) at scan time, and lists the
  executables of each existing directory in a carried PATH line.
- The `leftovers` scan lists the executables of `~/.local/bin`,
  `<brew_prefix>/bin`, and `<brew_prefix>/sbin` (names only).

## 12. Worked sketches

### 12.1 nvm → mise (`runtimes`)

```python
# catalog.py
VersionManagerSpec(
    name="nvm", dirs=(".nvm",), env_var="NVM_DIR", tool="node", versions="versions/node",
    default=lambda root: read_text(root / "alias/default").strip(),
    mise_replaces=True, words=("nvm", "NVM_DIR"), extra_vars=("NVM_DIR",),
    path_markers=(r"\.nvm",),                       # regex fragments after a "/"; fnm has two,
                                                     # conda is r"(?:ana|mini)conda\d?(?=/)"
    npm_globs=(("versions/node/*/lib/node_modules", -3),),   # (glob, index of the version in the path parts)
)
# Line-only specs (detect=False) keep rcparse's words for rvm, nodenv, goenv, chruby, tfenv, conda,
# mamba, micromamba (rcparse.py:91-93).
# The manager's directory: catalog.manager_dir(spec, home, env). If env[spec.env_var], stripped,
# is an absolute path, or is ~ or starts with ~/ (expanded against the scanned home), it is the
# directory, and dirs are not tried. When it does not exist, is a file, or is the home or a parent
# of the home (by file identity: a link or another case of the name counts), the manager is not
# found. A blank or relative value counts as unset: then the first of dirs that exists. Shown with
# the home as ~.
# npm_globs are relative to that directory, so only one fnm location is scanned. The node_tools
# scan uses the same function (it cannot read the runtimes facts), also for VOLTA_HOME.
# env_var: NVM_DIR, FNM_DIR, VOLTA_HOME, PYENV_ROOT, RBENV_ROOT, ASDF_DATA_DIR, SDKMAN_DIR, JENV_ROOT.

# migrators/runtimes.py
class Runtimes(Migrator):
    key = "runtimes"; title = "Runtime versions of version managers"
    sections = ("Version managers", "mise")

    def scan(self, ctx):
        return RuntimeFacts([detect(spec, ctx) for spec in catalog.VERSION_MANAGERS if spec.detect],
                            mise_extra=extra_mise_files(ctx))          # today's scan_mise (scan.py:462-472)

    def pages(self, f):
        lines = runtime_lines(facts_of(f))                              # today's plan.py:123-141
        return [Page("runtimes", self.title, "config/mise.toml", INTRO, "checklist",
                     [Item(f"runtime:{t}", f"{t} {', '.join(v)}", default=False) for t, v in lines.items()],
                     note="off: mise installs them; the base has " + base_runtimes(f.base))]

    def plan(self, f, c, txn):
        user = config_files.layered_mise(f, c)                          # your config, if carried as "layer"
        for tool, versions in runtime_lines(facts_of(f)).items():
            if not c.selected("runtimes", f"runtime:{tool}"):
                continue
            if user is not None and tool in user.tools:
                ...    # "config/mise.toml has <tool> from your mise config already, so these versions were
                       #  not added; `mise use -g <tool>@<version>` for one you need"
            elif txn.mise().add_tool(tool, versions if len(versions) > 1 else versions[0]):
                ...    # carried row; the item says "config/mise.toml has these versions now"
        for vm in facts_of(f).managers:                                 # today's plan.py:468-493
            lines = [x for x in shell.findings(f) if x.source == vm.name]   # None-safe helper
            txn.report.todo("Version managers", intro(f.base), [step_text(vm, lines, c)])

# migrators/config_files.py: the idiomatic settings. It owns your mise config, so it writes the union
# right after set_base (settings come after set_base, section 4.4).
IDIOMATIC = ("idiomatic_version_file_enable_tools", "idiomatic_version_file_disable_files")

def mise_settings(f, c, txn, user):                     # user: your parsed ~/.config/mise/config.toml
    for key in IDIOMATIC:
        if key not in user.settings:
            continue                                    # never write a key you do not set: a copy
                                                        # would freeze dotkit's list
        base_val = f.base.mise_settings.get(key, [])
        yours = user.settings[key]
        if not (isinstance(yours, list) and all(isinstance(x, str) for x in yours)):
            txn.report.todo("mise", "", [f"your mise config sets `{key}` to {toml(yours)}, which is not a "
                                         f"list of names. dotkit's value is {toml(base_val)}. Check it."])
            continue                                    # no union, no edit
        if set(base_val) <= set(yours):
            continue                                    # yours has all of dotkit's: order does not matter
        union = dedup(list(base_val) + yours)           # dotkit's order first, then yours
        # toml(): a backticked TOML value, ready to paste, e.g. `["node", "python"]`.
        if layered(c):                                  # carried as "layer": edit the repository file
            if txn.mise().replace_setting(key, union, expect=yours):
                txn.report.todo("mise", "", [f"config/mise.toml: `{key}` is now {toml(union)}, dotkit's list "
                                             f"and yours, because a list in your config replaces dotkit's "
                                             f"(yours was {toml(yours)}). Check it."])
            else:
                txn.report.todo("mise", "", [f"config/mise.toml sets `{key}`, which replaces dotkit's "
                                             f"{toml(base_val)}. Set it to {toml(union)}."])
        else:                                           # kept in place: never edit it, only say so
            txn.report.todo("mise", "", [f"~/.config/mise/config.toml sets `{key}`, which replaces "
                                         f"dotkit's {toml(base_val)}. Set it to {toml(union)}."])
```

- **`Base.mise_settings`** reads `[settings]` of the base `config/mise.toml`,
  as `Base.mise` reads `[tools]` (`base.py:123-130`). Nothing hard-codes the
  base value. Today the base enables `["node", "python", "ruby", "rust"]` and
  disables `["ruby:Gemfile", "node:package.json"]`.
- **No hard-coded runtime versions in text.** The intro's "The base has Node
  24, Python 3.14, and Ruby 3.4" (`plan.py:490-493`) and the page note
  (`plan.py:181`) come from `Base.mise` (`base_runtimes()`), so a base
  change can't make them wrong. The display name of a tool comes from one
  function in `catalog.py` (today `key.capitalize()`: `node` → `Node`,
  `go` → `Go`); a name that needs another spelling gets an entry there.
- **The project-file hint** (`rcparse.py:43-44`) is correct for the current
  base, which reads `.nvmrc`, `.node-version`, `.python-version`, and
  `.ruby-version`. Keep it, and mention that `.tool-versions` is always read.
- **The union never adds tools of its own.** It is dotkit's list plus yours.

### 12.2 Oh My Zsh (`frameworks`, report only)

```python
FrameworkSpec(name="Oh My Zsh", paths=(".oh-my-zsh",),
              vars=("ZSH", "ZSH_THEME", "ZSH_CUSTOM", "ZSH_CACHE_DIR", "DISABLE_AUTO_UPDATE", ...),
              words=("oh-my-zsh", r"\$ZSH\b", r"\$\{ZSH\}", "ZSH_THEME", r"\bomz\b", r"\bplugins=\("))

class Frameworks(Migrator):
    key = "frameworks"; title = "Shell frameworks"; sections = ("Shell frameworks",)
    def scan(self, ctx):  return FrameworkFacts([found(spec, ctx) for spec in catalog.FRAMEWORKS])  # scan.py:511-517
    def pages(self, f):   return []
    def plan(self, f, c, txn):
        for fw in facts_of(f).found:
            n = sum(1 for x in shell.findings(f) if x.source == fw.name)
            txn.report.todo("Shell frameworks", INTRO,
                [f"{fw.name} ({fw.path}): dotkit's zsh does not load it. {n} lines of your shell files used it "
                 f"(see \"Shell lines: Shell framework\"). Before `rm -rf {fw.path}`, check that nothing points "
                 f"into it."])
```

`ZDOTDIR` is a core zsh variable. An assignment of it is reported as a
framework line, as today, but it is not attributed to any framework
(`Finding.source` is empty): `catalog.UNATTRIBUTED_FRAMEWORK_VARS`.

Advanced configs are handled by hand. There is no carrying of `$ZSH_CUSTOM`,
no plugin mapping, and no abbreviations. `ShellNixDoc` has only the keymap.

### 12.3 App Store apps (`appstore`)

```python
class AppStore(Migrator):
    key = "appstore"; title = "App Store apps"; sections = ("App Store apps", "Not carried, by your choice")
    def scan(self, ctx):  ...       # `mas list`, or the _MASReceipt names without mas (scan.py:267-283)
    def pages(self, f):
        return [Page("mas", self.title, "nix/mas.txt", INTRO, "checklist",
                     [Item(f"mas:{i}", n) for i, n in facts_of(f).apps])]
    def plan(self, f, c, txn):
        for i, n in facts_of(f).apps:
            if c.selected("mas", f"mas:{i}"):
                txn.list("nix/mas.txt").add(f"{i} {n}")
            else:
                ...                                                     # "Not carried, by your choice"
        if facts_of(f).receipts_only:
            txn.report.todo("App Store apps", "...run `dotkit add brew:mas`...", facts_of(f).receipts_only)
    def adopt(self, f, declared, txn, opts):
        for i, n in facts_of(f).apps:
            if not declared.mas_id(i):
                txn.list("nix/mas.txt").add(f"{i} {n}")
```

`nix/mas.txt` format: one app per line, `<id> <name>`, where the name is the
rest of the line, trimmed (it can contain spaces, `#`, and Unicode). A line
whose first non-blank character is `#` is a comment, and blank lines are
ignored. If two lines have the same name, the first one wins (as
`plan.py:803-805` does today). `nix/taps.txt` format: `<owner/repo>` or
`<owner/repo> <clone URL>`, with the comments of `nix/read-list.nix`; a line
with a URL becomes `{ name; clone_target; }`.

The nix module reads both files through two options, `dotkit.lists.taps` and
`dotkit.lists.masApps` (lists of files, default `[ ]`), and sets
`homebrew.taps` and `homebrew.masApps` from them. The template has the two
files and lists them in its `nix/darwin.nix`, which no longer has the
`homebrew.taps = [ ];` and `homebrew.masApps = { };` lines.
`dotkit add mas:ID` and `dotkit remove mas:ID` (`scripts/dotkit-pkg`) edit
`nix/mas.txt`. The name comes from `mas`; without `mas`, the command says to
run `dotkit add brew:mas` first.

## 13. Refactor steps

The base fix for mise's idiomatic version files is already done
(`config/mise.toml`), and the existing project-file hints are correct. The
code in `migrate/` is unchanged since `d91296a`, so the line references hold.

Rules for every step:

- **No compatibility code.** When code moves, every caller and every test
  moves with it in the same commit. No re-exported old names, no
  compatibility properties, no wrappers that keep an old module or function
  alive, no transitional document types.
- **Scaffolding never outlives its step.** A step that spans several commits
  (step 8) may have code in its old and its new place between commits. At
  the end of the step, the old place is gone.
- **No compatibility with repositories from earlier dotkit versions**
  (section 1). No step converts old formats.
- **Goldens.** Each step is one commit, or a short series of commits, and
  must pass STD. "No behavior change" means the goldens from step 1 stay
  byte-identical and the PTY key sequences stay unchanged. A step that
  changes behavior updates the goldens on purpose, in its own commit, and
  the reviewer checks that diff.

| # | Step | Behavior | Needs | Done when |
|---|---|---|---|---|
| 1 | **Golden plans.** `tests/test_golden.py` and `tests/golden/`: the empty Mac, `used_mac`, and a kitchen-sink Mac (nvm, pyenv, asdf, sdkman; Oh My Zsh; cargo, go, uv tools; a Stow-linked `~/.config/ghostty`; a secret in a function; vi keymap; every group on; a custom tap; App Store apps with spaces, `#`, and Unicode in their names). Serialize the whole `Plan`, normalized: in `Path` values the home directory is `~`; in text it is `<HOME>` and the rest of the temp directory is `<TMP>`, so a `~` in text means the code wrote `~` itself; fixed user, date, and hostname; fake tokens as their placeholders; a fake `check_syntax` (see the docstring of `tests/test_golden.py`). Also goldens for the adopt output and the files it edits | no change | — | STD; changing one report string in `plan.py` fails a golden (check once by hand); `/tmp/dk-before.txt` taken |
| 1a | **Report fixes found by the goldens.** (1) The Secrets intro (`plan.py:548-550`) is reworded so that `secrets.line_secret` does not flag it ("password manager" matches `_SETTINGS`); `secrets.py` stays as it is. (2) A report section holds blocks, each an intro and its items, in the order the blocks first appear: `todo(title, intro, items)` adds to the block with the same intro or starts a new one, and items without an intro go to the section's block without one (`report.py:48-55` kept only the first intro, so old taps showed under the custom-tap intro and cargo/Go items under npm's); section order and `Report.count` stay. (3) `scan_go` stores `where` with the home as `~` (`scan.py:437`, `439`), like the other sources; `rm` in `plan.py:413` stays unquoted. One commit with the code and tests, then one commit with only the regenerated goldens | changes report text | 1 | goldens updated and reviewed; tests: the `used_mac` report has the Secrets intro verbatim; with a custom tap and `homebrew/cask-fonts`, the `brew untap` item comes after "Homebrew merged these taps into its own. Remove them."; npm and cargo tools that are not carried keep their own intros; a Go binary in `~/go/bin` has `where == "~/go/bin/<name>"`, and one in a `GOBIN` outside home stays absolute |
| 2 | **Taps and App Store apps as data files.** The nix readers and the `dotkit.lists.taps` and `dotkit.lists.masApps` options (section 12.3) in `nix/`; the template gets `nix/taps.txt` and `nix/mas.txt` and lists them in its `nix/darwin.nix`, which drops the `homebrew.taps`/`homebrew.masApps` lines; today's planner writes the two files instead of filling the anchors (`plan.py:792-809`), with the report texts updated (`plan.py:361-386`); adopt appends to them instead of "add them by hand" (`adopt.py:88-93`, `97-110`); `dotkit add mas:ID` and `dotkit remove mas:ID` in `scripts/dotkit-pkg` (help text `dotkit-pkg:67`, completion `nix/completions/_dotkit`); README tables; `tests/e2e/migrated.sh`; `test_template_anchors` and `test_nix_files_parse` updated | changes the repository layout; `dotkit migrate` now adds apps and taps | 1 | goldens updated and reviewed; `nix flake check`; `nix build .#checks.aarch64-darwin.example`; a test evaluates the readers on fixture files (a custom tap with a URL; app names with spaces, `#`, and Unicode; a duplicate name) and gets the expected `homebrew.taps` and `homebrew.masApps` |
| 3 | **Context and the CLI seam.** `context.py` with the file and path helpers of section 2; every caller and test imports them from there; `main(argv, make_context=None)`; `tests/main_child.py` uses the seam instead of patching `cli.scan.Scanner` | no change | 1 | STD; the old helper names no longer exist; no test patches `scan.Scanner` |
| 4 | **Catalog.** `catalog.py` with `VersionManagerSpec` (incl. line-only specs and `default` readers replacing `scan.py:493-506`; `mise_replaces` replacing `plan.py:127`, `476`), `FrameworkSpec`, the pnpm and built-from-source rules; `npm_globs` for nvm, fnm, and asdf, relative to the home directory, replacing the hard-coded globs of the npm scan (`scan.py:268-277`; same patterns, order, labels, and version index; the mise glob stays in the scan, after them); `ZDOTDIR` unattributed (section 12.2); rcparse builds `_RULES` FRAMEWORK/VERSION_MANAGER, the `SKIP_VARS` entries, and `_PATH_VERSION_MANAGERS` from it; `Finding.source` | no change | 1 | STD; a corpus test checks that the generated regexes match the words and variables of the old literals (kept in the test), and nothing else |
| 5 | **Documents and transactions.** `docs.py` with every type of section 4.4 (`ListDoc` covers brews, casks, taps, and mas); `MiseDoc` settings API; `Base.mise_settings`; `_Build` writes through one `Txn` instead of `self.brews`, `self.mise_lines`, `self.plan`; its `render()` becomes document renders | no change, except three ruled cases that no golden reaches: a copy to a taken repository path is not made and gets a to-do (the old code let the last copy win, behind both links); a mise config with a secret forced to "layer" no longer suppresses tools; a tap name with whitespace is not written and gets the to-do | 2, 4 | STD; `tests/test_docs.py` (load/add/render, conflicts, add-time checks, `replace_setting` refusals and undo, commit/rollback) |
| 6 | **Writer and checks.** `writer.py` (render, plan-time guard, `write_new` with write-time verification) replaces `write.py`; `checks.py` (links, moved aside, blockers, summary); `mask_lines` moves to `writer.py`, which also masks the summary; `SecretInRepository` ends the run with a clean error and exit 2; a repository path inside a copied directory, or one that a copied directory would contain, counts as taken (rule 4) | no change on the goldens; two ruled cases that no golden reaches: a summary line with a secret is masked, and an overlapping directory copy is not made and gets the `other_config` to-do | 5 | STD; `write.py` is gone; a test that stages a secret past the guard aborts with no directory left; a summary line with a token is masked; `~/.config/bash` selected for adoption next to a carried `~/.bash_profile` is not copied, has no link, and gets the to-do, and a copied directory that would contain `config/zsh/zshrc` is refused the same way |
| 7 | **Pages, per-item choices, and the new UI.** `Page`/`Item`/`Choices` of section 4.3 (Homebrew as three pages; one shell item per file); the planner returns pages instead of `groups`/`file_items`/`adoptable`, and `_Build` reads `Choices.picked`/`options`; "Not carried, by your choice"; `ui/screens.py`, `ui/curses_ui.py`, `ui/plain.py` replace `wizard.py` and `prompts.py`; `ScriptedFrontend`; `wizard_child.py` writes the new `Choices` JSON; PTY tests rewritten for the new screens, plain-prompt counts taken from the planner | UI and Choices change; default-choice goldens unchanged | 6 | STD; `wizard.py` and `prompts.py` are gone; new goldens with deselected items; UI flow tests |
| 8 | **Migrators and the registry.** The `Migrator` base, `MIGRATORS`, `Session`. The first commit replaces `Planner` with `Session` and switches `ui/`, `__main__`, and the tests to it; the code that is not yet split runs as a private last entry of the registry. Then one migrator per commit, in `MIGRATORS` order: its scan step and its `Scan` fields, its `_Build` part, its pages, and its tests (into `tests/migrators/`) move together. rcparse parsing moves into the `shell` scan, for the page labels; `plan` parses again with the selection-aware chain (section 4.3). `Facts.get`, `Facts.base`, `Facts.check_syntax`, `needs`. One `Txn` per migrator (owner = its key), committed in `MIGRATORS` order, and a core `Txn` for the keymap, `nix/user.nix`, and the scan warnings; no rollback yet (step 11). The identity scan is core and runs first. `Session.scan` catches an exception per migrator and records `"<key>: <type>: <message>"` as a warning, until step 11 turns this into `Failure`. `adopt.py` (until step 12) reads the migrators' facts and imports `tap_lines`/`mas_lines` from `migrators/homebrew.py` and `migrators/appstore.py`. The whole-plan tests move to `tests/test_session.py` | no change, except on the failure path that no golden reaches: a scan warning now starts with the migrator key (`homebrew: …` instead of `brew: …`), warnings come in `MIGRATORS` order, and a migrator stops at its first failing part (cargo failing now also loses the Go tools of `built_tools`) | 7 | STD per commit; at the end `plan.py`, `scan.py`, the `Scan` dataclass, `_Build`, `migrators/_rest.py`, and the private registry entry are gone |
| 9 | **Runtimes rules.** The union rule for both idiomatic settings, written by `config_files` right after its `set_base`, and a to-do instead of an edit when your mise config is kept in place (section 12.1); runtime versions in report text and page note from `Base.mise` (replaces `plan.py:181`, `490-493`); `.tool-versions` in the hint; `VersionManagerSpec.env_var` and `catalog.manager_dir` (section 12.1) for detection and for the npm and Volta scans of `node_tools`, and `npm_globs` become relative to the manager's directory (one fnm location); the version-manager item says "config/mise.toml has these versions now" only for tools that `MiseDoc.add_tool` accepted, and for a tool that your mise config already has it says "config/mise.toml has <tool> from your mise config already, so these versions were not added; `mise use -g <tool>@<version>` for one you need" (today `plan.py:482-483` checks only that the page is on) | changes report text and possibly `config/mise.toml` | 8 | goldens updated and reviewed; tests: pyenv with `python` in your mise config gets the second text; your config without the keys writes nothing; a single-line key gets the union and parses; a multi-line or dotted key gives the to-do; base values come from the base file; a kept mise config with the key gets the to-do and is not edited; `NVM_DIR` pointing elsewhere finds nvm and its npm globals there, and an `NVM_DIR` that does not exist finds no nvm even if `~/.nvm` exists; `VOLTA_HOME` is followed |
| 9a | **Tools when your mise config is kept in place.** When `~/.config/mise/config.toml` stays in place ("keep", the only option when it has a secret), the repository's `config/mise.toml` is not linked, so mise never reads it. One pure helper, `config_files.mise_target(facts, choices)` ("repository", "kept", or `None` without a config of yours), tells the tool migrators (node_tools, python_tools, built_tools, runtimes) which case applies. When it is "kept", they add nothing to `config/mise.toml`: no carried row, no "Old copy" removal line, no "has these versions now". A selected tool that your kept config already has gets nothing (`config_files.user_mise_tools(facts)`; from step 12, `Declared.mise_key`). Every other selected tool is an item of one block in the "mise" section, with the intro "Your ~/.config/mise/config.toml stays in place, so mise does not read config/mise.toml of this repository. To get these tools from mise, add them to your config:" and items such as "`typescript` (nvm v20.11.0): `mise use -g npm:typescript`". A runtime with several versions gets the first (default) one, `mise use -g node@20`, and names the others ("also installed: 18"). The version-manager item keeps "With mise: …" only for tools that are not selected. The "mise needs cargo/go for these" line appears when a cargo or Go tool is carried or gets this to-do. This replaces the outcome of the step 5 case (a mise config with a secret, forced to "layer", whose tools went into the unread file) | changes report text and `config/mise.toml` when your config is kept | 9 | goldens unchanged (no fixture keeps a mise config); tests: a kept config with npm, pipx, cargo (without cargo installed), and node 20 and 18 selected leaves `config/mise.toml` as the template and gives the block, `mise use -g node@20` with "also installed: 18", and the cargo line; a tool that the kept config has gets nothing; `test_a_mise_config_that_is_not_carried_does_not_hide_tools` is rewritten to this behavior (still no token anywhere); a layered config and no config at all behave as before |
| 10 | **Registry invariants.** Unique page keys and item-id prefixes; `sections` ⊆ `SECTIONS` (`ORDER` becomes `SECTIONS`); the empty home; the AST no-write test; the secret property test; every static section intro and rcparse hint passes `secrets.line_secret(...) is None` | no change | 8 | the tests pass; a migrator that writes a file, or copies a token file, fails them; an intro with "password manager" in it fails |
| 11 | **Crash isolation.** One transaction per migrator; isolated `scan` and `pages`; `Failure`; the identity pseudo-migrator; `manual_steps` for every migrator (section 7.5 texts for `shell` and `config_files`); "Problems during the migration"; summary and stderr lines; `--debug`; masking; strict mode (`fake.session` default, `DOTKIT_MIGRATE_STRICT=1` in the e2e job, hidden `--strict`); exit code 0 with failures; in the UI, the warning line and the review Problems block (section 8) | the scan catch-all (`scan.py:648-651`, by then inside the migrators) becomes isolation; plan crashes are newly isolated | 10 | `tests/test_isolation.py`; UI flow tests for the warning line and the Problems block; goldens unchanged (no faults in the fixtures) |
| 12 | **Adopt unification.** `Declared` (section 4.5); `adopt()` for homebrew, appstore, node_tools, python_tools, built_tools; `Session.adopt`; `writer.apply_edits`; adopt output from the documents' `added()` and the report; `adopt.py` removed; the pnpm rule applies to npm packages only (today `adopt.py` skips a package named `pnpm` from any backend). Setup now also skips a formula that your mise config provides | de-duplication change | 11 | goldens updated and reviewed; `adopt.py` is gone; tests: your mise config has `jq` and brew has `jq` → skipped in setup and adopt; apply twice → the second run adds nothing; a fault in the second replace restores the first file |
| 13 | **Leftovers report** (section 11). `Package.commands`; executable listings in the `shell` and `leftovers` scans; `base.TOOL_PATH` checked against `nix/home.nix`; report-only parsing of `~/.bashrc`, `~/.zlogin`, `~/.zlogout`, `~/.profile`; the shadowing warnings with their context and fixes; the Homebrew stale/cleanup warning, including deselected items; the reference check next to every removal | new report text | 8 | goldens updated and reviewed; fixtures: `~/.npm-global/bin` with `tsc` carried from `~/.zshrc` while `npm:typescript` moves to mise → "interactive shells" warning with file and line; `~/.local/bin/ruff` while `pipx:ruff` moves → "GUI apps" warning; `source ~/.nvm/nvm.sh` in `~/.bashrc` → "still runs"; a carried line that re-adds `/opt/homebrew/bin` → no warning |
| 14 | **Package docstring.** `dotkit_migrate/__init__.py` points to this file and keeps the Python 3.9 rules; README "Work on dotkit" mentions it | no change | all | review |

Order: 1 → 1a → 2 → 3 and 4 (in either order) → 5 → 6 → 7 → 8. After 8: 9 (then
9a), 10, and 13 in any order; 11 needs 10; 12 needs 11. Land each step that changes
behavior (1a, 2, 7, 9, 9a, 11, 12, 13) on its own, so that each golden diff can be
reviewed by itself.

## 14. Open items

None block the work.

Two things were not verified and limit what the report may claim in step 13:
how mise's PATH handling works in nested shells, and its order relative to
direnv's hook. If someone checks them later, extend section 11 and the step
13 fixtures.
