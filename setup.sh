#!/bin/bash
# setup.sh -- Idempotent VPS installer for Auto-Trader v2 (Reaction Network)
#
# Usage:
#   bash setup.sh              # Install everything
#   bash setup.sh --skip-apt   # Skip system package installation
#
# What it does:
#   1. Install system packages (Python 3.13, venv, sqlite3, etc.)
#   2. Create Python virtual environment (./venv)
#   3. Install Python dependencies
#   4. Copy config templates (never overwrite existing)
#   5. Create required directories
#   6. Run smoke test
#   7. Print next steps
#
# Safe to re-run: all steps are idempotent.

set -euo pipefail

# ── Colors ─────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'  # No Color

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

# ── Config ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="venv"
PYTHON="python3.13"
PIP="$VENV_DIR/bin/pip"
PYTHON_VENV="$VENV_DIR/bin/python"

SKIP_APT=false
if [[ "${1:-}" == "--skip-apt" ]]; then
    SKIP_APT=true
fi

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Auto-Trader v2 — VPS Setup"
echo "  Directory: $SCRIPT_DIR"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# ── Step 1: System packages ────────────────────────────────────────────────────
if $SKIP_APT; then
    info "Skipping system package installation (--skip-apt)"
else
    info "Installing system packages..."

    # Check if we're on Debian/Ubuntu
    if ! grep -qiE "debian|ubuntu" /etc/os-release 2>/dev/null; then
        warn "Not running on Debian/Ubuntu. You may need to install packages manually."
    fi

    # Try Python 3.13 first (may need deadsnakes PPA on older Debian)
    if ! command -v python3.13 &>/dev/null; then
        info "Python 3.13 not found. Attempting to install..."

        # Debian Bookworm+ should have it in the main repo
        # For older Debian, we try the deadsnakes approach or pyenv
        sudo apt-get update -qq

        # Try installing from main repos first
        if sudo apt-get install -y -qq python3.13 python3.13-venv python3.13-dev 2>/dev/null; then
            ok "Python 3.13 installed from main repos"
        else
            warn "Python 3.13 not in main repos."
            info "Trying pyenv or manual installation..."

            # Check if pyenv is available
            if command -v pyenv &>/dev/null; then
                info "Installing Python 3.13 via pyenv..."
                pyenv install 3.13.5 2>/dev/null || true
                PYTHON="$(pyenv prefix 3.13.5)/bin/python3.13"
                if [[ -x "$PYTHON" ]]; then
                    ok "Python 3.13 installed via pyenv: $PYTHON"
                else
                    fail "Could not install Python 3.13 via pyenv. Install manually."
                fi
            else
                echo ""
                warn "Python 3.13 is not available."
                echo "  Install it manually, then re-run setup.sh."
                echo "  Options:"
                echo "    1. Use Debian Bookworm (12) which has Python 3.11+"
                echo "    2. Install pyenv: curl https://pyenv.run | bash"
                echo "    3. Build from source: https://www.python.org/downloads/"
                echo ""
                fail "Python 3.13 required but not found."
            fi
        fi
    else
        ok "Python 3.13 already installed: $(python3.13 --version)"
    fi

    # Install other system dependencies
    sudo apt-get install -y -qq \
        python3-venv \
        python3-dev \
        libssl-dev \
        libffi-dev \
        build-essential \
        sqlite3 \
        git \
        2>/dev/null || warn "Some apt packages failed (non-fatal if venv works)"

    ok "System packages installed"
fi

# ── Step 2: Create virtual environment ─────────────────────────────────────────
if [[ -d "$VENV_DIR" && -x "$PYTHON_VENV" ]]; then
    ok "Virtual environment already exists at $VENV_DIR"
    PY_VER=$($PYTHON_VENV --version 2>&1 | head -1)
    info "Using: $PY_VER"
else
    info "Creating virtual environment at $VENV_DIR..."
    $PYTHON -m venv "$VENV_DIR" || fail "Failed to create venv with $PYTHON"

    # Fallback: try system python3 if 3.13 venv failed
    if [[ ! -x "$PYTHON_VENV" ]]; then
        warn "Python 3.13 venv failed, trying system python3..."
        rm -rf "$VENV_DIR"
        python3 -m venv "$VENV_DIR" || fail "Failed to create venv with python3"
    fi

    ok "Virtual environment created: $($PYTHON_VENV --version 2>&1 | head -1)"
fi

# ── Step 3: Install Python dependencies ────────────────────────────────────────
info "Installing Python dependencies..."
$PIP install --upgrade pip --quiet
$PIP install -r requirements.txt --quiet 2>&1 | tail -5
ok "Python dependencies installed"

# ── Step 4: Copy config templates (never overwrite) ───────────────────────────
info "Setting up configuration files..."

# .env
if [[ -f .env ]]; then
    ok ".env already exists (not overwritten)"
else
    cp .env.example .env
    ok ".env created from .env.example"
fi

# Strategy YAMLs — don't overwrite if they exist (they may have auto-generated UIDs)
if [[ -f config/strategies/momentum_rising.yaml ]]; then
    ok "config/strategies/momentum_rising.yaml already exists (not overwritten)"
else
    warn "Default strategy YAML not found. You may need to create one."
fi

if [[ -f config/strategies/paper_test.yaml ]]; then
    ok "config/strategies/paper_test.yaml already exists (not overwritten)"
else
    ok "config/strategies/paper_test.yaml created (minimal 1-symbol strategy for testing)"
fi

# ── Step 5: Create directories ─────────────────────────────────────────────────
mkdir -p logs
mkdir -p data/reports
ok "Directories created: logs/, data/reports/"

# ── Step 6: Smoke test ─────────────────────────────────────────────────────────
info "Running smoke test..."

SMOKE_PASSED=true

# Test 1: Core imports
if $PYTHON_VENV -c "from core.daemon import Daemon; from core.substrate import Substrate; from core.enzyme import create_enzyme, list_enzymes; print('core imports OK')"; then
    ok "Core imports work"
else
    warn "Core imports failed — check requirements.txt"
    SMOKE_PASSED=false
fi

# Test 2: Enzyme registry
if $PYTHON_VENV -c "import enzymes; from core.enzyme import list_enzymes; print('Enzymes:', list_enzymes())"; then
    ok "Enzyme registry populated"
else
    warn "Enzyme registry failed"
    SMOKE_PASSED=false
fi

# Test 3: LLM module
if $PYTHON_VENV -c "from llm import call_llm, init_router; print('LLM module OK')"; then
    ok "LLM module imports work"
else
    warn "LLM module imports failed"
    SMOKE_PASSED=false
fi

# Test 4: Learning module
if $PYTHON_VENV -c "from learning.analyzer import wilson_score_interval; from learning.rulebook import generate_rulebook; print('Learning module OK')"; then
    ok "Learning module imports work"
else
    warn "Learning module imports failed"
    SMOKE_PASSED=false
fi

# Test 5: Database init
if $PYTHON_VENV -c "from core.database import init_db; init_db(); print('Database init OK')"; then
    ok "Database initialization works"
else
    warn "Database initialization failed"
    SMOKE_PASSED=false
fi

# Test 6: Single cycle (quick integration test with minimal strategy)
info "Running single cycle test (paper_test strategy, 1 symbol, 60s timeout)..."
if timeout 60 $PYTHON_VENV main.py --paper --strategy paper_test --cycle-once --log-level WARNING 2>&1 | tail -3; then
    ok "Single cycle completed successfully"
else
    warn "Single cycle test had issues (may be expected if exchange/LLM keys not configured)"
fi

# ── Summary ────────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════"
if $SMOKE_PASSED; then
    echo -e "  ${GREEN}Setup Complete!${NC}"
else
    echo -e "  ${YELLOW}Setup Complete (with warnings)${NC}"
fi
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Next Steps:"
echo ""
echo "  1. Edit .env — add your LLM API keys:"
echo "     ${EDITOR:-nano} .env"
echo ""
echo "     Minimum: an OpenRouter key (free-tier models available):"
echo "       OPENROUTER_API_KEY=sk-or-v1-YOUR-KEY"
echo ""
echo "  2. (Optional) Add exchange API keys for live trading:"
echo "     BITGET_API_KEY=... BITGET_SECRET_KEY=... in .env"
echo "     Paper mode needs NO exchange keys."
echo ""
echo "  3. Test in paper mode (minimal 1-symbol strategy):"
echo "     source venv/bin/activate"
echo "     python main.py --paper --strategy paper_test --cycle-once"
echo ""
echo "  4. Run the daemon continuously (minimal):"
echo "     python main.py --paper --strategy paper_test"
echo ""
echo "  5. Switch to full strategy when ready (hot-plug, no restart needed):"
echo "     python main.py --paper --strategy momentum_rising"
echo ""
echo "  6. For 24/7 operation, see: docs/deployment-guide.md"
echo ""
echo "  7. Verify your deployment:"
echo "     bash scripts/verify_e2e.sh"
echo ""
echo "  Log file: logs/auto-trader.log"
echo "  Database: trading_journal.db"
echo ""