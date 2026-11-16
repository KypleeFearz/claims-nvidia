#!/usr/bin/env bash
# Every check of the CI (.github/workflows/ci.yml), in the same order. Run it before
# you push. It stops at the first check that fails. `local` mode: no key, no model.
#   scripts/check.sh            backend and frontend
#   scripts/check.sh backend    the backend only
#   scripts/check.sh frontend   the frontend only
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
what="${1:-all}"

step() { printf '\n== %s\n' "$*"; }

if [[ "$what" == all || "$what" == backend ]]; then
  cd "$root/backend"
  step "backend: lint"
  uv run ruff check . ../scripts
  uv run ruff format --check . ../scripts
  step "backend: types"
  uv run mypy .
  step "backend: tests"
  uv run pytest -q
fi
if [[ "$what" == all || "$what" == frontend ]]; then
  cd "$root/frontend"
  [[ -d node_modules ]] || npm ci
  step "frontend: lint"
  npm run lint
  step "frontend: types"
  npm run typecheck
  step "frontend: tests"
  npm test
  step "frontend: build"
  npm run build
fi
printf '\nAll checks passed (%s). The CI runs the same checks on your pull request.\n' "$what"
