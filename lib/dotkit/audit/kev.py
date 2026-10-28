"""The catalog of known exploited vulnerabilities of CISA (KEV): the CVEs that attackers use. A finding of
one of them is always listed. The catalog is about 2 MB; the cache in $XDG_CACHE_HOME/dotkit keeps it for a
day.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import FrozenSet, Optional

from ..core import proc

URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
MAX_AGE = 24 * 3600


def cves(text: str) -> FrozenSet[str]:
    """The CVE IDs of the catalog; an empty set for text that is not the catalog."""
    try:
        data = json.loads(text)
    except ValueError:
        return frozenset()
    entries = data.get("vulnerabilities") if isinstance(data, dict) else None
    return frozenset(e["cveID"] for e in entries or [] if isinstance(e, dict) and isinstance(e.get("cveID"), str))


def load(cache: Path, now: Optional[float] = None) -> Optional[FrozenSet[str]]:
    """The CVE IDs of the catalog, from the cache when it is less than a day old, else from CISA. None when
    neither works: the caller warns."""
    now = time.time() if now is None else now
    path = cache / "kev.json"
    try:
        fresh = now - path.stat().st_mtime < MAX_AGE
    except OSError:
        fresh = False
    if not fresh:
        try:
            cache.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        tmp = cache / "kev.download"
        r = proc.run(["curl", "-fsS", "--max-time", "60", "--retry", "3", "--retry-delay", "5", "--retry-all-errors",
                      "-o", str(tmp), URL], stdout=proc.DEVNULL, stderr=proc.CAPTURE)
        try:
            if r.ok and cves(tmp.read_text(encoding="utf-8")):
                os.replace(tmp, path)
        except OSError:
            pass
    try:
        found = cves(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    return found or None
