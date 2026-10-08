#!/usr/bin/env bash
# Build a shareable tarball of the working tree: dist/smartpanel-<version>.tar.gz.
# Takes every tracked + untracked-but-not-ignored file, so uncommitted work is
# included and .venv / caches / dist are not.
set -euo pipefail

REPO_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "$REPO_DIR"

VERSION="$(sed -nE 's/^version = "([^"]+)"/\1/p' pyproject.toml)"
NAME="smartpanel-${VERSION}"
mkdir -p dist
OUT="dist/${NAME}.tar.gz"

git ls-files -co --exclude-standard -z \
  | tar --null -T - --transform "s|^|${NAME}/|" -czf "$OUT"

echo "$OUT ($(du -h "$OUT" | cut -f1), $(tar -tzf "$OUT" | wc -l) files)"
