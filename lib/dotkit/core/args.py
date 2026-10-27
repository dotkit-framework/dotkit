"""The option parser of the dotkit commands, with the rules of the Bash scripts (PARITY.md, rows O and P).

Options can come anywhere after the command, and `--` ends them. Each word
is read in order: -h stops the parse with Help at once, so an unknown option
before it stops first. There are no combined short options (-ny) and no
abbreviated long options. argparse has other rules and other messages, so
dotkit does not use it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Sequence, Tuple, Union

from . import ui


class Help(Exception):
    """-h or --help: print the usage and exit 0."""


@dataclass
class Spec:
    # Option -> the name of a flag: {"-n": "dry_run", "--dry-run": "dry_run"}.
    flags: Dict[str, str]
    # Option that takes a value -> (name, the message when the value is missing).
    values: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    # Name of a value -> a function that checks it and gives the value to keep, when the option is read.
    convert: Dict[str, Callable[[str], str]] = field(default_factory=dict)
    # The text after "unknown option: OPT."
    hint: str = "Use dotkit --help."


Options = Dict[str, Union[bool, str]]


def parse(argv: Sequence[str], spec: Spec) -> Tuple[Options, List[str]]:
    """The options (each flag False or True, each value "" or the text) and the other words, in order."""
    opts: Options = {name: False for name in spec.flags.values()}
    opts.update({name: "" for name, _ in spec.values.values()})
    args: List[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            args += argv[i + 1:]
            break
        if a in ("-h", "--help"):
            raise Help()
        if a in spec.flags:
            opts[spec.flags[a]] = True
        elif a in spec.values:
            name, missing = spec.values[a]
            if i + 1 >= len(argv):
                raise ui.usage_error(missing, spec.hint)
            i += 1
            opts[name] = spec.convert[name](argv[i]) if name in spec.convert else argv[i]
        elif a.startswith("-"):
            raise ui.usage_error(f"unknown option: {a}.", spec.hint)
        else:
            args.append(a)
        i += 1
    return opts, args
