#!/usr/bin/env bash
# Start this checkout: the backend and the frontend, on the ports you give.
# Ctrl-C stops both. The data (claims, uploads) stays in this checkout.
#   scripts/dev.sh              backend 8000, frontend 5173 (your computer)
#   scripts/dev.sh 8001 5174    on the VM, next to the demo (docs/DEPLOY.md)
# The mode and the key come from .env at the root, if the file is there
# (CLAIMS_MODE, CLAIMS_READER, NVIDIA_KEY). No .env: `local` mode, no key.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
back="${1:-8000}"
front="${2:-5173}"

env_file=()
[[ -f "$root/.env" ]] && env_file=(--env-file ../.env)
[[ -d "$root/frontend/node_modules" ]] || (cd "$root/frontend" && npm ci)

trap 'kill 0' EXIT # the two servers stop with this script
# BACKEND_PORT must be the port of uvicorn (the sandbox calls the backend on it).
# A variable set here wins over the same name in .env.
(cd "$root/backend" && BACKEND_PORT="$back" uv run uvicorn app.main:app \
  ${env_file[@]+"${env_file[@]}"} --host 127.0.0.1 --port "$back") &
(cd "$root/frontend" && BACKEND_URL="http://localhost:$back" npm run dev -- \
  --port "$front" --strictPort) &

echo "Open http://localhost:$front (backend: http://localhost:$back/health)."
echo "On the VM, from your computer first: brev port-forward claims-nvidia -p $front:$front"
wait
