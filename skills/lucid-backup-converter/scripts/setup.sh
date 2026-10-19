#!/usr/bin/env bash
# One-time setup for the Lucid backup converter. Safe to rerun; it skips what's already done.
#
# Usage: bash setup.sh [tools-dir]        (default: ~/.lucid-convert)
#
# Installs into tools-dir:
#   drawio/      sparse checkout of github.com/jgraph/drawio (only src/main/webapp, ~110 MB),
#                pinned to a tested commit. Holds LucidImporter and the export page.
#   node_modules Playwright (Node library that drives a headless browser)
# Needs: git, Node.js 18+, npm, Python 3, and Google Chrome (or it downloads Playwright's Chromium).
set -euo pipefail

TOOLS="${1:-$HOME/.lucid-convert}"
# draw.io commit this skill was tested with (2026-10-18). Override with DRAWIO_REF=<sha or tag>.
DRAWIO_REF="${DRAWIO_REF:-17b367b91dbab91804f852645788d7ffb46dcb79}"
PLAYWRIGHT_VERSION="${PLAYWRIGHT_VERSION:-1.49.1}"

say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

say "Checking prerequisites"
command -v git     >/dev/null || fail "git is not installed (macOS: xcode-select --install; Linux: apt install git)"
command -v node    >/dev/null || fail "Node.js is not installed (https://nodejs.org, or: brew install node)"
command -v npm     >/dev/null || fail "npm is not installed (it ships with Node.js)"
command -v python3 >/dev/null || fail "Python 3 is not installed (macOS: brew install python; Linux: apt install python3)"
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[ "$NODE_MAJOR" -ge 18 ] || fail "Node.js 18 or newer is needed (found $(node -v))"
echo "git $(git --version | awk '{print $3}'), node $(node -v), python $(python3 -V | awk '{print $2}')"

mkdir -p "$TOOLS"
cd "$TOOLS"

say "Fetching draw.io webapp ($DRAWIO_REF) into $TOOLS/drawio"
if [ -f drawio/src/main/webapp/js/extensions.min.js ] && [ "$(git -C drawio rev-parse HEAD 2>/dev/null)" = "$DRAWIO_REF" ]; then
  echo "already present"
else
  rm -rf drawio
  git init -q drawio
  git -C drawio remote add origin https://github.com/jgraph/drawio.git
  git -C drawio sparse-checkout set src/main/webapp
  git -C drawio fetch -q --depth 1 --filter=blob:none origin "$DRAWIO_REF"
  git -C drawio checkout -q FETCH_HEAD
fi
for f in export3.html js/app.min.js js/extensions.min.js js/export.js; do
  [ -f "drawio/src/main/webapp/$f" ] || fail "draw.io checkout is missing $f"
done
grep -q LucidImporter drawio/src/main/webapp/js/extensions.min.js || fail "this draw.io version has no LucidImporter"

say "Installing Playwright $PLAYWRIGHT_VERSION"
[ -f package.json ] || echo '{ "name": "lucid-convert-tools", "private": true }' > package.json
if [ "$(node -p "try{require('./node_modules/playwright/package.json').version}catch(e){''}")" = "$PLAYWRIGHT_VERSION" ]; then
  echo "already installed"
else
  npm install --silent --no-audit --no-fund "playwright@$PLAYWRIGHT_VERSION"
fi

say "Checking for a browser"
if node -e "require('playwright').chromium.launch({channel:'chrome'}).then(b=>b.close()).catch(()=>process.exit(1))"; then
  echo "using installed Google Chrome"
elif node -e "require('playwright').chromium.launch().then(b=>b.close()).catch(()=>process.exit(1))"; then
  echo "using Playwright's Chromium"
else
  echo "Google Chrome not found; downloading Playwright's Chromium (~150 MB)"
  npx --yes playwright install chromium
fi

say "Setup complete. Tools folder: $TOOLS"
