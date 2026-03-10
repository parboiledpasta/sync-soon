#!/usr/bin/env bash
# =============================================================================
# Semabridge Phase 1 — Quick-start Local Development Environment
# =============================================================================
# Prerequisites:
#   • Docker + Docker Compose
#   • Python 3.10+  (UV package manager recommended)
#   • This repo cloned locally
#
# What this script does:
#   1. Starts Temporal dev server + Ray head via docker compose.
#   2. Installs Python dependencies (including new Phase 1 extras).
#   3. Starts a Semabridge Temporal worker on the host.
#
# Usage:
#   chmod +x scripts/quickstart_dev.sh
#   ./scripts/quickstart_dev.sh
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

echo "=== Semabridge Phase 1 — Quick-start ==="
echo ""

# ---- 1. Docker services ---------------------------------------------------
echo "[1/3] Starting Temporal + Ray via docker compose …"
docker compose up -d temporal ray-head
echo "  Temporal Frontend : localhost:7233"
echo "  Temporal UI       : localhost:8080"
echo "  Ray Dashboard     : localhost:8265"
echo ""

# Wait for Temporal health
echo "  Waiting for Temporal to become healthy …"
for i in $(seq 1 30); do
    if docker compose exec -T temporal tctl cluster health 2>/dev/null | grep -q SERVING; then
        echo "  ✓ Temporal is healthy."
        break
    fi
    sleep 2
done
echo ""

# ---- 2. Python dependencies -----------------------------------------------
echo "[2/3] Installing Python dependencies …"
if command -v uv &>/dev/null; then
    uv pip install -e ".[dev,distributed]"
else
    pip install -e ".[dev,distributed]"
fi
echo ""

# ---- 3. Start Temporal worker ---------------------------------------------
echo "[3/3] Starting Semabridge Temporal worker …"
echo "  Task queue : semabridge-sync"
echo "  Press Ctrl-C to stop."
echo ""

export TEMPORAL_HOST="localhost:7233"
export RAY_ADDRESS="ray://localhost:6379"
export SEMABRIDGE_ORCHESTRATOR="temporal"

python -m semabridge.orchestration.temporal.worker
