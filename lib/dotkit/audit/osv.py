"""The OSV API (https://google.github.io/osv.dev/api/), with curl.

One POST /v1/querybatch asks for all tools at once and returns only the IDs of the advisories and the
time of their last change. GET /v1/vulns/ID returns an advisory; a cache in $XDG_CACHE_HOME/dotkit/osv
keeps each one until OSV changes it. A run without new advisories is one request: a few kilobytes.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..core import proc, ui

API = "https://api.osv.dev/v1"
# The most queries in one request that OSV takes.
BATCH = 1000
# A query with more advisories than one page has gets more pages; this many at most.
PAGES = 20
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")


def cache_dir(environ=None) -> Path:
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_CACHE_HOME") or os.path.join(environ.get("HOME") or os.path.expanduser("~"), ".cache")
    return Path(base) / "dotkit" / "osv"


def _curl(args: List[str], body: Optional[str] = None) -> dict:
    # --retry-all-errors: also when the network is not back yet after the Mac wakes.
    argv = ["curl", "-fsS", "--max-time", "60", "--retry", "3", "--retry-delay", "5", "--retry-all-errors", *args]
    r = proc.run(argv, stdin=body, stdout=proc.CAPTURE, stderr=proc.CAPTURE)
    if not r.ok:
        raise ui.die(f"cannot read {args[-1]}: {r.err.strip() or f'curl exit code {r.code}'}")
    try:
        data = json.loads(r.out)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise ui.die(f"{args[-1]} did not return a JSON object.")
    return data


def query(queries: Sequence[dict]) -> List[List[Tuple[str, str]]]:
    """(ID, modified) of the advisories of each query, in the order of the queries."""
    results: List[List[Tuple[str, str]]] = [[] for _ in queries]
    tokens: Dict[int, str] = {}
    pending = list(range(len(queries)))
    for _ in range(PAGES):
        if not pending:
            break
        more = []
        for start in range(0, len(pending), BATCH):
            chunk = pending[start:start + BATCH]
            body = {"queries": [dict(queries[i], page_token=tokens[i]) if i in tokens else queries[i] for i in chunk]}
            data = _curl(["-H", "Content-Type: application/json", "--data-binary", "@-", f"{API}/querybatch"],
                         json.dumps(body))
            got = data.get("results")
            if not isinstance(got, list) or len(got) != len(chunk):
                raise ui.die(f"{API}/querybatch returned {len(got) if isinstance(got, list) else 'no'} results "
                             f"for {len(chunk)} queries.")
            for i, r in zip(chunk, got):
                r = r if isinstance(r, dict) else {}
                results[i] += [(v["id"], v.get("modified") or "") for v in r.get("vulns") or []
                               if isinstance(v, dict) and isinstance(v.get("id"), str) and _ID.fullmatch(v["id"])]
                token = r.get("next_page_token")
                if isinstance(token, str) and token:
                    tokens[i] = token
                    more.append(i)
        pending = more
    return results


def records(ids: Dict[str, str], cache: Path) -> Dict[str, dict]:
    """The advisory of each ID; ids: ID -> modified, from query(). The cache keeps the modified time of
    the batch with each advisory: the advisory itself has the time with more digits. A cached advisory
    with the same time is not read again; the others come in one curl, 8 at a time."""
    out: Dict[str, dict] = {}
    need: List[str] = []
    for rid, modified in sorted(ids.items()):
        try:
            cached = json.loads((cache / f"{rid}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = None
        if (isinstance(cached, dict) and modified and cached.get("batch_modified") == modified
                and isinstance(cached.get("record"), dict)):
            out[rid] = cached["record"]
        else:
            need.append(rid)
    if not need:
        return out
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ui.die(f"cannot create {cache}: {e.strerror}.") from None
    args = ["--parallel", "--parallel-max", "8"]
    for rid in need:
        # A download of an earlier run that stopped must not count as this one.
        try:
            (cache / f"{rid}.download").unlink()
        except OSError:
            pass
    for rid in need:
        args += ["-o", str(cache / f"{rid}.download"), f"{API}/vulns/{rid}"]
    r = proc.run(["curl", "-fsS", "--max-time", "60", "--retry", "3", "--retry-delay", "5", "--retry-all-errors",
                  *args], stdout=proc.CAPTURE, stderr=proc.CAPTURE)
    missing = []
    for rid in need:
        download = cache / f"{rid}.download"
        try:
            record = json.loads(download.read_text(encoding="utf-8"))
            download.unlink()
        except (OSError, ValueError):
            record = None
        if not isinstance(record, dict):
            missing.append(rid)
            continue
        out[rid] = record
        try:
            tmp = cache / f"{rid}.tmp"
            tmp.write_text(json.dumps({"batch_modified": ids[rid], "record": record}), encoding="utf-8")
            os.replace(tmp, cache / f"{rid}.json")
        except OSError:
            pass
    if missing:
        error = r.err.strip().split("\n")[-1] if r.err.strip() else f"curl exit code {r.code}"
        raise ui.die(f"cannot read {len(missing)} of {len(need)} advisories from {API}/vulns: {error}")
    return out
