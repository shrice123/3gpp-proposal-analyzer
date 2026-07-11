#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

npm run test:backend
npm run typecheck
npm run lint
npm run build
node --test tests/rendered-html.test.mjs
npm run build:renderer
npm run build:backend
npm run test:launcher
