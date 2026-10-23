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
  the `Txn` they get), `declared`, `rcparse`, `secrets`, `base`, and their
  own helpers. They never import `ui/`, `writer`, or `curses`, and never
  write a file (section 6).
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
Session to `ShellNixDoc`), `nix/user.nix`, the identity scan (section 4.2),
`checks.py`, and the writer.

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
        template, base files, and files that the scan found."""

    def adopt(self, facts: Facts, declared: Declared, txn: Txn, opts: AdoptOptions) -> None:
        """`dotkit migrate`: add what the repository lacks. Default: nothing."""

    def manual_steps(self, ctx: Context) -> str:
        """What a person does by hand when this migrator failed. Must describe
        what actually happens without it (plain-template behavior)."""
```

A migrator's `scan` sees only the `Context`. Other migrators' facts are read
in `pages`, `plan`, and `adopt`, when every scan has finished. So scan order
never matters.

### 4.2 Facts and Identity

```python
@dataclass
class Identity:                          # core scan, today's scan_identity (scan.py:628-635)
    home: str; user: str; hostname: str = ""; login_shell: str = ""
    git_name: str = ""; git_email: str = ""

class Facts:
    identity: Identity
    warnings: List[str]                  # expected problems, from every migrator
    def get(self, key: str) -> Optional[object]   # None if that scan failed or was skipped
```

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

A migrator reports every deselected item under "Not carried, by your choice":
it stays installed or in place, dotkit does not manage it, and with
`homebrew.onActivation.cleanup = "uninstall"` activation would uninstall it.

### 4.4 Documents and transactions

Migrators change the output only through documents. A document is one output
file. It is created on first use from base text: the template file in setup,
or the repository's current file in adopt. It knows how to merge, how to
check an entry, and how to render.

```python
class Docs:
    def __init__(self, mode: str, template: Path, repo: Optional[Path]): ...   # mode: "setup" | "adopt"
    def begin(self, owner: str) -> "Txn": ...      # asserts that no other transaction is open

class Txn:
    # The same accessors as Docs. Each returns a view on the committed document.
    def list(self, path: str) -> ListView: ...
    def mise(self) -> MiseView: ...
    def links(self) -> LinksView: ...              # setup only
    def shell_rc(self, main: str) -> ShellRcView: ...   # setup only; main in base.SHELL_LAYERS
    def file(self, path: str) -> FileView: ...     # setup only
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
| `Copies` | files and directories copied into the repository | `add(rel, src) -> bool` | `src` absolute and inside the home directory; `rel` relative | no |
| `ReportDoc` | `MIGRATION.md`, the summary | `carried(what, where)`, `todo(section, intro, items)`, `blocker(msg)` | `section` in `report.SECTIONS` | yes (adopt output) |

`MiseDoc` details:

- `set_base(text)` is used by `config_files` when your mise config is carried
  as "layer": your file replaces the template text (today's `_merge_mise`,
  `plan.py:1014-1017`). Keys in it count as existing text.
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
```

`Declared` contains:

- **always:** the base lists and the base mise tools, plus the tools of your
  `~/.config/mise/config.toml`, whether it is carried as "layer" or kept in
  place (in both cases mise still reads it);
- **`for_repo` only:** the repository's `nix/packages.txt`, `nix/brews.txt`,
  `nix/casks.txt`, `nix/taps.txt`, `nix/mas.txt` and `config/mise.toml`, plus
  the `taps` and `masApps` that `dotkit` evaluates from the configuration
  (`dotkit:308-310`).

A Homebrew formula is skipped when `brew()` returns something. The report
says what provides it and how to remove the Homebrew copy. This replaces
`Planner.brew_split` (`plan.py:110-118`) and `adopt.missing`
(`adopt.py:31-74`).

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

- `render(docs) -> Plan`. If `local` is not empty, `.gitignore` gets the
  `/local/` block (`plan.py:839-841`).
- `guard(plan)`: the plan-time secret guard (section 6).
- `write_new(plan, template)` for setup:
  1. copy the template into a temp directory next to the repository;
  2. add the copies, the files, and `local/`;
  3. write `.build/links-in-way`;
  4. verify the staged tree (section 6);
  5. `git init`;
  6. rename the directory to the repository path.

  If anything fails, the temp directory is removed (today's `write.py`).
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
15. Homebrew packages that are not in the lists
16. Not carried, by your choice
17. Homebrew taps
18. App Store apps
19. Other config
20. Other config that stays out of the repository
21. Scan warnings

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
3. **The plan-time guard (core, in `writer.guard`).** Today's `guard_copies`
   and `guard_files` run over every tracked rendered file and every copy
   source. A shell file is split by statement, never in the middle of one.
   Other files are masked line by line. Then the rendered report is masked
   line by line. The guard changes the plan and reports what it did, so
   dry-run and the review screen show its result.
4. **Write-time verification (core, in `writer`).** Before the rename or
   `os.replace`, scan the staged tracked files (copies included; `local/` and
   `.git` excluded). It changes nothing. A find aborts the write, and nothing
   is left behind. It catches what slipped past the guard, including a file
   that changed between the scan and the copy.
5. **Failure text.** `Failure.error` and the `--debug` traceback pass through
   `secrets.line_secret` line by line. A line with a secret becomes "(this
   line has a secret; not shown)".

Rules that keep this enforceable:

- **Migrators cannot write files.** An AST test over `migrators/` fails on
  `open(…)` with a write or append mode, `.write_text`, `.write_bytes`,
  `shutil.copy*`, `shutil.copytree`, `os.replace`, `os.rename`, `os.remove`,
  `os.unlink`, and `subprocess`. Commands run only through `ctx.runner`
  (read-only by contract).
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
intro, rows, get, set, detail)` from the Session. The curses and plain
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
(`1 3-5`), `a` (all), or `n` (none). A choices page asks per item, as
today's `prompts.py:54-65`. `--plain` and an unusable terminal
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
- The review shows "Not carried, by your choice: N" per page.
- **`--yes` and `--dry-run`** have no screens. Defaults come from
  `Choices.defaults`, and problems go to stderr and to the printed summary.

## 9. Testing

| Kind | Where | What it proves |
|---|---|---|
| Migrator unit | `tests/migrators/test_<key>.py` | `scan` on `fake.Home` + `FakeRunner` gives the expected facts; `pages` gives the expected items and defaults; `plan` on an in-memory `Docs` gives the expected entries and report rows; `adopt` against a `Declared` |
| Document | `tests/test_docs.py` | per type: load, add, render; conflicts; `add()` checks; `render()` never raises on accepted entries; `MiseDoc.replace_setting` refusals and the parse-compare undo; `commit()`/`rollback()`; a second `begin()` asserts |
| Writer and checks | `tests/test_writer.py`, `tests/test_checks.py` | guard behavior (today's `test_guard`, `test_final_guard_keeps_secret_shell_statements_whole`); write-time verification aborts with nothing left; atomic adopt with a fault in the second replace; links in the way (today's tests) |
| Golden plans | `tests/test_golden.py`, `tests/golden/*.json` | the full `Plan` (all fields, home as `~`, fixed date and hostname, `check_syntax` passed in) for an empty Mac, `used_mac` (`tests/test_plan.py:27-56`), and a kitchen-sink Mac; the adopt output and the edited files. `DOTKIT_MIGRATE_REGOLD=1` rewrites them. A golden diff is how every behavior change is reviewed |
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
    mise_replaces=True, words=("nvm", "NVM_DIR"), extra_vars=("NVM_DIR",), path_marker=".nvm",
    npm_globs=(("versions/node/*/lib/node_modules", -3),),
)
# Line-only specs (detect=False) keep rcparse's words for rvm, nodenv, goenv, chruby, tfenv, conda,
# mamba, micromamba (rcparse.py:91-93).

# migrators/runtimes.py
IDIOMATIC = ("idiomatic_version_file_enable_tools", "idiomatic_version_file_disable_files")

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
                     note="off: mise installs them; the base has " + base_runtimes(ctx.base))]

    def plan(self, f, c, txn):
        user = config_files.layered_mise(f, c)                          # your config, if carried as "layer"
        for tool, versions in runtime_lines(facts_of(f)).items():
            if c.selected("runtimes", f"runtime:{tool}"):
                if txn.mise().add_tool(tool, versions if len(versions) > 1 else versions[0]):
                    ...                                                 # carried row
        # Both idiomatic settings are arrays; your value replaces dotkit's completely.
        for key in IDIOMATIC:
            if user is not None and key in user.settings:
                base_val = ctx.base.mise_settings.get(key, [])
                union = dedup(list(base_val) + list(user.settings[key]))   # dotkit's order first, then yours
                if union != user.settings[key] and not txn.mise().replace_setting(
                        key, union, expect=user.settings[key]):
                    txn.report.todo("mise", "", [f"config/mise.toml sets `{key}`, which replaces dotkit's "
                                                 f"{base_val}. Set it to {union}."])
        # Never write these keys when you do not set them: a copy would freeze dotkit's list.
        for vm in facts_of(f).managers:                                 # today's plan.py:468-493
            lines = [x for x in shell.findings(f) if x.source == vm.name]   # None-safe helper
            txn.report.todo("Version managers", intro(ctx.base), [step_text(vm, lines, c)])
```

- **`Base.mise_settings`** reads `[settings]` of the base `config/mise.toml`,
  as `Base.mise` reads `[tools]` (`base.py:123-130`). Nothing hard-codes the
  base value. Today the base enables `["node", "python", "ruby", "rust"]` and
  disables `["ruby:Gemfile", "node:package.json"]`.
- **No hard-coded runtime versions in text.** The intro's "The base has Node
  24, Python 3.14, and Ruby 3.4" (`plan.py:490-493`) and the page note
  (`plan.py:181`) come from `Base.mise` (`base_runtimes()`), so a base
  change can't make them wrong.
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
ignored. `nix/taps.txt` format: `<owner/repo>` or `<owner/repo> <clone URL>`,
with the comments of `nix/read-list.nix`.

## 13. Refactor steps

The base fix for mise's idiomatic version files is already done
(`config/mise.toml`), and the existing project-file hints are correct.

Each step is one commit, or a short series of commits, and must pass STD.
"No behavior change" means the goldens from step 1 stay byte-identical and
the PTY key sequences stay unchanged. A step that changes behavior updates
the goldens on purpose, and the diff is reviewed.

| # | Step | Behavior | Needs | Done when |
|---|---|---|---|---|
| 1 | **Golden plans.** `tests/test_golden.py` and `tests/golden/`: the empty Mac, `used_mac`, and a kitchen-sink Mac (nvm, pyenv, asdf, sdkman; Oh My Zsh; cargo, go, uv tools; a Stow-linked `~/.config/ghostty`; a secret in a function; vi keymap; every group on). Serialize the whole `Plan`, normalized. Also goldens for the adopt output and the files it edits | no change | — | STD; changing one report string in `plan.py` fails a golden (check once by hand); `/tmp/dk-before.txt` taken |
| 2 | **Context and the CLI seam.** `context.py` with the file and path helpers (re-exported from the old names); `main(argv, make_context=None)`; `tests/main_child.py` uses the seam instead of patching `cli.scan.Scanner` | no change | 1 | STD; no test patches `scan.Scanner` |
| 3 | **Catalog.** `catalog.py` with `VersionManagerSpec` (incl. line-only specs and `default` readers replacing `scan.py:493-506`; `mise_replaces` replacing `plan.py:127`, `476`), `FrameworkSpec`, the pnpm and built-from-source rules; rcparse builds `_RULES` FRAMEWORK/VERSION_MANAGER, the `SKIP_VARS` entries, and `_PATH_VERSION_MANAGERS` from it; `Finding.source` | no change | 1 | STD; a corpus test checks that the generated regexes match the words and variables of the old literals (kept in the test), and nothing else |
| 4 | **Documents and transactions.** `docs.py` with every type of section 4.4 (and a transitional `DarwinDoc` for the `homebrew.taps`/`homebrew.masApps` anchors, `plan.py:792-809`, removed in step 12); `MiseDoc` settings API; `Base.mise_settings`; `_Build` writes through one `Txn` instead of `self.brews`, `self.mise_lines`, `self.plan`; `render()` becomes document renders | no change | 3 | STD; `tests/test_docs.py` (load/add/render, conflicts, add-time checks, `replace_setting` refusals and undo, commit/rollback) |
| 5 | **Writer and checks.** `writer.py` (render, plan-time guard, `write_new` with write-time verification); `checks.py` (links, moved aside, blockers, summary) | no change (the verification never fires on the goldens) | 4 | STD; a test that stages a secret past the guard aborts with no directory left |
| 6 | **Migrators and the registry.** The `Migrator` base, `MIGRATORS`, `Session` (replaces `Planner`; the wizard, prompts, and `__main__` use it). Move one migrator per commit, in `MIGRATORS` order: its scan step, its `_Build` part, its pages, its tests (into `tests/migrators/`). rcparse parsing moves into the `shell` scan. `Facts.get`, `needs`. `Scan` keeps compatibility properties until `test_scan.py` is ported, then they go | no change (scan warnings now come in `MIGRATORS` order; only visible when several steps fail) | 5 | STD per commit; at the end `plan.py`, `scan.Scanner`, and `_Build` are gone |
| 7 | **Runtimes rules.** The union rule for both idiomatic settings (section 12.1); runtime versions in report text and page note from `Base.mise` (replaces `plan.py:181`, `490-493`); `.tool-versions` in the hint | changes report text and possibly `config/mise.toml` | 6 | goldens updated and reviewed; tests: your config without the keys writes nothing; a single-line key gets the union and parses; a multi-line or dotted key gives the to-do; base values come from the base file |
| 8 | **Registry invariants.** Unique page keys and item-id prefixes; `sections` ⊆ `SECTIONS` (turn `ORDER` into `SECTIONS`); the empty home; the AST no-write test; the secret property test | no change | 6 | the tests pass; a migrator that writes a file, or copies a token file, fails them |
| 9 | **Crash isolation.** One transaction per migrator; isolated `scan` and `pages`; `Failure`; the identity pseudo-migrator; `manual_steps` for every migrator (section 7.5 texts for `shell` and `config_files`); "Problems during the migration"; summary and stderr lines; `--debug`; masking; strict mode (`fake.session` default, `DOTKIT_MIGRATE_STRICT=1` in the e2e job, hidden `--strict`); exit code 0 with failures | the scan catch-all (`scan.py:648-651`) becomes isolation; plan crashes are newly isolated | 6, 8 | `tests/test_isolation.py`; goldens unchanged (no faults in the fixtures) |
| 10 | **Adopt unification.** `Declared` (section 4.5); `adopt()` for homebrew, node_tools, python_tools, built_tools; `Session.adopt`; `writer.apply_edits`; adopt output from the documents' `added()` and the report. Setup now also skips a formula that your mise config provides | de-duplication change | 9 | goldens updated and reviewed; tests: your mise config has `jq` and brew has `jq` → skipped in setup and adopt; apply twice → the second run adds nothing; a fault in the second replace restores the first file |
| 11 | **Pages and per-item choices.** `Page`/`Item`/`Choices` of section 4.3 (Homebrew as three pages; one shell item per file); `ui/screens.py`, `ui/curses_ui.py`, `ui/plain.py`; `ScriptedFrontend`; the warning line and the review Problems block; "Not carried, by your choice"; `wizard_child.py` writes the new `Choices` JSON; PTY tests updated once, plain-prompt counts taken from the Session; `wizard.py`/`prompts.py` removed | UI and Choices change; default-choice goldens unchanged | 9 | STD; new goldens with deselected items; UI flow tests |
| 12 | **mas and taps as data files.** A nix reader for `nix/mas.txt` (`id name`, section 12.3) and `nix/taps.txt`; `dotkit.lists.masApps` and `dotkit.lists.taps` options (default `[ ]`) merged into `homebrew.masApps` and `homebrew.taps`; the template gets empty `nix/mas.txt` and `nix/taps.txt` and the list entries in `nix/darwin.nix` (keep its `homebrew.taps = [ ];` and `homebrew.masApps = { };` for hand entries); `appstore` and `homebrew` write `ListDoc`s; `DarwinDoc` removed; adopt applies apps and taps; `dotkit add mas:ID` in `scripts/dotkit-pkg` (and its help text, `dotkit-pkg:67`); README | new files; `dotkit migrate` now applies apps and taps | 10 | `nix flake check`; `nix build .#checks.aarch64-darwin.example`; a fixture with a custom tap and an app name with spaces, `#`, and Unicode evaluates to the same `homebrew.masApps`/`taps` as today's anchors; `migrated.sh` checks `nix/mas.txt` when the runner has apps |
| 13 | **Leftovers report** (section 11). `Package.commands`; executable listings in the `shell` and `leftovers` scans; `base.TOOL_PATH` checked against `nix/home.nix`; report-only parsing of `~/.bashrc`, `~/.zlogin`, `~/.zlogout`, `~/.profile`; the shadowing warnings with their context and fixes; the Homebrew stale/cleanup warning; the reference check next to every removal | new report text | 6, 11 | goldens updated and reviewed; fixtures: `~/.npm-global/bin` with `tsc` carried from `~/.zshrc` while `npm:typescript` moves to mise → "interactive shells" warning with file and line; `~/.local/bin/ruff` while `pipx:ruff` moves → "GUI apps" warning; `source ~/.nvm/nvm.sh` in `~/.bashrc` → "still runs"; a carried line that re-adds `/opt/homebrew/bin` → no warning |
| 14 | **Package docstring.** `dotkit_migrate/__init__.py` points to this file and keeps the Python 3.9 rules; README "Work on dotkit" mentions it | no change | all | review |

Order: 1 → 2 and 3 (in either order) → 4 → 5 → 6. After 6: 7, 8, and 13
(13 also needs 11); 9 needs 8; 10 needs 9; 11 needs 9; 12 needs 10. Land 7,
9, 10, 11, 12, and 13 one at a time, so that each golden diff can be
reviewed on its own.

## 14. Open items

None block the work.

Two things were not verified and limit what the report may claim in step 13:
how mise's PATH handling works in nested shells, and its order relative to
direnv's hook. If someone checks them later, extend section 11 and the step
13 fixtures.
