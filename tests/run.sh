#! /usr/bin/env bash
# Filename:    run.sh
# Description: Single gate for the DahuaEvents contract tests. Exit 0 only if
#              everything passes. CI runs THIS rather than restating the steps in
#              YAML — two copies of a gate drift, and the half that drifts is always
#              the one nobody watches.
# Author:      CliveS & Claude Opus 5
# Date:        01-09-2026
# Version:     1.0
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.."
BUNDLE="DahuaEvents.indigoPlugin/Contents/Server Plugin"

echo "== unit tests (capability probe, dialog XML, version consistency) =="
python3 -m pytest tests -q

echo
echo "== python syntax =="
# -B alone does NOT stop py_compile writing bytecode — it only suppresses the
# implicit write on import. The sweep at the end is the part that works.
PYTHONDONTWRITEBYTECODE=1 # Glob rather than a list: a hand-maintained list silently stops covering the
# newest file, which is exactly what happened when dahua_stream.py was added.
python3 -B -m py_compile "$BUNDLE"/*.py
echo "  ok"

echo
echo "== XML well-formed =="
for f in "$BUNDLE"/*.xml "DahuaEvents.indigoPlugin/Contents/Info.plist"; do
    python3 -c "import xml.dom.minidom,sys; xml.dom.minidom.parse(sys.argv[1])" "$f"
    echo "  ok  $(basename "$f")"
done

echo
echo "== lint (errors only) =="
# NOT `ruff check . && echo ok` — `set -e` does not fire on the left-hand side
# of `&&`, so that form lets a lint failure through and the script still exits 0.
# Inherited from an earlier repo, where it had been silently passing.
python3 -m ruff check .
echo "  ok"

echo
# Never ship __pycache__ inside the bundle.
find DahuaEvents.indigoPlugin -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

echo "All DahuaEvents contract tests passed."
