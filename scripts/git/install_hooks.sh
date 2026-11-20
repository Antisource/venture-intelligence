#!/bin/sh
# Point git at the versioned hooks in .githooks/ (commit-msg and pre-push guards).
set -eu
root=$(git rev-parse --show-toplevel)
chmod +x "$root/.githooks/"* 2>/dev/null || true
git -C "$root" config core.hooksPath .githooks
echo "hooks installed: core.hooksPath=.githooks"
