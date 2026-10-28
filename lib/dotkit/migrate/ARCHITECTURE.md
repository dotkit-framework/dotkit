# Architecture of the migration wizard

This is the reference for the migration wizard in `lib/dotkit/migrate/`:
the design, the contracts between its parts, and the rules for secrets,
writes, and errors. Section 9 says how to add a migrator.

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

- **Python and dependencies:** the standard library and tomli of
  `dotkit/_vendor`, on the macOS Python of the Command Line Tools (3.9). It runs as `/usr/bin/python3 -I -B`
  (`dotkit-migrate`, `run.py`). No `match`, no `dataclass(slots=…, kw_only=…)`,
  no `zip(strict=…)`, no `X | Y` outside annotations (use `Optional`/`Union`
  at runtime), no `ExceptionGroup`. `tomllib` comes from `_compat` (vendored
  tomli on 3.9/3.10). `tests/test_start.py` checks the grammar.
- **Writes:** the wizard reads the Mac and writes only the new repository
  (setup) or files inside the existing repository (adopt). The one exception
  is `~/.secrets`: setup appends the variables of the secrets that it moves
  there, and never changes a line that the file has (section 5). It never
  installs, uninstalls, or deletes anything on the Mac. Removals are report
  text only. The one mode that changes the Mac is `dotkit tidy`
  (`TIDY.md`), and only through `tidy/actions.py`.
- **Secrets:** a detected secret never goes into a file that Git tracks, and
  the report never shows one. A secret that the wizard can move goes to
  `~/.secrets` as a variable, and the repository file refers to the variable.
- **Atomic writes:** setup creates the repository completely or not at all.
  Adopt changes all its files or none.
- **Dry run:** `--dry-run` shows exactly the plan that a real run would write.
- **Partial failure:** a bug in one migrator does not stop the migration
  (section 6).
- **No compatibility with earlier dotkit versions:** the new wizard,
  `dotkit migrate`, the template, and the nix modules work only with
  repositories in the layout described here. Nothing reads or converts older
  formats, for example `homebrew.taps` and `homebrew.masApps` entries that an
  earlier wizard wrote into `nix/darwin.nix`.

## 2. Layers and package layout

```
migrate/
  dotkit-migrate                   start script: runs lib/run.py dotkit-migrate
lib/
  run.py                           start file of the dotkit programs
  dotkit/migrate/
    ARCHITECTURE.md                this file; TIDY.md has the design of dotkit tidy
    __main__.py                    CLI only: arguments, mode, exit codes, stderr lines
    context.py                     Context: home, env, runner, base, applications, brew_prefix;
                                   read-only file helpers; tilde/expand/repo_error
    catalog.py                     DATA: version managers, frameworks, tool rules, app config places
    model.py                       Item, Page, Choices, Facts, Identity, Failure, Plan
    docs.py                        Docs, Txn, and the document types
    declared.py                    Declared: what the base, the repository, and your mise config provide
    registry.py                    MIGRATORS (ordered tuple) and Session
    writer.py                      render, secret guard, write_new, apply_edits
    checks.py                      links in the way, files moved aside, blockers, summary
    report.py                      ReportDoc rendering, SECTIONS
    migrators/
      __init__.py                  the Migrator base class and shared report helpers
      tools.py                     what node_tools, python_tools, and built_tools share
      homebrew.py  appstore.py  node_tools.py  python_tools.py  built_tools.py
      runtimes.py  shell.py  frameworks.py  config_files.py  other_config.py
      logins.py  leftovers.py
    tidy/                          dotkit tidy (TIDY.md): apps.py and clis.py (the scans) and plan.py
                                   follow the rules of migrators/; actions.py changes the Mac; cli.py
                                   runs the mode
    ui/
      screens.py                   Screen specs from the Session's pages; no curses
      curses_ui.py                 widgets and the screen loop
      plain.py                     the same screens as line prompts
    rcparse.py  secrets.py  proc.py  base.py  _compat.py  _vendor/
    secrets_file.py                ~/.secrets: read its variables, quote export lines
    lift.py                        the secrets of ~/.npmrc and Git config files as variables
  tests/                           unit, migrator, document, golden, invariant, UI and PTY tests
```

The paths below are in `lib/dotkit/migrate/` (code) and `lib/tests/` (tests).

Rules:

- `migrators/` import only `context`, `catalog`, `model`, `docs` (the views
  of the `Txn` they get, and the pure helpers of `docs.py` such as
  `nix_string`, `toml_value`, and the entry checks; never `Docs` itself or
  its core methods), `declared`, `rcparse`, `secrets`, `secrets_file`, `lift`, `base`, `_compat`
  (for `tomllib`), `report` (only the text helper `plural`), and their own
  helpers. They never import `ui/`, `writer`, `proc`, `subprocess`, or
  `curses`, and never write a file (section 5). Constants that a scan
  passes to the runner, such as `BREW_ENV`, are data in `catalog.py`.
- `ui/` imports only `model`, `registry` (the Session), and the path helpers
  from `context`.
- Only `writer.py` writes to disk. The one exception is `__main__.py`, which
  writes the `--result` file that `scripts/setup` asks for (the path of the
  new repository, to a file that `scripts/setup` made with `mktemp`).
- `MIGRATORS` is the single source of order: scan order, page order, plan
  order, and the order of the "Carried over" rows.

```python
MIGRATORS = (
    homebrew, appstore, node_tools, python_tools, built_tools, runtimes,
    shell, frameworks, config_files, other_config, logins, leftovers,
)
```

Core, not migrators: the repository path, the zsh keymap (written by the
Session to `ShellNixDoc`), `nix/user.nix`, the identity scan (section 3.2)
and the "Login shell" to-do, which reads only the identity, the scan
warnings, `checks.py`, and the writer.

## 3. Contracts

### 3.1 Migrator

```python
class Migrator:
    key: str = ""                        # stable id: facts key, Txn owner, test name, Failure.migrator
    title: str = ""                      # human name for problems and warnings
    needs: Tuple[str, ...] = ()          # facts it cannot work without (section 6.3)
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

    def mise_commands(self, facts: Facts) -> Dict[str, Tuple[str, ...]]:
        """The commands of each mise key that this migrator can add, from its facts:
        {"npm:typescript": ("tsc", "tsserver")}. The Session merges them into
        Facts.mise_commands after the scans, for the leftovers report. It reads only
        its own facts (and facts.base), never another migrator's, so the order of
        the hooks does not matter. It is part of the scan phase: if it raises, the
        migrator fails "while scanning". Default: none."""
        return {}

    def manual_steps(self, ctx: Context) -> str:
        """What a person does by hand when this migrator failed. Must describe
        what actually happens without it (plain-template behavior)."""
```

A migrator's `scan` sees only the `Context`. Other migrators' facts are read
in `pages`, `plan`, and `adopt`, when every scan has finished. So scan order
never matters. `pages`, `plan`, and `adopt` never get the `Context`: it has
the runner and the environment, which belong to the scan. What they need
from outside comes through `Facts` (section 3.2).

Pure helpers of one migrator that others call take facts and choices, for
example `config_files.mise_target(facts, choices)`,
`config_files.mise_dest(facts, choices)`,
`config_files.merged_sources(facts, choices)` (the files merged into one
repository file), `other_config.adopted(facts, choices, rel)`, and
`shell.carried_paths(facts, choices)`. They must handle missing facts
(`None`).

### 3.2 Facts and Identity

```python
@dataclass
class Identity:                          # the core scan: registry.IdentityScan
    home: str; user: str; hostname: str = ""; login_shell: str = ""
    git_name: str = ""; git_email: str = ""

class Facts:
    identity: Identity
    warnings: List[str]                  # expected problems, from every migrator
    base: Base                           # set by the Session: the dotkit base (read-only data)
    check_syntax: Optional[Callable[[str, str], bool]]   # set by the Session; None: no check
    mise_commands: Dict[str, Tuple[str, ...]]            # set by the Session from Migrator.mise_commands
    def get(self, key: str) -> Optional[object]   # None if that scan failed or was skipped
    def without(self, keys) -> "Facts"            # the same facts without these migrators and their warnings
```

`base` and `check_syntax` are the only things from outside the facts that
`pages`, `plan`, and `adopt` use. `check_syntax(shell, text)` runs `zsh -n` or
`bash -n` in production and is a fake in the tests.

Each migrator module has a typed accessor, for example
`def facts_of(f: Facts) -> Optional[BrewFacts]`. A reader must handle `None`.

The identity scan is isolated like a migrator. If it fails, the defaults are
`$USER` and an empty hostname. An empty user name is a blocker, because
`nix/user.nix` needs it.

### 3.3 Pages, items, choices

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
    option_text: Dict[str, str] = field(default_factory=dict)   # from config_files.OPTION_TEXT

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
| config_files | `files` | choices | `file:<target>` | per item (`ConfigFiles.pages`) |
| other_config | `other` | checklist | `other:<rel>` | off |

The shell page has one item per file, not one per statement. A carried
statement can depend on an earlier one (variables that `rcparse.Parser`
tracks), so leaving out a single statement could break later ones.

Items that cannot be carried (redundant Homebrew copies, `ok=False`
packages, pnpm, and tools that dotkit has already, `Declared.tool`) are not
items. They go to the report. A file with a secret gets a restricted option
list (`keep`, `dotkit`; `.gitconfig` or your mise config with a secret:
`keep` only), unless `lift.py` can move all its secrets to `~/.secrets`:
`~/.npmrc` then has `tokens` first (your file, with a variable for each
token), and a Git config file keeps its usual options without the settings
that have a secret. `~/.npmrc` and `~/.config/uv/uv.toml` are
`base.OWN_FILES`: their tools read them after the dotkit settings, which
the base puts elsewhere (the global config of npm, `/etc/uv/uv.toml`), so
their choices are `adopt` (or `tokens`) and `keep`, never `dotkit`.

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
never their values). For Homebrew formulae and casks, the intro
(`migrators.NOT_CARRIED_BREW`) adds that `dotkit upgrade brew` upgrades
only the packages in the lists, so these go stale, and that with
`homebrew.onActivation.cleanup = "uninstall"` activation would uninstall
them. An item that is off by default (cargo, Go, runtimes, other
config) and that stays off keeps the to-do of its kind, which already says
why it is off.

A shell file that has only lines with secrets is an item too, on by
default: its assignments go to `~/.secrets` (section 5).

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

### 3.4 Documents and transactions

Migrators change the output only through documents. A document is one output
file. It is created on first use from base text: the template file in setup,
or the repository's current file in adopt. It knows how to merge, how to
check an entry, and how to render.

```python
class Docs:
    def __init__(self, mode: str, template: Path, repo: Path,
                 home: str, host: str, strict: bool,
                 secrets: Dict[str, str]): ...   # mode: "setup" | "adopt"; secrets: what ~/.secrets assigns
    def begin(self, owner: str) -> "Txn": ...      # asserts that no other transaction is open
    # Core only, never on a Txn:
    def drop(self, rel: str) -> None: ...          # the copy guard: remove a copy, its file, and the links to it
    def render(self) -> Tuple[Dict[str, str], str]: ...   # (tracked files, lines for ~/.secrets) as text;
                                                   # writer.render wraps it
    def secret_names(self) -> List[str]: ...       # the names that the lines for ~/.secrets add
    conflicts: List[Conflict]                      # reported under "Problems during the migration"

class Txn:
    # The same accessors as Docs. Each returns a view on the committed document.
    def list(self, path: str) -> ListView: ...
    def mise(self) -> MiseView: ...
    def links(self) -> LinksView: ...              # setup only
    def shell_rc(self, main: str) -> ShellRcView: ...   # setup only; main in base.SHELL_LAYERS
    def file(self, path: str) -> FileView: ...     # setup only
    def shell_nix(self) -> ShellNixView: ...       # setup only; owner "core" only (the keymap)
    def secrets(self) -> SecretsView: ...          # setup only; ~/.secrets, outside the repository
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
   `nix/home.nix`, `nix/shell.nix`, `nix/user.nix`, the shell files), or by a whole file (`FileDoc`) that has text.
   `Copies.add` on a taken path makes no copy and returns `False`, with no
   conflict: the migrator handles it (for example `other_config` keeps the
   file in place and adds a to-do). A whole file set on a path that a copy
   takes is a conflict. Paths overlap too: a path is taken when
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
| `ListDoc` | `nix/brews.txt`, `nix/casks.txt`, `nix/taps.txt`, `nix/mas.txt` | `add(entry, comment="") -> bool`, `names()`, `added()`; the view also has `flagged(entry)` (section 3.5) | brews/casks: one word without `#`; taps: `owner/repo` and an optional URL without spaces; mas: digits, a space, then a non-empty name | yes: appends after the existing text, with one comment per run: `# From Homebrew on <host>, see MIGRATION.md.` (`# From the App Store on <host>, ...` for `nix/mas.txt`) in setup, `# Added by dotkit migrate on <host>.` in adopt, which writes no MIGRATION.md |
| `MiseDoc` | `config/mise.toml` | `add_tool(key, value, comment="") -> bool`, `has_tool(key)`, `set_base(text)`, `set_setting(key, value) -> bool`, `replace_setting(key, value, expect) -> bool`, `added()`; the view also has `flagged(key, value)` | key: non-empty, no newline; value: str, list of str, or a flat table of str/int/bool | yes (`add_tool` only) |
| `LinksDoc` | `nix/home.nix` `dotkit.links` (after the anchor `docs.LINKS_ANCHOR`, `    ".zshrc.local" = "config/zsh/zshrc";\n`) | `link(target, source_or_None) -> bool` | target relative to home, no `..`; source a repository path or None | no |
| `ShellRcDoc` | `config/zsh/zshenv`, `config/zsh/zprofile`, `config/zsh/zshrc`, `config/bash/bash_profile` | `add_part(header, text)`, `ensure()` (write the file even when empty) | main in `SHELL_LAYERS` | no |
| `ShellNixDoc` | `nix/shell.nix` | `set_keymap(value)`, written by the Session | `emacs` or `vi` | no |
| `SecretsDoc` | `~/.secrets`, outside the repository: the lines that the writer appends | `export(name, value, line, source) -> bool`, `free_name(base, value)`, `taken(name)` | `name` a shell variable name; `line` one line | no |
| `FileDoc` | a whole file with one owner: `nix/user.nix`, a merged `config/gitconfig` | `set(text)` | the path is not owned by another document | no |
| `Copies` | files and directories copied into the repository | `add(rel, src) -> bool`, `False` when `rel` is taken (rule 4); `taken(rel) -> Optional[Tuple[str, str]]`, `(relation, path)` (rule 4) | `src` absolute and inside the home directory; `rel` relative | no |
| `ReportDoc` | `MIGRATION.md`, the summary | `carried(what, where)`, `todo(section, intro, items)`, `blocker(msg)` | `section` in `report.SECTIONS` | yes (adopt output) |

`MiseDoc` details:

- `set_base(text)` is used by `config_files` when your mise config is carried
  as "layer": your file replaces the template text. Keys in it count as
  existing text.
- **The order of `set_base` and `add_tool` does not matter.** `config_files`
  comes after the tool migrators in `MIGRATORS`, so `set_base` usually runs
  last. The tool migrators therefore never rely on the document to skip the
  tools of your mise config. They ask a pure helper:
  `Declared.yours_mise_key`. `MiseDoc` keeps tool entries apart from the
  base text and renders them into the final base. If `set_base` brings a
  key that a committed entry already has, that is a conflict (strict mode
  raises).
- **Settings come after `set_base`.** Unlike tool entries, setting edits
  work on the base text, so `set_base` raises once a setting was edited
  (`test_settings_come_after_set_base`). Only `config_files`, which calls
  `set_base`, edits the settings that come from your config, right after
  it.
- `add_tool` inserts at the end of `[tools]`, or creates `[tools]`
  (`docs.insert_tools`). Render checks that the result parses with
  `tomllib`. If it does not, `Docs.render` falls back to the base text and
  lists the tools as a to-do under "mise".
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
fixed headers of `docs._SHELL_HEADERS`. Parts follow with their
`# From ~/<file>.` line. The shell migrator sends an assignment with a secret
to `SecretsDoc`, never to a repository file (section 5).
`ensure()` covers a layer file that exists but carries nothing: the
repository file still has to exist, so that the link moves the old file
aside. The syntax check (`facts.check_syntax`, `zsh -n`/`bash -n`) is done
by the `shell` migrator before it adds the text (`shell.planned`).

### 3.5 Declared

One de-duplication rule for setup and adopt.

```python
class Declared:
    @classmethod
    def for_setup(cls, base: Base, facts: Facts) -> "Declared": ...
    @classmethod
    def for_repo(cls, base: Base, repo: Path, nix_declared: dict, facts: Facts) -> "Declared": ...
    def brew(self, name: str, kind: str) -> Optional[str]   # what provides it, e.g. "ripgrep (dotkit, from Nix)",
                                                             # "jq (your mise config)"; None: nothing does
    def mise_key(self, key: str) -> bool                     # the base, the repository, or your mise config
    def yours_mise_key(self, key: str) -> bool               # your mise config only
    def tool(self, name: str) -> bool                        # a command that the base or the repository has,
                                                             # from Nix, Homebrew, or mise (BREW_ALIASES, mise_short)
    def tool_source(self, name: str) -> Optional[str]        # what has it, for the report: "bat (from Nix)"
    def tap(self, name: str) -> bool
    def mas_id(self, app_id: int) -> bool
    def mas_name(self, name: str) -> bool                    # a name that is taken: the raw names of
                                                             # nix/mas.txt, plus keys written by hand into
                                                             # homebrew.masApps
```

`Declared` contains:

- **always:** the base lists and the base mise tools, plus the tools of your
  `~/.config/mise/config.toml`, whether it is carried as "layer" or kept in
  place (in both cases mise still reads it);
- **`for_repo` only:** the repository's `nix/packages.txt`, `nix/brews.txt`,
  `nix/casks.txt`, `nix/taps.txt`, `nix/mas.txt` and `config/mise.toml`, plus
  the `taps` and `masApps` that `dotkit` evaluates from the configuration
  (`dotkit:309-311`), passed as `--declared`. The evaluated values already
  include `nix/taps.txt` and `nix/mas.txt`, and any entries that someone
  wrote into `homebrew.*` by hand. Adopt only ever writes to the two list
  files.

How the migrators use it. The skips are the same in setup and in adopt; the
report sentences below are written in setup only. Adopt skips a declared
item silently, so that a second run with nothing new outputs nothing.

- A Homebrew formula is skipped when `brew()` returns something. The report
  says what provides it and how to remove the Homebrew copy. For a tool of
  your mise config, that is a block of its own in "Homebrew copies of
  dotkit tools": "Your mise config has these tools, so they are not in your
  lists. The Homebrew copy stays installed. Remove it when nothing else
  needs it; brew says when another formula depends on it." with items such
  as "`jq`: your mise config has jq. `brew uninstall jq`".
- An npm, pipx, cargo, or Go tool is skipped when `tool()` is true (for
  example `cargo:bat`, which the base has from Nix), with a block in "Tools
  that are not in mise": "dotkit has these tools already, so they are not
  added." and items such as "`bat` (cargo install): dotkit has bat (from
  Nix). Old copy: `cargo uninstall bat`". It is skipped silently when
  `yours_mise_key` is true for its key (`npm:prettier`), and in adopt also
  when `mise_key` is.
- A runtime asks `yours_mise_key` only. The base's `node = "24"` is a
  default, not a version of yours, so an nvm `node 20` is still offered and
  added.

A generated line can look like a secret only because of a name: the mise
line `"pipx:detect-secrets" = "latest"`, or an App Store line `123 Token:
Keeper`. Before it adds an entry, a migrator asks the view
(`ListView.flagged(entry)`, `MiseView.flagged(key, value)`, which run
`secrets.line_secret` on the exact line that would be written). A flagged
entry is not added, in setup and in adopt alike, and it gets a to-do. That
to-do, and any other text about the entry (for example under "Not carried,
by your choice" when it is deselected), names it without the flagged line:
a tool by its name and its `mise use -g` command, an App Store app by its
ID, a tap by its name, with `brew tap-info <name>` for its URL. For
example: "`detect-secrets` (pipx): dotkit's secret check flags its line in
config/mise.toml, so it is not added. Add it by hand: `mise use -g
pipx:detect-secrets`." So the guard never has to mask such a line, and
adopt never stops on one. In adopt, the to-do comes back on each run until
the entry is added by hand: it is still missing.

The `appstore` migrator gives an app the `<name> (<id>)` label when `Declared.mas_name` or the `nix/mas.txt` document already has its
name (`appstore.add_app`, for setup and adopt). The `homebrew` migrator
checks a tap with the same functions that `ListDoc` uses at add time
(`docs.tap_problem`, `docs.tap_name_problem`).

### 3.6 Failure

```python
@dataclass
class Failure:
    migrator: str            # "runtimes", or "identity"
    title: str
    phase: str               # "scan" | "pages" | "plan" | "adopt" | "skipped" | "conflict"
    error: str               # "KeyError: 'versions'": at most 200 characters, the home as ~, masked (section 5);
                             # a conflict: what both owners wanted
    where: str               # the last traceback frame inside dotkit.migrate: "migrators/runtimes.py:88 in plan";
                             # a conflict: the document
    manual: str              # Migrator.manual_steps(ctx)
    reason: str = ""         # for "skipped": "Shell lines failed"; a conflict: the target, "npm:eslint"
```

A conflict (section 3.4, rule 4) is a `Failure` of the migrator whose value
was refused. The texts of the phases ("failed while planning") are
`model.PHASE_TEXT`.

### 3.7 Session

```python
class Session:
    def __init__(self, ctx: Context, migrators: Optional[Sequence[Migrator]] = None,  # None: registry.MIGRATORS,
                 strict: bool = False,                                               # read when constructed
                 check_syntax: Optional[Callable[[str, str], bool]] = None): ...
    def scan(self) -> Facts                          # isolated per migrator; cached
    def pages(self) -> List[Page]                    # isolated per migrator; cached
    def defaults(self, repo: str) -> Choices
    def build(self, choices: Choices) -> Plan        # fresh Docs on each call
    def adopt(self, repo: Path, nix_declared: dict, opts: AdoptOptions) -> AdoptResult
    def scan_warning(self) -> str                    # the warning line of the first screen (section 7)
    def trace(self, f: Failure) -> str               # the masked traceback, for --debug
    failures: List[Failure]                          # of the scan and the pages
```

`build()`:

1. Create `Docs("setup")`.
2. Run each migrator's `plan` in its own transaction (section 6).
3. In a core transaction: `nix/user.nix`, the keymap, the scan warnings,
   and the "Login shell" to-do.
4. The conflicts and failures, under "Problems during the migration".
5. `writer.render` and the plan-time guard.
6. `checks.check`.
7. `MIGRATION.md` and the summary, masked.

`__main__` calls `build()` once after the scan (for the blockers), the
wizard calls it each time the review screen opens, and `__main__` calls it
again before writing. Each call is deterministic for the same choices.

`adopt()` builds `Declared.for_repo`, runs each migrator's `adopt` in its
own transaction on `Docs("adopt")`, and returns an `AdoptResult` (`model.py`):
the changed files, the text each was read with (`before`), the output lines
(masked), and the failures.

### 3.8 Plan and the writer

```python
@dataclass
class Plan:
    repo: Path; keymap: str; home: str
    files: Dict[str, str]           # repository path -> text, tracked
    copies: Dict[str, Path]         # repository path -> source
    secrets: str                    # the lines that write_secrets appends to ~/.secrets, "" for none
    secret_names: List[str]         # their names, for the summary
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
  1. the copy guard (section 5), which drops a copy with a secret through
     `Docs.drop`, so that its links never reach `nix/home.nix`;
  2. the documents render;
  3. the file guard over the rendered tracked files.

  There is no separate public guard. After `render`, `checks.py` runs, and
  then `Session.build` renders the report and the summary and masks them
  (`mask_lines`).
- `write_secrets(plan)` for setup, before `write_new`: append `plan.secrets`
  to `~/.secrets`, which it creates with mode 0600. It writes no name that
  the file has (`SecretsDoc`), so a second run adds nothing. When it fails,
  setup stops before the repository exists. When `write_new` fails after it,
  `~/.secrets` has the variables that the shell files and config files of
  this Mac use already, so nothing changes for the shell.
- `write_new(plan, template)` for setup:
  1. copy the template into a temp directory next to the repository;
  2. add the copies and the files;
  3. write `.build/links-in-way`;
  4. verify the staged tree (`writer.verify`, section 5);
  5. `git init`;
  6. rename the directory to the repository path.

  If anything fails, the temp directory is removed.
  The removal also works when a copy brought read-only directories (mode
  0555): on a permission error it makes the parent directory writable
  (0o700), and the path itself when it is a directory, and tries again
  (`onexc` on Python 3.12 and newer, `onerror` on 3.9); if the directory
  still remains, a note on stderr names it.
- `apply_edits(result, repo)` for adopt:
  1. check that each file still has the text that the plan was built from
     (otherwise stop with `AdoptStopped`: "<file> changed since the scan;
     run dotkit migrate again"); resolve a list file that is a link to its
     target, which must be inside the repository (otherwise stop with an
     error); then write every changed file as a temp file next to its
     target;
  2. verify them (`writer.verify_edits`): only lines that the file did not
     have before count, so a repository that already holds a line that
     looks like a secret can still be adopted into;
  3. `os.replace` them one by one;
  4. if one replace fails, write back the old texts kept in memory.

`checks.py` has `check`, `link_targets`, `moved_aside`, `links_in_the_way`,
and `summary`. It works on `LinksDoc` and the
file system, and adds the blockers (`~/.config` is a link) and `move_links`.
It is core: it runs whatever the migrators did.

## 4. Report

`MIGRATION.md` (`report.render`) has two parts:
"Carried over" (one row per accepted contribution, in `MIGRATORS` order),
then "To do" with sections in `report.SECTIONS` order. `SECTIONS` is the
one ordered list of titles. `ReportDoc.todo` raises `ValueError` for a
section that is not in it (a failure of that migrator; strict mode
re-raises), and a test checks every migrator's `sections`. The order:

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
characters stays as it is (`rm ~/go/bin/hey`). One helper,
`context.shell_path`, builds these paths for every migrator.

The summary (the review screen, `--dry-run`, the plain prompts) starts with
the problems line when there are failures (section 6.4), then the lines of
`checks.summary`.

## 5. Secrets

Secrets are handled at five points:

1. **Detection during the scan.** Every config file the scan finds gets its
   `secrets`, including `~/.config/mise/config.toml`. Planning does not call
   `secrets.has_secret`.
2. **Routing in the migrators.** Secrets go to `~/.secrets` as variables
   (`SecretsDoc`), and the repository files refer to the variables. The base
   `~/.zprofile` and `~/.bash_profile` source `~/.secrets`, and the
   `gui-secrets` agent of `nix/darwin.nix` gives its exported variables to
   GUI apps.
   - The shell migrator sends an assignment with a secret (rcparse's
     `SecretLine` that `rcparse._ASSIGN` matches) to `~/.secrets`, as it was
     written. Another statement with a secret (an alias, a function) is not
     carried; the report names it, never its text.
   - `config_files` asks `lift.py`. `lift.npmrc` finds the tokens of
     `~/.npmrc`; each token becomes `${NAME}`, which npm expands, and the
     other lines are carried as they are.
     `lift.gitconfig` takes the settings with a secret out of a Git config
     file; they become `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_<n>`, and
     `GIT_CONFIG_VALUE_<n>`. A file with a secret that `lift.py` cannot move gets only
     `keep`/`dotkit`, as before.
   - `other_config` does not offer an entry with a secret.
   - `SecretsDoc` never writes a name that `~/.secrets` has: the same value
     is done, another value is refused, and the caller reports the name.
     `free_name` picks `NAME_2`, `NAME_3`, ... for a name that the migrator
     chooses (the npm tokens). A refused value is not a `Conflict`, because
     a conflict shows both values.
3. **The plan-time guard (core, inside `writer.render`).** `_guard_copies`
   and `_guard_files` run over every tracked rendered file and every copy
   source. A shell file is split by statement, never in the middle of one.
   Other files are masked line by line. Then the rendered report, the
   summary, and adopt's output are masked line by line
   (`writer.mask_lines`). The guard changes the plan and reports what it
   did, so dry-run and the review screen show its result.
4. **Write-time verification (core, in `writer`).** Before the rename or
   `os.replace`, scan the staged tracked files (copies included; `.build/`
   and `.git` excluded, because Git does not track them). It uses
   exactly the checks of the plan-time guard (`secrets.scan_text` for files,
   `secrets.has_secret` for copied files and template files, the same
   strictness, links inside a copied directory and `.DS_Store` skipped), so
   it can only find what differs from what the guard saw, for example a file
   that changed between the scan and the copy. It changes nothing. A find raises
   `SecretInRepository` (the file and the kind of secret, never the value),
   which aborts the write and leaves nothing behind. A directory that the
   verification cannot read also aborts it: it fails closed. `__main__` prints
   "dotkit setup: <message>. Nothing was written." (or "dotkit migrate: …")
   and exits 2; `--debug` adds the traceback.
5. **Failure text.** `Failure.error` and the `--debug` traceback pass through
   `secrets.line_secret` line by line. A line with a secret becomes "(this
   line has a secret; not shown)".

Rules that keep this enforceable:

- **Migrators cannot write files.** An AST test over `migrators/`
  (`tests/test_registry.py`) fails on `open(…)` or `Path.open(…)` with a
  write or append mode; on the `Path` methods `write_text`, `write_bytes`,
  `unlink`, `rename`, `touch`, `mkdir`, `rmdir`, `symlink_to`,
  `hardlink_to`, and `chmod`; on `shutil.copy*`, `shutil.copytree`,
  `shutil.move`, `shutil.rmtree`, `os.replace`, `os.rename`, `os.remove`,
  `os.unlink`, `os.system`, `os.popen`, `os.symlink`, `os.makedirs`, and
  `subprocess`; and on an import of `proc`, `subprocess`, `writer`, `ui`, or
  `curses` (section 2). Commands run only through `ctx.runner` (read-only by
  contract).
- **The guard and the verification fail closed.** If either raises, the run
  stops and nothing is written (section 6.2).
- **A property test:** a home directory with a `fake.TOKENS` value in every
  file that any migrator reads must give no token in `Plan.files`, the copied
  sources, the report, the summary, or adopt's output. Tokens in
  `Plan.secrets` are allowed: that is where secrets go. How the test finds the
  files:
  1. Scan and plan the kitchen-sink Mac (and adopt it) once, and record every
     file opened under the home directory (patch `io.open`, `builtins.open`,
     and `os.open` for the test). Every copy source of that run counts as
     read too, including the files inside a copied directory: the writer
     reads them only at write time, so otherwise a migrator that skips its
     own secret check would copy files that never get a token.
  2. A sanity check fails the test when the record misses files that the
     migrators are known to read (`~/.zshrc`, `~/.npmrc`, an npm
     `package.json`, a pipx `pipx_metadata.json`, the uv receipt), so that a
     Python version where the patch sees nothing cannot pass silently.
  3. Put a token into each recorded file without breaking its format: a JSON
     object gets one more key; a file that is not UTF-8 text (the Go binary)
     is skipped, from a short explicit list in the test ("not text" means
     not UTF-8 or containing a NUL, the rule of `secrets.scan_file`); every
     other file, also JSON that is not an object,
     gets a comment line `# <token>` (valid in shell, TOML, INI, YAML, and
     Git config files).
  4. Run again. The scan warnings must equal those of the first run, so the
     tokens broke no parser, and the assertions above must hold. The guard
     must catch nothing in this run: no report entry of the `guard` owner,
     and no masked line ("(a line with a secret was here)", or the
     placeholder comment in a generated file) in the report, the summary,
     adopt's output, or `Plan.files`. Masking hides a leak, so the token
     assertions alone cannot see one. The migrators route secrets
     themselves, and the guard and the masking are only the backstop, so a
     catch means a migrator bug.

## 6. Errors

### 6.1 Expected problems

A command that fails or times out (`Runner.run` returns `None`), an
`OSError` on a file, or a file that does not parse is not a bug. The
migrator handles it, keeps what it could read, and adds a line to
`facts.warnings`. The report lists the line under "Scan warnings".

### 6.2 Crashes: isolated per migrator

An unexpected `Exception` in a migrator's `scan`, `pages`, `plan`, or
`adopt` is a crash. `KeyboardInterrupt` and `SystemExit` are not caught. When
a migrator crashes:

- **in `scan`:** its facts object and its warnings are thrown away, and
  `facts.get(key)` returns `None`;
- **in `mise_commands`** (run by `Session.scan` after every scan): the same
  as in `scan`, with a `Failure` of the scan, and none of its commands are in
  `Facts.mise_commands`;
- **in `pages`:** it has no pages, and its `plan` does not run (without pages
  there are no defaults and no choices);
- **in `plan` or `adopt`:** its transaction is rolled back, so it contributes
  nothing. No half of its entries, report rows, or copies stay.
- **in every case:** one `Failure` is recorded, and the rest of the run
  continues.

```python
# Session._plans
for m in self.migrators:
    if m.key in out:                    # failed earlier, or skipped (7.3)
        continue
    txn = docs.begin(m.key)
    try:
        m.plan(facts, choices, txn)
        txn.commit()
    except Exception as e:
        txn.rollback()
        self._fail(m, "plan", e, failures)   # records the Failure; re-raises in strict mode
        out.add(m.key)
        return False                    # build() starts again without it
```

**A crash in `pages` or `plan` starts again without that migrator.** Other
migrators read its facts (for example the tool migrators read
`config_files`), so they may already have planned around it. After the
rollback, the Session hides the failed migrator's facts (`Facts.without`)
and builds the pages or the plan again from fresh `Docs`. Each migrator can
fail once, so a build runs at most once more than there are migrators. The
result equals the result of the registry without that migrator.

There is no fallback. A failed migrator means plain-template behavior for
its part, and its `manual_steps` text says exactly what that is (section
6.5).

Core code is not isolated: the documents' rendering, the writer, the secret
guard, the verification, `checks.py`, and the UI frontends. There is no safe
partial result for them, so an exception there stops the run with nothing
written. The write is atomic, so nothing is half done. A UI exception is
shown after curses has restored the terminal.

### 6.3 Migrators that depend on others

A migrator that reads other facts through `facts.get()` must handle `None`
and do less. For example, `leftovers` without the shell facts finds no
carried PATH lines (`shell.carried_paths` returns nothing), and still lists
the old copies in `~/.local/bin` and the Homebrew directories. A migrator
that cannot work without another lists it in `needs` (no migrator has
one yet). If that one failed, it does not run, and the Session records
`Failure(phase="skipped", reason="<title> failed")`. `needs` is only for
skipping. It never changes the order.

The same holds for another migrator's choices: a migrator never reads a
page key of another migrator directly. It asks a pure helper of that
migrator, which answers "no" when that migrator's facts are missing (for
example `other_config.adopted(facts, choices, rel)`, which `shell` asks for
`~/.bashrc`).

### 6.4 What the user sees

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
- **The UI:** section 7.
- **Exit code:** 0 when migrators failed. Setup goes on to build; the
  failures are on stderr, in the summary, and in MIGRATION.md. Exit codes for
  other errors (`__main__.py`): 2 for a bad path, a missing terminal,
  blockers, or a stopped write (a secret found by the verification, or in
  adopt a file that changed since the scan); 1 for quit.
- **`dotkit migrate`:** adds what the working migrators found, and prints
  "Could not check: runtimes (…); nothing of it was added."

### 6.5 `manual_steps` for `shell` and `config_files`

These two must describe what the template does without them. That behavior
comes from `nix/home.nix:118-160`, `templates/default/nix/home.nix`, and
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
> ~/.config/starship.toml, ~/.gemrc, ~/.bunfig.toml, ~/.editorconfig, and
> ~/Library/Preferences/pnpm/config.yaml, and links config/mise.toml of this
> repository as ~/.config/mise/config.toml. The first apply renames your files
> at these paths to <name>.before-dotkit. They stay on disk, outside the
> repository, and the tools no longer read them. The tools of your old mise
> config are not in config/mise.toml. ~/.npmrc and ~/.config/uv/uv.toml stay
> in place, outside the repository: their tools read them after the dotkit
> settings. ~/.gitconfig stays in place, and Git still reads it after the
> dotkit settings. Copy the settings you need from the .before-dotkit files.

### 6.6 Strict mode

Strict mode re-raises where the error happens: crashes, conflicts, unknown
report sections. It is on in all unit tests (`fake.session(...)` defaults to
`strict=True`; the isolation tests turn it off explicitly), and in the CI e2e
job (`DOTKIT_MIGRATE_STRICT=1` in `.github/workflows/ci.yml`). There is also
a hidden `--strict` flag. Production runs are never strict.

## 7. UI

Screens are data. `ui/screens.py` builds a list of `Screen(kind, id, title,
intro, rows, get, set, detail, note, dest, lines, problems, warning)` from
the Session (`note` and `dest` come from the page; `lines` is the review
text; `problems` is the review's Problems block; `warning` is the warning
line of the first screen). The curses and plain frontends render the same
screens.

The sequence:

1. Where the repository goes (`text`).
2. The zsh keymap (`radio`).
3. One screen per page, in `MIGRATORS` order (`checklist` or `choices`).
   Pages without items are skipped.
4. The plan (`review`).

The header shows `step/total` (`UI.frame`) for the screens that are shown.

| Screen kind | In `ui/curses_ui.py` | Keys |
|---|---|---|
| `text` | `UI.text_input` | type, Backspace, Ctrl+U, Enter, Esc quits |
| `radio` | `UI.radio` | ↑↓/space select, Enter, b back |
| `checklist` | `UI.checklist` | ↑↓/j k move, space toggles one item, `a` all or none, Enter next, b back, q quit; the detail line shows `Item.detail`, the page note shows under the intro |
| `choices` | `UI.choice_list` | ↑↓ move, space/→/l next option, ←/h previous, Enter, b |
| `review` | `UI.pager` | ↑↓ scroll, Enter create (or print), b back |

Plain prompts (`ui/plain.py`): a checklist page prints its numbered items
and their state. The answer is Enter (keep), numbers or ranges to toggle
(`1 3-5`), `a` (all), or `n` (none), one answer per page; an answer that
does not parse asks again. A choices page asks per item. The plain prompts have no way back; end of input quits, and
nothing is written. `--plain` and an unusable terminal
(`curses_ui.available()`) select it.

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

## 8. Testing

| Kind | Where | What it proves |
|---|---|---|
| Migrator unit | `tests/migrators/test_<key>.py` | `scan` on `fake.Home` + `FakeRunner` gives the expected facts; `pages` gives the expected items and defaults; `plan` on an in-memory `Docs` gives the expected entries and report rows; `adopt` against a `Declared` |
| Document | `tests/test_docs.py` | per type: load, add, render; conflicts; `add()` checks; `render()` never raises on accepted entries; `MiseDoc.replace_setting` refusals and the parse-compare undo; `commit()`/`rollback()`; a second `begin()` asserts |
| Writer and checks | `tests/test_writer.py`, `tests/test_checks.py` | guard behavior (`test_guard`, `test_final_guard_keeps_secret_shell_statements_whole`); write-time verification aborts with nothing left; links in the way; the masked summary |
| Session and adopt | `tests/test_session.py`, `tests/test_adopt.py`, `tests/test_declared.py`, `tests/test_failed_scans.py` | whole plans on `used_mac` and other fake Macs; the identity scan; `Session.adopt` and `writer.apply_edits` (apply twice, a fault in the second replace, old and new secret lines, a changed file, links); `Declared`; a failed scan of each migrator that has facts of its own (setup, and `dotkit migrate` equal to the registry without it) |
| Golden plans | `tests/test_golden.py`, `tests/golden/*.json` | the full `Plan` (all fields; normalized: in `Path` values the home directory is `~`; in text it is `<HOME>` and the rest of the temp directory is `<TMP>`, so a `~` in text means the code wrote `~` itself; fixed user, date, and hostname; fake tokens as their placeholders; a fake `check_syntax`) for an empty Mac, `used_mac` (`tests/test_session.py`), and a kitchen-sink Mac, each with the default choices, and the last two also with deselected items; the adopt output and the edited files of the kitchen-sink Mac, with and without `--all`. `DOTKIT_MIGRATE_REGOLD=1` rewrites them. A golden diff is how every behavior change is reviewed |
| Registry invariants | `tests/test_registry.py` | unique page keys and item-id prefixes; `sections` ⊆ `SECTIONS`; an empty home gives no warnings and no contributions; the AST no-write test; the secret property test (section 5) |
| Extension points | `tests/test_extension_points.py` | a fake tool migrator and fake catalog entries, defined only in the test: the catalog, invariant, failed-scan, isolation, and leftovers test classes run unchanged with them |
| Isolation | `tests/test_isolation.py` | for every migrator M and every phase P (and `mise_commands`), M.P raising (for `plan`: after real work, so it has pending entries) gives the same plan as the registry without M, plus one `Failure`, plus `skipped` entries for its `needs` dependents; strict mode re-raises; a token in an exception message is not in the report, summary, stderr, or `--debug` output; a core failure (a document render, the guard) gives no repository directory |
| UI flow | `tests/test_ui_flow.py` with a `ScriptedFrontend` that answers screens by id and item id | page order, skipped empty pages, defaults, per-item selection, the warning line, the Problems block |
| PTY | `tests/test_wizard_pty.py`, `tests/wizard_child.py` | curses mechanics: keys, keypad sequences, resize, small window, quit, the plain prompts with answer counts taken from the Session; one test where `wizard_child.py` adds a migrator that crashes, and the wizard still finishes |
| Start and grammar | `tests/test_start.py` | `-I -B` isolation; the Python 3.9 grammar |
| Temp directories | `tests/__init__.py` (`load_tests`), `tests/test_tempdirs.py` | a run of the suite leaves no `dotkit-migrate-*` directory in `$TMPDIR`; the tests make their `dotkit-migrate-*` directories with `fake.tempdir()` and remove them with `fake.remove()` |
| e2e | `tests/e2e/migrated.sh` (at the top of the repository, not in `lib/tests`) in CI | a used macOS runner, strict mode; each new migration adds a fixture to the e2e job and a check here |

Other focused tests: `tests/test_catalog.py` (the generated rcparse rules),
`tests/test_rcparse.py`, `tests/test_secrets.py`, `tests/test_context.py`,
`tests/test_base.py` (the tables against `nix/home.nix`),
`tests/test_main.py` (the CLI in a child process),
`tests/test_nix_lists.py` and `tests/test_dotkit_pkg.py` (the list readers
and `dotkit add mas:ID`; skipped without nix or `scripts/`).

The standard checks for every step (called STD below):

```sh
cd lib && /usr/bin/python3 -B -m unittest discover -s tests -t . -v
cd lib && nix shell --inputs-from .. nixpkgs#python3 nixpkgs#nixfmt -c python3 -B -m unittest discover -s tests -t . -v
nix build .#checks.aarch64-darwin.migrate
# On a real Mac, read-only. Take "before" at the start of the step, the same day; the path must not exist:
migrate/dotkit-migrate setup --dry-run --yes --repo ~/.dotkit-dryrun-probe > /tmp/dk-after.txt 2>&1
diff /tmp/dk-before.txt /tmp/dk-after.txt
```

## 9. How to add a migrator

1. **Catalog:** add entries to `catalog.py` if the migration is a family
   (a version manager, a framework, a package source rule). rcparse picks up
   new words, variables, and PATH markers from it. A new framework or
   version manager is only a catalog entry: `frameworks`, `runtimes`, and
   `node_tools` loop over the catalog, so skip step 2. The catalog tests
   check every entry generically. An app that keeps its config in a directory
   of its own, such as pi in `~/.pi/agent`, is an `AppConfigSpec` of
   `APP_CONFIGS`: its config entries, its repository directory, and its old
   places, which `other_config` reports and never offers. Its login files
   go to `secrets.SECRET_FILES` and `logins.STEPS`.
2. **Module:** add `migrators/<key>.py` with a `Migrator` subclass (`key`,
   `title`, `sections`, `needs` if it cannot work without another), a facts
   dataclass and its `facts_of` accessor, `scan`, `pages` (stable, prefixed
   item ids; safe defaults), `plan`, `adopt` if `dotkit migrate` should add
   it, and `manual_steps` (`tests/test_registry.py` checks that it has one).
   A tool source that moves to mise reuses `migrators/tools.py`, and
   implements `mise_commands(facts)` (the commands of each mise key it can
   add, `tools.mise_commands`), which the leftovers report uses to find
   shadowed binaries.
3. **Outputs:** reuse document types. Add a document type to `docs.py` only
   for a new kind of output file, with its add-time checks and its tests.
4. **Report:** add new section titles to `report.SECTIONS`, in their place.
5. **Registry:** add the migrator to `MIGRATORS` in its place. That is its
   page order and its row order in "Carried over".
6. **Secrets:** content that can hold a secret is routed by the migrator
   (`Txn.secrets()`, restricted options). A config format that can read a
   variable gets a function in `lift.py` that moves its secrets out. Never
   copy a file the scan did not check.
7. **Tests:** `tests/migrators/test_<key>.py`; extend the kitchen-sink
   fixture (`kitchen_sink` in `tests/test_golden.py`) and regenerate the
   goldens (review the diff); the invariant and isolation tests cover it
   without new code: they take their lists from `MIGRATORS` (for example
   the adopters are the migrators that override `adopt`).
   `tests/test_extension_points.py` shows it with a fake tool migrator and
   fake catalog entries.
8. **e2e:** add a fixture to the CI e2e job and a check in
   `tests/e2e/migrated.sh`.
9. **Docs:** update the README table "On the Mac / In the repository".

## 10. Leftovers report

The wizard never removes anything. Most leftovers are dead wood:

- dotkit links its own `~/.zshenv`, `~/.zprofile`, `~/.zshrc`, and
  `~/.bash_profile`, so the old files (renamed `.before-dotkit`) are not
  read.
- rcparse never carries framework or version-manager lines (`SKIP_VARS`,
  `_RULES`, and `_PATH_VERSION_MANAGERS` in `rcparse.py`, built from
  `catalog.py`).
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
  prompt. That includes all of `DOTKIT_TOOL_PATH` (`nix/home.nix:92-103`:
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
  (`nix/darwin.nix:43-44`). There, a leftover binary with the same name in
  `~/.local/bin`, `/opt/homebrew/bin`, or `/opt/homebrew/sbin` **wins** over
  the mise shim.
- **Nested shells and direnv** (checked with mise 2026.10.3 and dotkit's
  direnv hook, in a real login interactive zsh driven through a pty). The
  first `mise activate` saves PATH in `__MISE_ORIG_PATH`, and nested shells
  inherit it (a later `mise activate` keeps it). At each hook-env, an entry
  that is not in the saved PATH and not mise's goes in front of mise's
  tools if it comes before every saved entry. If it comes after one of
  them, it goes to the very end of PATH (after `/usr/bin`, `/bin`, `/sbin`).
  direnv's hook is first in `precmd_functions`
  (`nix/modules/zsh/default.nix:103-105`) and mise's runs later, so in the
  first shell the directories of a direnv directory come before a carried
  new directory and before mise's tools. Let P be a directory that a parent
  shell put in front of PATH: a project's tools from direnv or from
  `nix develop -c zsh` (a PATH prefix, `PATH=<dir>:$PATH zsh`, stood in for
  a real `nix develop`). In a nested `zsh -i` or `zsh -l -i` (and
  `exec zsh`, checked only for the `config/zsh/zshrc` lines):
  - With the dotkit base alone, P stays first. In `zsh -l -c` (agents), P
    stays first through `_dotkit_first`.
  - A carried line in `config/zsh/zshrc` that adds a new directory runs
    again in the child and puts that directory before P, so it also shadows
    the tools of the project.
  - A carried line in `config/zsh/zshrc` that puts in front a directory that
    `config/zsh/zprofile` adds already moves P to the end of PATH: that
    directory is a saved entry, and P now comes after it. A directory of
    `config/zsh/zshenv` is a saved entry too (checked with a zshenv line
    that prepends it and with one that appends it). So mise's global
    tools and the system directories come before the tools of the project.
    With the same directory only in `config/zsh/zprofile`, or appended in
    `config/zsh/zshrc` (`path+=(…)`), P stays first. `zsh -l -c` is not
    affected (it reads no zshrc and runs no prompt hook).
  - A carried line in `config/zsh/zshenv` that puts a directory in front
    has the same effect by itself, with or without a line in
    `config/zsh/zshrc`: every zsh runs zshenv, also a nested non-login one.
    In a nested `zsh -l -c`, the directory of that line comes before P.
    The same line in `config/zsh/zprofile`, without a line in
    `config/zsh/zshrc`, keeps P first in all these cases.
  - The directories of `DOTKIT_TOOL_PATH` stay behind mise's tools, also in
    `zsh -l -c`. That shell does not activate mise again (zprofile skips it
    when `MISE_SHELL` is set), but the inherited mise tool directories come
    before `DOTKIT_TOOL_PATH`, so they are in `_dotkit_first`, which
    zprofile puts first.
- **Not checked:** a real `nix develop` (its default shell is bash), panes
  of a terminal multiplexer (they inherit the environment of the
  multiplexer server), and nested bash. The report text must not make
  claims about them.

What the migrator reports:

1. **Carried PATH lines that add a new directory.** For each one in
   `config/zsh/zshrc`, list the file and line. "New" means not in this set:
   the `DOTKIT_TOOL_PATH` entries (`base.TOOL_PATH`, checked by
   `test_base` against `nix/home.nix`), the entries that
   `rcparse.path_done` leaves out (`rcparse._PATH_DONE`), the system
   directories, and the directories carried into `config/zsh/zshenv` or
   `config/zsh/zprofile`. Each such line
   is listed, also when nothing in its directory shadows a tool now, in a
   block of its own: it puts the directory in front of mise's tools, and in
   an interactive zsh that starts from another shell, also in front of the
   directories that the parent shell put in front of PATH (P above).
2. **Shadowed tools.** If such a directory, or `~/.local/bin`,
   `/opt/homebrew/bin`, or `/opt/homebrew/sbin`, contains a command with the
   same name as a mise tool, warn. The mise tools are the tool entries that
   this run added to `config/mise.toml` (none when your mise config is kept:
   those tools are not in mise yet) and the base's mise tools (`Base.mise`:
   the runtimes, `uv`, …), whose shims GUI apps use. Name the context:
   - for a carried new directory: "interactive shells";
   - for a toolPath directory: "GUI apps and launchd agents; interactive
     shells use mise's".

   Suggested fixes, in order:
   - drop the line;
   - move it to config/zsh/zprofile (`~/.zprofile.local`), which runs before
     mise starts;
   - uninstall the leftover binary (`pipx uninstall ruff`,
     `npm uninstall -g typescript`, `brew uninstall node`). Which package a
     binary belongs to comes from its link target, read at scan time and
     resolved against the directory it is listed in. A `Cellar/<formula>/`
     or `Caskroom/<cask>/` segment anywhere in the target names the Homebrew
     package; a `…/pipx/venvs/<package>/`, `…/uv/tools/<package>/`, or
     `…/lib/node_modules/<package>/` segment names the pipx, uv, or npm
     package (`npm` and `corepack` themselves give no command). Without
     such a segment, the item says "`ls -l <path>` shows where it comes
     from" instead of a command. When the Homebrew package is in the lists
     of this repository (for example `python@3.13`, kept on the formulae
     page), the item says so and gives `dotkit remove brew:<name>` (or
     `cask:<name>`), which takes it out of the list and uninstalls it, not
     a bare `brew uninstall`, which the next apply would undo;
   - as a last resort, `activate_aggressive = true` in `[settings]` of
     config/mise.toml. It acts only on full hook-env runs, and it does not
     change GUI apps.
3. **Carried PATH lines that put an earlier directory in front again.** For
   each line in `config/zsh/zshrc` that puts in front (`Carried.front`) a
   directory that `config/zsh/zprofile` or `config/zsh/zshenv` adds
   already, list the file, the line, and the earlier files, in a block of
   its own (`leftovers.AGAIN`). These are front directories that are not
   new: rcparse leaves out the toolPath and system ones
   (`rcparse.path_done`), so only the early ones remain. Both sides are
   compared without a trailing slash. In an interactive zsh that starts
   from another shell (`zsh`, `exec zsh`), mise moves P to the end of PATH.
   The fix is to drop that directory from the line. An appended line
   (`path+=(…)`) puts nothing in front and gets no item. A zshenv line that
   puts the directory in front also gets the item of point 4.
4. **Carried PATH lines of zshenv that put a directory in front.** For each
   line carried into `config/zsh/zshenv` with a non-empty `Carried.front`,
   list the file, the line, and its front directories, in a block of its
   own (`leftovers.ZSHENV`). Every zsh runs zshenv. In an interactive zsh
   that starts from another shell (`zsh`), mise moves P to the end of PATH.
   In `zsh -l -c`, the directory of the line comes before P. The fix is to move the line to config/zsh/zprofile
   (`~/.zprofile.local`).
5. **Shell files that dotkit does not replace and that still run:**
   `~/.bashrc` (interactive bash, after `mise activate`), `~/.zlogin` and
   `~/.zlogout` (zsh reads them), `~/.profile` (`sh -l`). Parse them with
   rcparse in report-only mode, and list their framework, version-manager,
   and new-PATH lines as "still runs". Installers for nvm, sdkman, and conda
   write to `~/.bashrc`.
6. **mise files that mise still reads:** `~/.tool-versions` (always read,
   whatever the idiomatic settings) and `~/.config/mise/conf.d/*.toml`. The
   `runtimes` migrator reports them ("mise still reads these files"), and
   its intro says that they override the dotkit defaults for everything
   under `~`; `leftovers` does not repeat them.
7. **Homebrew packages that are not in the lists** need no block here. A
   deselected one is under "Not carried, by your choice", whose Homebrew
   intro also says that `dotkit upgrade brew` upgrades only the declared
   packages (`dotkit:251-257`), so it goes stale, and that
   `homebrew.onActivation.cleanup = "uninstall"` would uninstall it. One
   that dotkit or your mise config provides is under "Homebrew copies of
   dotkit tools". A dependency formula, which `brew leaves` does not show,
   matters only when it shadows a tool, and point 2 finds that by the
   binary.
8. **A reference check next to every `rm` or `rm -rf` command** (not next to
   the uninstall commands of a package manager): "Before `rm -rf
   ~/.pyenv`, check that nothing points into it: LaunchAgents, IDE
   interpreter settings, git hooks, crontab (`grep -rl ~/.pyenv
   ~/Library/LaunchAgents`)."

The facts this needs:

- `Package.commands` (`migrators/tools.py`): the command names of each
  package. npm: `bin` in `package.json`; pipx: the apps in
  `pipx_metadata.json`; uv: the receipt's entry points; cargo: `bins` in
  `.crates2.json`; Go: the file name. Each tool migrator names them by mise
  key in its `mise_commands` (`tools.mise_commands`), and the Session
  merges them into `Facts.mise_commands`, which `leftovers.mise_tools`
  reads. The mise tools with more commands than their name are in
  `catalog.MISE_COMMANDS`: `node`, `npm`, `npx`; `python3`, `python`,
  `pip3`; `ruby`, `gem`; `uv`.
- The `shell` scan runs rcparse (pure) at scan time, and lists the
  executables of each existing directory in a carried PATH line.
- The `leftovers` scan lists the executables of `~/.local/bin`,
  `<brew_prefix>/bin`, and `<brew_prefix>/sbin` (names and link targets;
  no file is opened).
