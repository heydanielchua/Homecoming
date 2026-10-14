#!/usr/bin/env bash
#
# Publish the site to the public repository that serves GitHub Pages.
#
# This repository stays private, because it holds docs/PRD.md and
# config/criteria.yml. Only `dist/` — the page and the data derived from
# public government data — goes to the public one.
#
# Two kinds of publish, because they cost very different amounts:
#
#   ./scripts/publish.sh            rebuild the page and push it.  Seconds.
#   ./scripts/publish.sh --data     rebuild the data too.          ~45 minutes.
#
# The page is 500 KB and changes every time a design is tweaked. The data is
# 123 MB across 12,000 files and changes once a month. Keeping them apart is
# what makes iterating on the design bearable: a design change re-uploads
# three files, not a hundred and fifty megabytes.
#
# HISTORY IS NOT KEPT. Each publish force-pushes a single orphan commit, so
# the public repository always holds exactly one. That is deliberate: with
# ordinary history a monthly data refresh would add 123 MB of objects for
# ever, and git never forgets, so the repository would pass GitHub's one
# gigabyte guideline inside a year. The public repository is a deployment
# artifact, not a record — the record is this repository, which has the code
# that produces it.
#
# Unchanged files are still cheap to re-publish. The orphan commit names the
# same blobs, and the remote already has them, so only what actually changed
# is uploaded.
set -euo pipefail

cd "$(dirname "$0")/.."

DIST="dist"
GIT_STATE=".publish.git"          # gitignored; the publisher's own history
BRANCH="main"
BUILD_DATA=0
BUILD_PAGE=1

usage() {
    cat <<'USAGE'
usage: scripts/publish.sh [--data] [--no-build] [--dry-run] [--remote URL]

  (no flags)   rebuild the page, then publish
  --data       rebuild the data as well (slow: reads the whole database)
  --no-build   publish what is already in dist/
  --dry-run    build and stage, but do not push
  --remote URL the public repository, if the `public` remote is not set
USAGE
}

DRY_RUN=0
REMOTE_URL=""
while [ $# -gt 0 ]; do
    case "$1" in
        --data)      BUILD_DATA=1 ;;
        --no-build)  BUILD_PAGE=0; BUILD_DATA=0 ;;
        --dry-run)   DRY_RUN=1 ;;
        --remote)    shift; REMOTE_URL="${1:-}" ;;
        -h|--help)   usage; exit 0 ;;
        *)           echo "unknown option: $1" >&2; usage; exit 2 ;;
    esac
    shift
done

# --- the page ------------------------------------------------------------
if [ "$BUILD_PAGE" = 1 ]; then
    echo "==> page"
    # Vite does not empty the output directory, because the data lives there
    # and a page build must not throw it away. The page's own files are
    # cleaned here instead, so a renamed asset does not leave its predecessor
    # behind to be published for ever.
    rm -rf "$DIST/assets"
    rm -f "$DIST/index.html" "$DIST/icon.svg"
    (cd web && npm run build)
fi

if [ "$BUILD_DATA" = 1 ]; then
    echo "==> data"
    python3 -m nhf.cli site --out "$DIST/data"
fi

if [ ! -f "$DIST/index.html" ]; then
    echo "no page in $DIST — run without --no-build" >&2
    exit 1
fi
if [ ! -f "$DIST/data/meta.json" ]; then
    echo "no data in $DIST/data — run with --data, or nhf site --out $DIST/data" >&2
    exit 1
fi

# The same check the build runs, in case dist/ was assembled by hand.
python3 - <<'PY'
import sys
from nhf.site import verify
problems = verify("dist/data")
for problem in problems:
    print(f"[!!] {problem}", file=sys.stderr)
if problems:
    print("refusing to publish", file=sys.stderr)
    sys.exit(1)
PY

# --- the push ------------------------------------------------------------
export GIT_DIR="$GIT_STATE"
export GIT_WORK_TREE="$DIST"

if [ ! -d "$GIT_STATE" ]; then
    echo "==> first publish: setting up $GIT_STATE"
    git init -q
    git config user.name "$(cd "$GIT_WORK_TREE/.." && GIT_DIR=.git git config user.name)"
    git config user.email "$(cd "$GIT_WORK_TREE/.." && GIT_DIR=.git git config user.email)"
fi

if [ -n "$REMOTE_URL" ]; then
    git remote remove public 2>/dev/null || true
    git remote add public "$REMOTE_URL"
fi

# A dry run is allowed without a remote: its whole point is to check the build
# and the staging before there is anywhere to push to.
if ! git remote get-url public >/dev/null 2>&1 && [ "$DRY_RUN" = 0 ]; then
    cat >&2 <<'MISSING'
No `public` remote is set, so there is nowhere to publish to.

Create the public repository first, then either pass it here:

    ./scripts/publish.sh --remote git@github.com:<you>/<public-repo>.git

or set it once:

    GIT_DIR=.publish.git git remote add public git@github.com:<you>/<public-repo>.git
MISSING
    exit 1
fi

# A fresh orphan each time: one commit, no history, see the note at the top.
git checkout -q --orphan "publish-$$" 2>/dev/null || git checkout -q --orphan publish-tmp
git add -A

BUILT_AT="$(python3 -c 'import json;print(json.load(open("dist/data/manifest.json"))["as_of"]["month"])')"
REPORTS="$(python3 -c 'import json;print(json.load(open("dist/data/manifest.json"))["counts"]["reports"])')"
SOURCE_REV="$(cd "$GIT_WORK_TREE/.." && GIT_DIR=.git git rev-parse --short HEAD)"

git commit -q -m "Listing Check — sales to ${BUILT_AT}, ${REPORTS} blocks

Built from the private repository at ${SOURCE_REV}.
Everything here is derived from public government data on data.gov.sg.
Published as a single commit; the history is in the repository that built it."

echo "==> staged $(git ls-files | wc -l | tr -d ' ') files"

if [ "$DRY_RUN" = 1 ]; then
    echo "dry run: not pushing"
    git remote get-url public >/dev/null 2>&1 \
        || echo "(no \`public\` remote yet, so a real publish would stop here)"
    exit 0
fi

echo "==> pushing to $(git remote get-url public) ($BRANCH)"
for attempt in 1 2 3 4; do
    if git push -f public "HEAD:$BRANCH"; then
        echo
        echo "published."
        echo "Enable Pages on the public repository from branch $BRANCH, / (root),"
        echo "if it is not on already."
        exit 0
    fi
    wait=$((2 ** attempt))
    echo "push failed; retrying in ${wait}s" >&2
    sleep "$wait"
done
echo "push failed after 4 attempts" >&2
exit 1
