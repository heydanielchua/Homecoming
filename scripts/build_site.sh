#!/usr/bin/env bash
#
# Build the whole site: the page, then the data beside it.
#
# For a design change you almost certainly want `scripts/publish.sh` instead,
# which rebuilds only the page and takes seconds. This rebuilds everything,
# which means reading the whole database — about three quarters of an hour.
set -euo pipefail

cd "$(dirname "$0")/.."

OUT="${1:-dist}"

echo "==> page"
# Vite is configured not to empty its output directory, because the data
# lives there too. The page's own files are cleaned here, so a renamed asset
# does not leave its predecessor behind.
rm -rf "$OUT/assets"
rm -f "$OUT/index.html" "$OUT/icon.svg"
(cd web && npm run build)

echo "==> data"
python3 -m nhf.cli site --out "$OUT/data" "${@:2}"

echo
echo "built $OUT:"
du -sh "$OUT"
find "$OUT" -name '*.json' | wc -l | xargs echo "  json files:"
echo
echo "Look at it:      python3 -m http.server --directory $OUT 8765"
echo "Publish it:      ./scripts/publish.sh"
