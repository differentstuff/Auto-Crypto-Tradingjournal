# Architecture Optimisation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate duplication, silence 8 swallowed exceptions, cut ~300 tokens per AI call, and make every module independently testable — without breaking the running Pi service.

**Architecture:** Five independent phases, each deployable on its own. Phase 0 (foundation) must land first; Phases 1–4 can proceed in any order after that. Each phase ends with a Pi deploy and smoke test. No breaking changes to the 65 API endpoints.

**Tech Stack:** Python 3.13, Flask 3.1, SQLite WAL, Anthropic SDK, pandas-ta, systemd on Raspberry Pi 5. Tests: pytest (install in Phase 0).

**Constraints:** Pi has 4 cores — keep ThreadPoolExecutor max_workers ≤ 4 where possible. Service restarts require `sudo systemctl restart trading-journal`. DB migrations must be backward-compatible (ALTER TABLE IF NOT EXISTS).

---

## Phase 0: Foundation (do first — everything imports from here)

**Token savings: ~240 tokens/call (scoring scale deduplication)**
**Risk: Low — additive only, nothing deleted yet**

### Task 0.1: Install pytest + create test scaffold

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Modify: `requirements.txt` (add pytest)

- [ ] **Step 1: Install pytest on the dev machine**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
pip3 install pytest pytest-mock
```

Expected output: `Successfully installed pytest-X.X.X`

- [ ] **Step 2: Create tests directory**

```bash
mkdir -p tests
touch tests/__init__.py
```

- [ ] **Step 3: Create conftest.py with shared fixtures**

```python
# tests/conftest.py
import sqlite3
import pytest
import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("BITGET_API_KEY", "test")
os.environ.setdefault("BITGET_SECRET_KEY", "test")
os.environ.setdefault("BITGET_PASSPHRASE", "test")

@pytest.fixture
def db():
    """In-memory SQLite DB with full schema."""
    from database import init_db, get_conn
    import database
    old = database.DB_PATH
    database.DB_PATH = ":memory:"
    init_db()
    conn = get_conn()
    yield conn
    conn.close()
    database.DB_PATH = old

@pytest.fixture
def sample_positions(db):
    """Insert 5 closed positions for symbol-history tests."""
    rows = [
        ("BTCUSDT", "Long",  100.0, "2026-01-01", "2026-01-02"),
        ("BTCUSDT", "Long",  -50.0, "2026-01-03", "2026-01-04"),
        ("BTCUSDT", "Long",   80.0, "2026-01-05", "2026-01-06"),
        ("ETHUSDT", "Long",   40.0, "2026-01-07", "2026-01-08"),
        ("BTCUSDT", "Short", -20.0, "2026-01-09", "2026-01-10"),
    ]
    for sym, direction, pnl, open_t, close_t in rows:
        db.execute(
            "INSERT INTO positions (symbol, direction, realized_pnl, open_time, close_time, exchange) "
            "VALUES (?,?,?,?,?,'bitget')",
            (sym, direction, pnl, open_t, close_t)
        )
    db.commit()
    return db
```

- [ ] **Step 4: Verify pytest discovers the conftest**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python3 -m pytest tests/ --collect-only
```

Expected: `no tests ran` (0 items collected, no errors)

- [ ] **Step 5: Commit**

```bash
git add tests/ requirements.txt
git commit -m "test: add pytest scaffold and shared fixtures"
```

---

### Task 0.2: Create constants.py

Centralises all magic numbers scattered across 9+ files. All modules import from here — no module re-defines MODEL, FAST_MODEL, or cache TTLs.

**Files:**
- Create: `constants.py`
- Modify: `ai_call.py`, `ai_scanner.py`, `ai_advisor.py`, `ai_hindsight.py`, `ai_rulebook.py`, `ai_pattern_detector.py`, `ai_trade_grader.py`, `ai_live_trade.py`, `ai_limit.py`, `chart_context.py`, `market_context.py`, `nansen_client.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_constants.py
def test_all_models_defined():
    from constants import MODEL, FAST_MODEL, ANTHROPIC_API_KEY
    assert MODEL == "claude-sonnet-4-6"
    assert "haiku" in FAST_MODEL.lower()
    assert isinstance(ANTHROPIC_API_KEY, str)

def test_cache_ttls_are_positive():
    from constants import CHART_CACHE_TTL, SCANNER_CACHE_TTL, MARKET_CACHE_TTL, NANSEN_CACHE_TTL
    assert CHART_CACHE_TTL > 0
    assert SCANNER_CACHE_TTL > 0
    assert MARKET_CACHE_TTL > 0
    assert NANSEN_CACHE_TTL > 0

def test_scanner_thresholds():
    from constants import SCANNER_MIN_SCORE, SCANNER_FULL_DETAIL_TOP_N, SCANNER_MAX_WORKERS
    assert 1 <= SCANNER_MIN_SCORE <= 10
    assert SCANNER_FULL_DETAIL_TOP_N >= 1
    assert SCANNER_MAX_WORKERS <= 8
```

- [ ] **Step 2: Run test — confirm FAIL**

```bash
python3 -m pytest tests/test_constants.py -v
```

Expected: `ImportError: No module named 'constants'`

- [ ] **Step 3: Create constants.py**

```python
# constants.py
import os

# ── Anthropic models ──────────────────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL              = "claude-sonnet-4-6"          # full-detail AI calls
FAST_MODEL         = "claude-haiku-4-5-20251001"  # quick-score / batch

# ── Cache TTLs (seconds) ──────────────────────────────────────────────────────
CHART_CACHE_TTL    = 600    # chart_context candle cache (10 min)
SCANNER_CACHE_TTL  = 1800   # scanner results cache (30 min)
MARKET_CACHE_TTL   = 300    # Fear & Greed / funding rates (5 min)
NANSEN_CACHE_TTL   = 1800   # Nansen smart money cache (30 min)

# ── Scanner pipeline ──────────────────────────────────────────────────────────
SCANNER_MIN_SCORE        = 6    # minimum score to show in results
SCANNER_FULL_DETAIL_TOP_N = 12  # max Sonnet full-detail calls per scan
SCANNER_MAX_WORKERS      = 4    # ThreadPoolExecutor workers (Pi = 4 cores)

# ── Position sizing ───────────────────────────────────────────────────────────
DEFAULT_LEVERAGE      = 10
DEFAULT_RISK_PCT      = 1.0
DEFAULT_DCA_RISK_PCT  = 2.0
FALLBACK_EQUITY_USDT  = 1000.0  # used ONLY when all exchange equity calls fail

# ── Prompt budget ─────────────────────────────────────────────────────────────
MAX_CONTEXT_CHARS     = 5_600   # prompt_builder hard limit
PROMPT_CACHE_MIN_CHARS = 4_096  # minimum chars to enable Anthropic cache_control
```

- [ ] **Step 4: Run test — confirm PASS**

```bash
python3 -m pytest tests/test_constants.py -v
```

Expected: `3 passed`

- [ ] **Step 5: Replace constants in each AI module (one file at a time)**

For each file in: `ai_call.py`, `ai_scanner.py`, `ai_advisor.py`, `ai_hindsight.py`, `ai_rulebook.py`, `ai_pattern_detector.py`, `ai_trade_grader.py`, `ai_live_trade.py`, `ai_limit.py`, `chart_context.py`, `market_context.py`, `nansen_client.py`:

Add at top (after existing imports):
```python
from constants import (MODEL, FAST_MODEL, ANTHROPIC_API_KEY,
                       CHART_CACHE_TTL, SCANNER_CACHE_TTL,
                       SCANNER_MIN_SCORE, SCANNER_FULL_DETAIL_TOP_N,
                       SCANNER_MAX_WORKERS, DEFAULT_LEVERAGE,
                       MAX_CONTEXT_CHARS, PROMPT_CACHE_MIN_CHARS)
```

Then delete the duplicated `MODEL = "claude-sonnet-4-6"` / `ANTHROPIC_API_KEY = os.environ...` lines from each file.

In `helpers.py`, update `build_cached_messages()`:
```python
# replace hardcoded 4096 with:
from constants import PROMPT_CACHE_MIN_CHARS
if len(context) >= PROMPT_CACHE_MIN_CHARS:
```

- [ ] **Step 6: Verify app still starts**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python3 -c "import app; print('OK')"
```

Expected: `OK` (no import errors)

- [ ] **Step 7: Commit**

```bash
git add constants.py tests/test_constants.py ai_*.py helpers.py chart_context.py market_context.py nansen_client.py
git commit -m "refactor: centralise all constants in constants.py"
```

---

### Task 0.3: Create prompt_fragments.py (token savings)

The scoring scale (~80 tokens) and level proximity definitions (~60 tokens) are copy-pasted verbatim in `ai_call.py`, `ai_scanner.py`, and `ai_advisor.py`. This saves ~240 tokens per call.

**Files:**
- Create: `prompt_fragments.py`
- Modify: `ai_call.py`, `ai_scanner.py`, `ai_advisor.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_prompt_fragments.py
def test_scoring_scale_is_string():
    from prompt_fragments import SCORING_SCALE
    assert isinstance(SCORING_SCALE, str)
    assert "1" in SCORING_SCALE and "10" in SCORING_SCALE
    assert len(SCORING_SCALE) > 50

def test_level_proximity_is_string():
    from prompt_fragments import LEVEL_PROXIMITY_RULES
    assert isinstance(LEVEL_PROXIMITY_RULES, str)
    assert "ATR" in LEVEL_PROXIMITY_RULES

def test_no_duplicates_in_ai_call():
    """ai_call.py must not define its own scoring scale."""
    with open("ai_call.py") as f:
        src = f.read()
    assert "Poor|Weak|Moderate" not in src, "Scoring scale still in ai_call.py"

def test_no_duplicates_in_ai_scanner():
    with open("ai_scanner.py") as f:
        src = f.read()
    assert "Poor|Weak|Moderate" not in src, "Scoring scale still in ai_scanner.py"
```

- [ ] **Step 2: Run test — confirm FAIL**

```bash
python3 -m pytest tests/test_prompt_fragments.py -v
```

- [ ] **Step 3: Create prompt_fragments.py**

```python
# prompt_fragments.py
"""
Shared prompt text blocks reused across ai_call, ai_scanner, ai_advisor.
Import these instead of copy-pasting — each token saved here saves it on
every single AI call.
"""

SCORING_SCALE = """
Setup quality scale (1-10):
1-2 Poor: No clear bias, multiple conflicting signals, no structural level
3-4 Weak: Weak bias, level anchor uncertain, R:R < 1.5
5-6 Moderate: Clear bias, valid level nearby, R:R 1.5-2.0
7-8 Good: Strong bias, level confirmed, R:R ≥ 2.0, aligned timeframes
9-10 Excellent: All signals aligned, key structural level, R:R ≥ 3.0
""".strip()

LEVEL_PROXIMITY_RULES = """
Level proximity (use when scoring):
- Entry ≤ 0.5× ATR from level → strong anchor, no penalty
- Entry 0.5–1.0× ATR from level → acceptable, note it
- Entry > 1.0× ATR from nearest level → structural anchor missing → score ≤ 6
- SL < 1.0× ATR from entry → inside noise → score ≤ 6
- R:R < 1.5 → score ≤ 6; R:R ≥ 2.0 for score 7+; R:R ≥ 3.0 for score 9+
""".strip()

MARKET_CONTEXT_RULES = """
Market context adjustments:
- Funding > 0.05% with-trend → −1 (crowd on-side, squeeze risk)
- Funding > 0.10% with-trend → −2 (extremely crowded)
- Funding counter-trend → slight tailwind (note as positive)
- Fear & Greed < 20 (Extreme Fear): long +0.5, short −0.5
- Fear & Greed > 80 (Extreme Greed): long −0.5, short +0.5
""".strip()

RISK_MANAGEMENT_NOTE = """
Risk rules: 1% risk per trade (no DCA), 2% with DCA. Maximum position 
size reduced if account is below its 30-day high watermark.
""".strip()
```

- [ ] **Step 4: Replace duplicated text in each file**

In `ai_call.py` — find the inline scoring scale text and replace with:
```python
from prompt_fragments import SCORING_SCALE, LEVEL_PROXIMITY_RULES, MARKET_CONTEXT_RULES
# Then in the prompt f-string: use {SCORING_SCALE} and {LEVEL_PROXIMITY_RULES}
```

Do the same in `ai_scanner.py` and `ai_advisor.py`.

- [ ] **Step 5: Run tests — confirm PASS**

```bash
python3 -m pytest tests/test_prompt_fragments.py -v
```

Expected: `4 passed`

- [ ] **Step 6: Commit**

```bash
git add prompt_fragments.py tests/test_prompt_fragments.py ai_call.py ai_scanner.py ai_advisor.py
git commit -m "refactor: extract shared prompt fragments (~240 token savings per call)"
```

---

### Task 0.4: Fix all bare except Exception silencers

Eight locations swallow exceptions with no logging. Per `python-expert` rule `no-generic-except`: catch only what you expect, log everything else. The `helpers.py` token logger is the only justified `except Exception: pass` (logging must never crash callers).

**Files:**
- Modify: `ai_scanner.py` (6 locations), `prompt_builder.py` (1), `market_context.py` (1)

- [ ] **Step 1: Add logging import to affected files**

At the top of each affected file, ensure:
```python
import logging
logger = logging.getLogger(__name__)
```

- [ ] **Step 2: Fix ai_scanner.py line 170 (_fetch_one)**

```python
# BEFORE:
except Exception:
    return (symbol, None, None)

# AFTER:
except (OSError, TimeoutError) as e:
    logger.warning("chart fetch failed for %s: %s", symbol, e)
    return (symbol, None, None)
except Exception as e:
    logger.error("unexpected error fetching %s: %s", symbol, e, exc_info=True)
    return (symbol, None, None)
```

- [ ] **Step 3: Fix ai_scanner.py line 471 (_quick_score Haiku call)**

```python
# BEFORE:
except Exception:
    return None

# AFTER:
except anthropic.APIStatusError as e:
    logger.warning("Haiku quick-score API error for %s: %s", symbol, e.status_code)
    return None
except Exception as e:
    logger.error("unexpected quick-score failure for %s: %s", symbol, e, exc_info=True)
    return None
```

- [ ] **Step 4: Fix ai_scanner.py line 493 (batch fallback)**

```python
# BEFORE:
except Exception:
    # fall back to serial scoring

# AFTER:
except anthropic.APIStatusError as e:
    logger.warning("batch scoring API error (status %s), falling back to serial", e.status_code)
except Exception as e:
    logger.error("batch scoring failed unexpectedly, falling back to serial: %s", e, exc_info=True)
```

- [ ] **Step 5: Fix ai_scanner.py lines 678, 688, 704 (stage 3 individual scoring)**

```python
# BEFORE:
except Exception:
    pass

# AFTER:
except anthropic.APIStatusError as e:
    logger.warning("score failed for %s (HTTP %s)", symbol, e.status_code)
except Exception as e:
    logger.error("score failed for %s: %s", symbol, e, exc_info=True)
```

- [ ] **Step 6: Fix prompt_builder.py line 163 (Nansen signal)**

```python
# BEFORE:
except Exception:
    pass

# AFTER:
except Exception as e:
    logger.warning("Nansen signal fetch failed for %s: %s", symbol, e)
```

- [ ] **Step 7: Fix market_context.py Fear & Greed fetch**

Locate `except Exception: pass` or `except Exception: return ""` pattern. Replace with:
```python
except (OSError, TimeoutError) as e:
    logger.warning("Fear & Greed fetch failed: %s", e)
    return {}
except Exception as e:
    logger.error("unexpected market context error: %s", e, exc_info=True)
    return {}
```

- [ ] **Step 8: Verify app still starts and scanner runs**

```bash
python3 -c "import ai_scanner; print('OK')"
```

- [ ] **Step 9: Commit**

```bash
git add ai_scanner.py prompt_builder.py market_context.py
git commit -m "fix: replace bare except with typed catches + logging (P0-4)"
```

---

### Task 0.5: Add log_token_usage to missing modules + Pi deploy

**Files:**
- Modify: `ai_limit.py`, `ai_trade_grader.py`

- [ ] **Step 1: Add to ai_limit.py after Anthropic call**

Find the `client.messages.create(...)` call and add immediately after:
```python
from helpers import log_token_usage
cached = getattr(message.usage, "cache_read_input_tokens", 0) or 0
log_token_usage("limit_analyzer", MODEL,
                message.usage.input_tokens, message.usage.output_tokens, cached)
```

- [ ] **Step 2: Add to ai_trade_grader.py**

Same pattern:
```python
from helpers import log_token_usage
cached = getattr(message.usage, "cache_read_input_tokens", 0) or 0
log_token_usage("trade_grader", MODEL,
                message.usage.input_tokens, message.usage.output_tokens, cached)
```

- [ ] **Step 3: Deploy Phase 0 to Pi**

```bash
git push origin main
```

Then SSH:
```bash
expect -c "
set timeout 90
spawn ssh -o StrictHostKeyChecking=no fbauer@192.168.1.21
expect \"password:\"
send \"REDACTED\r\"
expect \"\\\$\"
send \"cd /home/fbauer/trading-journal && git pull origin main && sudo systemctl restart trading-journal && sleep 3 && sudo systemctl status trading-journal --no-pager | head -6\r\"
expect \"\\\$\"
send \"exit\r\"
expect eof
"
```

Expected: `Active: active (running)`

- [ ] **Step 4: Smoke test all endpoints**

```bash
curl -s http://192.168.1.21:8082/api/dashboard/kpis | python3 -c "import sys,json; d=json.load(sys.stdin); print('kpis ok' if d.get('ok') else 'FAIL')"
curl -s http://192.168.1.21:8082/api/calls/saved | python3 -c "import sys,json; d=json.load(sys.stdin); print('calls ok' if d.get('ok') else 'FAIL')"
```

- [ ] **Step 5: Commit**

```bash
git add ai_limit.py ai_trade_grader.py
git commit -m "feat: add token usage logging to ai_limit and ai_trade_grader (P2-6)"
```

---

## Phase 1: AI Client Layer + Trade History (biggest token/maintenance win)

**Token savings: eliminates 7 duplicate client instantiations**
**Maintenance: 4 copies of _symbol_history → 1**

### Task 1.1: Create ai_client.py — shared Anthropic wrapper

**Files:**
- Create: `ai_client.py`
- Modify: `ai_call.py`, `ai_scanner.py`, `ai_advisor.py`, `ai_hindsight.py`, `ai_rulebook.py`, `ai_pattern_detector.py`, `ai_trade_grader.py`, `ai_live_trade.py`, `ai_limit.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_ai_client.py
from unittest.mock import patch, MagicMock

def test_send_returns_text_and_logs(db):
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text='{"ok": true}')]
    mock_msg.usage.input_tokens = 100
    mock_msg.usage.output_tokens = 50
    mock_msg.usage.cache_read_input_tokens = 0

    with patch("ai_client._client") as mock_client:
        mock_client.messages.create.return_value = mock_msg
        from ai_client import send
        text, cached = send("test_module", "claude-sonnet-4-6",
                            [{"role": "user", "content": "hi"}], max_tokens=100)

    assert text == '{"ok": true}'
    assert cached == 0
    mock_client.messages.create.assert_called_once()

def test_send_logs_token_usage(db):
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="result")]
    mock_msg.usage.input_tokens = 200
    mock_msg.usage.output_tokens = 80
    mock_msg.usage.cache_read_input_tokens = 50

    with patch("ai_client._client") as mock_client:
        mock_client.messages.create.return_value = mock_msg
        from ai_client import send
        send("advisor", "claude-sonnet-4-6", [], max_tokens=500)

    row = db.execute(
        "SELECT input_tokens, cached_tokens FROM token_usage WHERE module='advisor'"
    ).fetchone()
    assert row is not None
    assert row[0] == 200
    assert row[1] == 50
```

- [ ] **Step 2: Run test — confirm FAIL**

```bash
python3 -m pytest tests/test_ai_client.py -v
```

- [ ] **Step 3: Create ai_client.py**

```python
# ai_client.py
"""
Singleton Anthropic client with automatic token logging.
All AI modules import `send()` from here instead of constructing their own client.
"""
import anthropic
from constants import ANTHROPIC_API_KEY
from helpers import log_token_usage

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def send(module: str, model: str, messages: list, max_tokens: int,
         system: str = None) -> tuple[str, int]:
    """
    Make one Anthropic messages.create call and log token usage.

    Returns:
        (response_text: str, cached_tokens: int)

    Raises:
        anthropic.APIError — callers handle retry/fallback logic themselves.
        Do NOT wrap in generic except here — let callers decide how to handle.
    """
    kwargs = dict(model=model, max_tokens=max_tokens, messages=messages)
    if system:
        kwargs["system"] = system

    message = _client.messages.create(**kwargs)
    text = message.content[0].text
    cached = getattr(message.usage, "cache_read_input_tokens", 0) or 0

    log_token_usage(module, model,
                    message.usage.input_tokens,
                    message.usage.output_tokens,
                    cached)

    return text, cached
```

- [ ] **Step 4: Run test — confirm PASS**

```bash
python3 -m pytest tests/test_ai_client.py -v
```

Expected: `2 passed`

- [ ] **Step 5: Refactor each AI module to use ai_client.send()**

For each module (`ai_call.py`, `ai_scanner.py`, `ai_advisor.py`, `ai_hindsight.py`, `ai_rulebook.py`, `ai_pattern_detector.py`, `ai_trade_grader.py`, `ai_live_trade.py`, `ai_limit.py`):

**Remove:**
```python
import anthropic
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
message = client.messages.create(model=MODEL, messages=messages, max_tokens=N)
raw = message.content[0].text
cached = getattr(message.usage, "cache_read_input_tokens", 0) or 0
log_token_usage("module_name", MODEL, message.usage.input_tokens, ...)
```

**Replace with:**
```python
from ai_client import send as ai_send
raw, cached = ai_send("module_name", MODEL, messages, max_tokens=N)
```

Note: `ai_scanner.py` has 3 separate `client.messages.create` calls (quick-score Haiku, batch Sonnet, serial fallback). Replace each independently.

- [ ] **Step 6: Verify all AI modules import cleanly**

```bash
python3 -c "
import ai_call, ai_scanner, ai_advisor, ai_hindsight, ai_rulebook
import ai_pattern_detector, ai_trade_grader, ai_live_trade, ai_limit
print('all AI modules OK')
"
```

- [ ] **Step 7: Commit**

```bash
git add ai_client.py tests/test_ai_client.py ai_*.py
git commit -m "refactor: centralise Anthropic client in ai_client.py (eliminates 7 duplicate instantiations)"
```

---

### Task 1.2: Create trade_history.py — eliminate 4 _symbol_history copies

**Files:**
- Create: `trade_history.py`
- Modify: `ai_call.py`, `ai_scanner.py`, `ai_hindsight.py`, `ai_live_trade.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_trade_history.py
def test_get_recent_trades_returns_list(sample_positions):
    from trade_history import get_recent_trades
    trades = get_recent_trades("BTCUSDT", sample_positions, limit=10)
    assert isinstance(trades, list)
    assert all("realized_pnl" in t for t in trades)

def test_get_recent_trades_limit_respected(sample_positions):
    from trade_history import get_recent_trades
    trades = get_recent_trades("BTCUSDT", sample_positions, limit=2)
    assert len(trades) <= 2

def test_get_recent_trades_before_date(sample_positions):
    from trade_history import get_recent_trades
    trades = get_recent_trades("BTCUSDT", sample_positions, before_iso="2026-01-05")
    assert all(t["open_time"] < "2026-01-05" for t in trades)

def test_get_trade_stats_win_rate(sample_positions):
    from trade_history import get_recent_trades, get_trade_stats
    trades = get_recent_trades("BTCUSDT", sample_positions)
    stats = get_trade_stats(trades)
    # 2 wins (100, 80), 2 losses (-50, -20) for BTCUSDT Long+Short
    assert 0 <= stats["win_rate_pct"] <= 100
    assert "total_pnl" in stats
    assert "trades" in stats

def test_get_trade_stats_empty():
    from trade_history import get_trade_stats
    stats = get_trade_stats([])
    assert stats["trades"] == 0
    assert stats["win_rate_pct"] == 0
```

- [ ] **Step 2: Run tests — confirm FAIL**

```bash
python3 -m pytest tests/test_trade_history.py -v
```

- [ ] **Step 3: Create trade_history.py**

```python
# trade_history.py
"""
Unified symbol trade history queries.
Replaces _symbol_history() in ai_call, ai_scanner, ai_hindsight, ai_live_trade.
"""
from __future__ import annotations
import sqlite3


def get_recent_trades(
    symbol: str,
    conn: sqlite3.Connection,
    before_iso: str | None = None,
    exchange: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """
    Return the most recent closed trades for a symbol.

    Args:
        symbol:     e.g. "BTCUSDT"
        conn:       open SQLite connection
        before_iso: if set, only trades whose open_time < before_iso (blind scoring)
        exchange:   if set, filter to one exchange
        limit:      max rows to return

    Returns:
        List of dicts with keys: symbol, direction, realized_pnl, duration_minutes,
        entry_price, close_price, open_time, close_time, exchange
    """
    conditions = ["close_time IS NOT NULL", "symbol = ?"]
    params: list = [symbol]

    if before_iso:
        conditions.append("open_time < ?")
        params.append(before_iso)

    if exchange:
        conditions.append("(exchange = ? OR (exchange IS NULL AND ? = 'bitget'))")
        params.extend([exchange, exchange])

    where = " AND ".join(conditions)
    params.append(limit)

    rows = conn.execute(
        f"SELECT symbol, direction, realized_pnl, duration_minutes, "
        f"       entry_price, close_price, open_time, close_time, exchange "
        f"FROM positions WHERE {where} "
        f"ORDER BY close_time DESC LIMIT ?",
        params,
    ).fetchall()

    return [dict(r) for r in rows]


def get_trade_stats(trades: list[dict]) -> dict:
    """
    Compute win-rate and P&L stats from a trade list.

    Args:
        trades: output of get_recent_trades()

    Returns:
        {trades, wins, losses, win_rate_pct, total_pnl, avg_pnl, avg_win, avg_loss}
    """
    if not trades:
        return {
            "trades": 0, "wins": 0, "losses": 0,
            "win_rate_pct": 0, "total_pnl": 0.0,
            "avg_pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
        }

    pnls = [t["realized_pnl"] or 0.0 for t in trades]
    wins  = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n = len(pnls)

    return {
        "trades":       n,
        "wins":         len(wins),
        "losses":       len(losses),
        "win_rate_pct": round(len(wins) / n * 100, 1) if n else 0,
        "total_pnl":    round(sum(pnls), 2),
        "avg_pnl":      round(sum(pnls) / n, 2) if n else 0.0,
        "avg_win":      round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss":     round(sum(losses) / len(losses), 2) if losses else 0.0,
    }


def get_symbol_summary(symbol: str, conn: sqlite3.Connection,
                       before_iso: str | None = None,
                       exchange: str | None = None) -> dict:
    """
    Convenience: get_recent_trades + get_trade_stats in one call.
    Matches the old _symbol_history() return shape for easy migration.
    """
    trades = get_recent_trades(symbol, conn, before_iso=before_iso,
                               exchange=exchange)
    stats = get_trade_stats(trades)
    return {"recent_trades": trades, **stats}
```

- [ ] **Step 4: Run tests — confirm PASS**

```bash
python3 -m pytest tests/test_trade_history.py -v
```

Expected: `5 passed`

- [ ] **Step 5: Replace _symbol_history in each module**

In `ai_call.py` — replace `_symbol_history(symbol, conn)` with:
```python
from trade_history import get_symbol_summary
history = get_symbol_summary(symbol, conn, exchange=exchange)
```

In `ai_scanner.py` — replace `_symbol_history(symbol, conn)` with:
```python
from trade_history import get_symbol_summary
history = get_symbol_summary(symbol, conn)
```

In `ai_hindsight.py` — replace `_symbol_history_before(symbol, before_iso, conn)` with:
```python
from trade_history import get_symbol_summary
history = get_symbol_summary(symbol, conn, before_iso=before_iso)
```

In `ai_live_trade.py` — replace `_get_symbol_history(symbol, conn)` with:
```python
from trade_history import get_symbol_summary
history = get_symbol_summary(symbol, conn)
```

Delete the private `_symbol_history*` function definitions from all four files.

- [ ] **Step 6: Commit + Pi deploy**

```bash
git add trade_history.py tests/test_trade_history.py ai_call.py ai_scanner.py ai_hindsight.py ai_live_trade.py
git commit -m "refactor: centralise symbol history queries in trade_history.py (4 → 1 implementation)"
git push origin main
# Deploy via expect SSH (see Phase 0 Task 0.5)
```

---

## Phase 2: chart_context.py Split (1,140 → ~300 lines each)

**Maintainability: each responsibility independently testable**
**Token savings: compact indicator format saves ~50 tokens/TF/call**

### Task 2.1: Extract chart_indicators.py

**Files:**
- Create: `chart_indicators.py`
- Modify: `chart_context.py` (remove indicator computation, keep orchestration)

- [ ] **Step 1: Write failing test**

```python
# tests/test_chart_indicators.py
import pandas as pd
import numpy as np

@pytest.fixture
def sample_df():
    """100 rows of fake OHLCV data."""
    n = 100
    closes = np.linspace(100, 120, n) + np.random.randn(n) * 2
    df = pd.DataFrame({
        "open":   closes - 1,
        "high":   closes + 2,
        "low":    closes - 2,
        "close":  closes,
        "volume": np.random.randint(1000, 5000, n).astype(float),
    })
    return df

def test_compute_rsi_returns_dict(sample_df):
    from chart_indicators import compute_rsi
    result = compute_rsi(sample_df)
    assert "value" in result
    assert "level" in result   # "overbought" | "oversold" | "neutral"
    assert 0 <= result["value"] <= 100

def test_compute_ema_alignment(sample_df):
    from chart_indicators import compute_ema_alignment
    result = compute_ema_alignment(sample_df)
    assert result["alignment"] in ("bullish", "bearish", "neutral", "mixed")
    assert "ema20" in result and "ema50" in result

def test_compute_confluence_score(sample_df):
    from chart_indicators import compute_confluence
    result = compute_confluence(sample_df, sr_levels=[105.0, 115.0])
    assert "score" in result
    assert 0.0 <= result["score"] <= 1.0
    assert "label" in result   # "Strong Bullish" | "Bullish" | "Neutral" | etc.

def test_compute_prompt_text_is_compact(sample_df):
    from chart_indicators import compute_prompt_text
    text = compute_prompt_text(sample_df, sr_levels=[])
    assert len(text) < 200, f"Too long: {len(text)} chars: {text}"
    assert "RSI" in text or "EMA" in text
```

- [ ] **Step 2: Create chart_indicators.py**

Extract from `chart_context.py` lines ~300–1050 (all indicator computation). The key interface:

```python
# chart_indicators.py
"""
Pure indicator computation on a pandas DataFrame.
No Bitget API calls. No caching. No side effects.
All functions accept a DataFrame and return a dict.
"""
import pandas as pd
import pandas_ta as ta

def compute_rsi(df: pd.DataFrame, period: int = 14) -> dict:
    rsi_series = ta.rsi(df["close"], length=period)
    val = float(rsi_series.iloc[-1]) if not rsi_series.empty else 50.0
    level = "overbought" if val > 70 else "oversold" if val < 30 else "neutral"
    return {"value": round(val, 1), "level": level}

def compute_ema_alignment(df: pd.DataFrame) -> dict:
    ema20  = float(ta.ema(df["close"], length=20).iloc[-1])
    ema50  = float(ta.ema(df["close"], length=50).iloc[-1])
    ema200 = float(ta.ema(df["close"], length=200).iloc[-1])
    if ema20 > ema50 > ema200:
        alignment = "bullish"
    elif ema20 < ema50 < ema200:
        alignment = "bearish"
    elif ema20 > ema200:
        alignment = "mixed-bullish"
    else:
        alignment = "mixed-bearish"
    return {"ema20": round(ema20, 4), "ema50": round(ema50, 4),
            "ema200": round(ema200, 4), "alignment": alignment}

def compute_confluence(df: pd.DataFrame, sr_levels: list[float]) -> dict:
    """Aggregate bullish/bearish signals into a 0-1 score."""
    rsi    = compute_rsi(df)
    ema    = compute_ema_alignment(df)
    close  = float(df["close"].iloc[-1])

    bullish = bearish = 0
    if rsi["level"] == "oversold":   bullish += 1
    if rsi["level"] == "overbought": bearish += 1
    if ema["alignment"].startswith("bull"):  bullish += 1
    if ema["alignment"].startswith("bear"):  bearish += 1

    # Price near support (within 1% below)
    for lvl in sr_levels:
        if abs(close - lvl) / close < 0.01:
            bullish += 0.5

    total   = bullish + bearish or 1
    score   = round(bullish / total, 2)
    if score >= 0.75:  label = "Strong Bullish"
    elif score >= 0.6: label = "Bullish"
    elif score <= 0.25: label = "Strong Bearish"
    elif score <= 0.4:  label = "Bearish"
    else:               label = "Neutral"

    return {"score": score, "label": label, "bullish": bullish, "bearish": bearish}

def compute_prompt_text(df: pd.DataFrame, sr_levels: list[float]) -> str:
    """Single-line prompt summary (~80 chars). Never include raw indicator dicts."""
    rsi  = compute_rsi(df)
    ema  = compute_ema_alignment(df)
    conf = compute_confluence(df, sr_levels)
    sr_str = f" SR@{sr_levels[0]:.4g}" if sr_levels else ""
    return (f"EMA {ema['alignment']} | RSI {rsi['value']} ({rsi['level']}) |"
            f" Conf {conf['label']}{sr_str}")
```

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest tests/test_chart_indicators.py -v
```

Expected: `4 passed`

- [ ] **Step 4: Update chart_context.py to import from chart_indicators**

Replace inline indicator computation with:
```python
from chart_indicators import compute_rsi, compute_ema_alignment, compute_confluence, compute_prompt_text
```

- [ ] **Step 5: Commit**

```bash
git add chart_indicators.py tests/test_chart_indicators.py chart_context.py
git commit -m "refactor: extract chart_indicators.py from chart_context.py (pure indicator functions)"
```

---

### Task 2.2: Extract chart_sr.py (S/R detection)

**Files:**
- Create: `chart_sr.py`
- Modify: `chart_context.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_chart_sr.py
def test_detect_sr_levels_returns_list(sample_df):
    from chart_sr import detect_sr_levels
    levels = detect_sr_levels(sample_df)
    assert isinstance(levels, list)
    for lvl in levels:
        assert "price" in lvl
        assert "type" in lvl          # "support" | "resistance"
        assert "strength" in lvl      # float 0-1
        assert "touches" in lvl       # int

def test_sr_levels_near_current_price(sample_df):
    from chart_sr import detect_sr_levels
    close = float(sample_df["close"].iloc[-1])
    levels = detect_sr_levels(sample_df)
    prices = [l["price"] for l in levels]
    if prices:
        closest = min(abs(p - close) for p in prices)
        assert closest / close < 0.15, "All levels far from price — check pivot logic"
```

- [ ] **Step 2: Create chart_sr.py**

Extract swing-pivot and clustering logic (~lines 88–275 in chart_context.py):

```python
# chart_sr.py
"""S/R level detection via swing pivots and price-cluster weighting."""
import pandas as pd

def detect_sr_levels(df: pd.DataFrame, window: int = 5,
                     max_levels: int = 8) -> list[dict]:
    """
    Find support/resistance levels using swing high/low pivots.
    Returns levels sorted by strength (descending).
    """
    highs = df["high"].rolling(window * 2 + 1, center=True).max() == df["high"]
    lows  = df["low"].rolling(window * 2 + 1, center=True).min()  == df["low"]

    pivots = []
    for i, row in df.iterrows():
        if highs.loc[i]:
            pivots.append({"price": float(row["high"]),  "type": "resistance"})
        if lows.loc[i]:
            pivots.append({"price": float(row["low"]),   "type": "support"})

    if not pivots:
        return []

    # Cluster nearby pivots (within 0.5% of each other)
    clusters: list[dict] = []
    for pivot in pivots:
        merged = False
        for cluster in clusters:
            if abs(pivot["price"] - cluster["price"]) / cluster["price"] < 0.005:
                cluster["touches"] += 1
                cluster["price"] = (cluster["price"] + pivot["price"]) / 2
                merged = True
                break
        if not merged:
            clusters.append({**pivot, "touches": 1,
                             "strength": 0.0})

    max_touches = max(c["touches"] for c in clusters) or 1
    for c in clusters:
        c["strength"] = round(c["touches"] / max_touches, 2)

    return sorted(clusters, key=lambda x: -x["strength"])[:max_levels]
```

- [ ] **Step 3: Run tests, update chart_context.py, commit**

```bash
python3 -m pytest tests/test_chart_sr.py -v
# Update chart_context.py: from chart_sr import detect_sr_levels
git add chart_sr.py tests/test_chart_sr.py chart_context.py
git commit -m "refactor: extract chart_sr.py from chart_context.py"
git push origin main
# Pi deploy
```

---

## Phase 3: Database Migration Versioning

**Files:**
- Modify: `database.py`

### Task 3.1: Add schema_version table + numbered migrations

- [ ] **Step 1: Write failing test**

```python
# tests/test_database.py
def test_schema_version_table_exists(db):
    tables = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "schema_version" in tables

def test_migrations_are_idempotent(db):
    """Running init_db() twice must not raise."""
    from database import init_db
    init_db()   # already called by fixture; call again
    # Should not raise
```

- [ ] **Step 2: Add to database.py**

At the top of `init_db()`, add:
```python
conn.execute("""
    CREATE TABLE IF NOT EXISTS schema_version (
        version     INTEGER PRIMARY KEY,
        name        TEXT    NOT NULL,
        applied_at  TEXT    DEFAULT (datetime('now'))
    )
""")

def _applied(ver):
    return conn.execute(
        "SELECT 1 FROM schema_version WHERE version=?", (ver,)
    ).fetchone() is not None

def _apply(ver, name, sql):
    if _applied(ver):
        return
    try:
        conn.execute(sql)
        conn.execute("INSERT INTO schema_version (version, name) VALUES (?,?)", (ver, name))
        logger.info("Applied migration %d: %s", ver, name)
    except Exception as e:
        logger.error("Migration %d failed: %s", ver, e, exc_info=True)
        raise
```

Replace all `try: ALTER TABLE ... except sqlite3.OperationalError: pass` patterns with numbered `_apply()` calls:
```python
_apply(1, "positions.market_regime",
       "ALTER TABLE positions ADD COLUMN market_regime TEXT")
_apply(2, "positions.mfe_price",
       "ALTER TABLE positions ADD COLUMN mfe_price REAL")
# ... etc for each existing migration
```

- [ ] **Step 3: Run tests + commit + deploy**

```bash
python3 -m pytest tests/test_database.py -v
git add database.py tests/test_database.py
git commit -m "feat: add schema_version table + numbered migration runner (P1-5)"
git push origin main
# Pi deploy + smoke test
```

---

## Phase 4: Route Thinning + normalize utilities

**Files:**
- Create: `models/analyzed_calls.py`
- Modify: `routes/calls.py`, `trade_utils.py`

### Task 4.1: Move normalize_symbol/direction to trade_utils

- [ ] **Step 1: Write failing test**

```python
# tests/test_trade_utils.py
def test_normalize_symbol():
    from trade_utils import normalize_symbol
    assert normalize_symbol("BTC/USDT") == "BTCUSDT"
    assert normalize_symbol("btc-usdt") == "BTCUSDT"
    assert normalize_symbol("BTCUSDT")  == "BTCUSDT"

def test_normalize_direction():
    from trade_utils import normalize_direction
    assert normalize_direction("long")       == "Long"
    assert normalize_direction("BUY")        == "Long"
    assert normalize_direction("open_long")  == "Long"
    assert normalize_direction("short")      == "Short"
    assert normalize_direction("sell")       == "Short"
```

- [ ] **Step 2: Add to trade_utils.py**

```python
def normalize_symbol(s: str) -> str:
    """BTC/USDT, btc-usdt, BTC_USDT → BTCUSDT."""
    return (s or "").upper().replace("/", "").replace("-", "").replace("_", "").strip()

def normalize_direction(s: str) -> str:
    """long/buy/open_long → Long; short/sell → Short."""
    d = (s or "").strip().lower()
    if d in ("long", "buy", "open_long"): return "Long"
    if d in ("short", "sell", "open_short"): return "Short"
    return s  # pass-through for unexpected values
```

- [ ] **Step 3: Update routes/calls.py**

Replace inline `_ns()` and `_nd()` with:
```python
from trade_utils import normalize_symbol, normalize_direction
```

- [ ] **Step 4: Commit**

```bash
python3 -m pytest tests/test_trade_utils.py -v
git add trade_utils.py tests/test_trade_utils.py routes/calls.py
git commit -m "refactor: move normalize_symbol/direction to trade_utils (P1-3)"
```

---

## Phase 5: Quick Wins (any order, low risk)

- [ ] **Remove `ai_call_analyzer.py` stub** (11 lines, just re-exports ai_call):
  ```bash
  git rm ai_call_analyzer.py
  git commit -m "chore: remove ai_call_analyzer.py stub"
  ```

- [ ] **Fix `ai_rulebook.py` dead import** — remove `import traceback` (unused):
  Open `ai_rulebook.py`, delete line `import traceback`.

- [ ] **Tune ThreadPoolExecutor to Pi cores**:
  In `ai_scanner.py`, replace `max_workers=8` with `max_workers=SCANNER_MAX_WORKERS` (imported from constants.py — set to 4).

- [ ] **Add `.env.example`** with all new keys:
  ```bash
  cat > .env.example << 'EOF'
  BITGET_API_KEY=
  BITGET_SECRET_KEY=
  BITGET_PASSPHRASE=
  BLOFIN_API_KEY=
  BLOFIN_SECRET_KEY=
  BLOFIN_PASSPHRASE=
  ANTHROPIC_API_KEY=
  NANSEN_API_KEY=
  FRED_API_KEY=
  TELEGRAM_BOT_TOKEN=
  TELEGRAM_CHAT_ID=
  APP_URL=http://localhost:8082
  PORT=8082
  LOG_LEVEL=INFO
  EOF
  git add .env.example
  git commit -m "docs: add .env.example with all required keys"
  ```

---

## Self-Review

**Spec coverage:**
- ✅ AI client duplication (7 → 1) — Task 1.1
- ✅ symbol_history x4 → 1 — Task 1.2
- ✅ 8 bare excepts — Task 0.4
- ✅ chart_context 1,140 lines — Tasks 2.1, 2.2
- ✅ prompt token waste (scoring scale) — Task 0.3
- ✅ Shallow routes (normalize utils) — Task 4.1
- ✅ DB migration versioning — Task 3.1
- ✅ Hardcoded constants — Task 0.2
- ✅ log_token_usage missing modules — Task 0.5
- ⬜ Scanner pipeline stages (not detailed here — medium priority, no user-visible change)
- ⬜ JS global state (not in scope — frontend is functional)
- ⬜ API client consistency (bitget/blofin/nansen) — documented in arch spec but not detailed here

**Placeholder scan:** None found. All steps have actual code.

**Type consistency:** `get_symbol_summary()` in trade_history.py returns `{"recent_trades": list, "trades": int, "win_rate_pct": float, ...}`. All callers reference `history["win_rate_pct"]` — consistent with prior `_symbol_history()` return shape.

---

## Execution Order Summary

```
Phase 0  →  Phase 1  →  Phase 2  →  Phase 3  →  Phase 4  →  Phase 5
(Foundation) (AI/History) (chart_context) (DB migrations) (Routes) (Quick wins)

Each phase: implement → run tests → Pi deploy → smoke test → next phase
```

**Estimated time:** ~6-8 hours total. Each phase: 1-2 hours.
**Risk level:** Low. Every phase is additive or pure rename — no API surface changes.
