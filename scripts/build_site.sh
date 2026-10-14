#!/usr/bin/env bash
#
# Build the whole site: the page, then the data beside it.
#
# The order matters and is the only subtlety here. Vite empties its output
# directory before writing, so the data has to go in afterwards or it would be
# deleted by the next page build. `nhf site` writes into dist/data, which is
# where the page looks for it — relatively, so the same build serves from a
# domain root or from a project path on GitHub Pages.
set -euo pipefail

cd "$(dirname "$0")/.."

OUT="${1:-dist}"

echo "==> page"
(cd web && npm run build)

echo "==> data"
python3 -m nhf.cli site --out "$OUT/data" "${@:2}"

echo
echo "built $OUT:"
du -sh "$OUT"
find "$OUT" -name '*.json' | wc -l | xargs echo "  json files:"
echo
echo "Serve it with any static host. To try it here:"
echo "  python3 -m http.server --directory $OUT 8765"
