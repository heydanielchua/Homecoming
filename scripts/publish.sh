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
# Either way it then waits for Pages to serve the build it just pushed, and
# reports a failure if it does not. A push is not a deployment: Pages builds
# afterwards, on its own schedule, and the only honest way to say something is
# live is to fetch it and find this build's bundle name in it.
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
# Where Pages serves the public repository, so a publish can check that what
# it pushed is actually what a visitor gets. Override for a different repo.
SITE_URL="${SITE_URL-https://heydanielchua.github.io/homecoming-site/}"
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

# The last chance to get this right, and the one that matters: whatever built
# the directory, what gets pushed must carry it or Pages will put Jekyll in
# front of thirteen thousand files.
touch "$DIST/.nojekyll"

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

# What the live page must be serving once this publish has landed. Pages
# builds and deploys on its own schedule after the push, so the push
# succeeding says nothing about what a visitor is getting — and the hashed
# bundle name is the one thing that distinguishes this build from the last.
WANT="$(grep -o 'assets/index-[A-Za-z0-9_-]*\.js' "$GIT_WORK_TREE/index.html" | head -1 || true)"

echo "==> pushing to $(git remote get-url public) ($BRANCH)"
PUSHED=0
for attempt in 1 2 3 4; do
    if git push -f public "HEAD:$BRANCH"; then PUSHED=1; break; fi
    wait=$((2 ** attempt))
    echo "push failed; retrying in ${wait}s" >&2
    sleep "$wait"
done
if [ "$PUSHED" = 0 ]; then
    echo "push failed after 4 attempts" >&2
    exit 1
fi

# A push is not a deployment. Wait for the live page to serve this build
# before saying it is published, because saying so early is the one failure
# here that cannot be seen from the terminal.
if [ -z "$SITE_URL" ] || [ -z "$WANT" ]; then
    echo
    if [ -z "$SITE_URL" ]; then
        echo "pushed. Not waiting for the deployment: no SITE_URL to poll."
    else
        echo "pushed. Not waiting for the deployment: no bundle name in index.html."
    fi
    echo "Enable Pages on the public repository from branch $BRANCH, / (root),"
    echo "if it is not on already."
    exit 0
fi

echo "==> waiting for $SITE_URL to serve ${WANT##*/}"
BODY="$(mktemp)"
trap 'rm -f "$BODY"' EXIT
reached=0
got=""
for i in $(seq 1 45); do
    if curl -fsS --max-time 20 -o "$BODY" "$SITE_URL?cb=$i" 2>/dev/null; then
        reached=1
        got="$(grep -o 'assets/index-[A-Za-z0-9_-]*\.js' "$BODY" | head -1 || true)"
        if [ "$got" = "$WANT" ]; then
            echo
            echo "published and live at $SITE_URL"
            exit 0
        fi
    elif [ "$reached" = 0 ] && [ "$i" -ge 3 ]; then
        # Three attempts and the host never answered at all. That is a
        # network that cannot reach it — a proxy, a firewall, no DNS — and
        # not a deployment that failed, so it must not be reported as one.
        # Nothing about the publish is known to be wrong; it simply has not
        # been checked from here.
        echo
        echo "pushed, but $SITE_URL cannot be reached from this machine, so"
        echo "the deployment was not checked. The push itself succeeded."
        echo "Check the pages build and deployment run on the public"
        echo "repository, or open the page yourself."
        exit 0
    fi
    sleep 20
done
echo >&2
if [ "$reached" = 0 ]; then
    echo "pushed, but $SITE_URL never answered, so the deployment was not" >&2
    echo "checked. The push itself succeeded." >&2
    exit 0
fi
echo "pushed, but $SITE_URL is still serving ${got:-no recognisable build}" >&2
echo "after 15 minutes, not ${WANT##*/}. The push landed; the deployment" >&2
echo "did not. Check the pages build and deployment run on the public" >&2
echo "repository." >&2
exit 1
