"""Automatically finalize the dashboard after the surveillance watcher finishes.

Python imports ``sitecustomize`` automatically at startup when it is available on
``sys.path``. Cloudflare runs ``python watch_sources.py`` and then copies
``reports/dashboard.html`` into ``dist/index.html``. Registering this small exit
hook ensures the generated dashboard is upgraded by ``enhance_dashboard.py``
before Cloudflare performs that copy.
"""

from __future__ import annotations

import atexit
import os
import sys
from pathlib import Path


def _is_watcher_run() -> bool:
    if not sys.argv:
        return False
    return Path(sys.argv[0]).name == "watch_sources.py"


def _finalize_dashboard() -> None:
    dashboard = Path("reports/dashboard.html")
    enhancer = Path("enhance_dashboard.py")
    if not dashboard.exists() or not enhancer.exists():
        return

    try:
        from enhance_dashboard import main as enhance_main

        result = enhance_main()
        if result not in (None, 0):
            print(f"Dashboard enhancement returned status {result}.", file=sys.stderr)
    except Exception as exc:  # Keep watcher completion intact while surfacing the error.
        print(f"Dashboard enhancement failed: {exc}", file=sys.stderr)


if _is_watcher_run() and os.environ.get("SUPPLY_INTEL_SKIP_ENHANCEMENT") != "1":
    atexit.register(_finalize_dashboard)
