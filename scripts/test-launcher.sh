#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMP_DATA="$(mktemp -d "${TMPDIR:-/tmp}/proposal-launcher-test.XXXXXX")"
cleanup() { rm -rf "$TEMP_DATA"; }
trap cleanup EXIT

bash -n "$ROOT/启动提案分析工具.command"
PROPOSAL_TOOL_DATA_DIR="$TEMP_DATA" PROPOSAL_NO_OPEN=1 PROPOSAL_EXIT_AFTER_HEALTH=1 \
  "$ROOT/启动提案分析工具.command"

