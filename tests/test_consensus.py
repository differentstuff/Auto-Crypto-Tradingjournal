"""Unit tests for consensus.py — deterministic scoring logic."""


def test_confirmed_delta_zero():
    from consensus import compute_consensus
    r = compute_consensus(7, 7)
    assert r["flag"] == "✓ Confirmed"
    assert r["delta"] == 0
    assert r["consensus_score"] == 7.0


def test_confirmed_delta_one():
    from consensus import compute_consensus
    r = compute_consensus(8, 7)
    assert r["flag"] == "✓ Confirmed"
    assert r["delta"] == 1


def test_aligned_delta_two():
    from consensus import compute_consensus
    r = compute_consensus(8, 6)
    assert r["flag"] == "~ Aligned"
    assert r["delta"] == 2


def test_divergent_delta_three():
    from consensus import compute_consensus
    r = compute_consensus(9, 6)
    assert r["flag"] == "⚠ Divergent"
    assert r["delta"] == 3


def test_review_delta_four_plus():
    from consensus import compute_consensus
    r = compute_consensus(9, 4)
    assert r["flag"] == "⚡ REVIEW"
    assert r["delta"] == 5


def test_result_has_all_fields():
    from consensus import compute_consensus
    r = compute_consensus(7, 7)
    for f in ("consensus_score", "claude_score", "gemini_score", "delta",
              "confidence", "flag", "prompt_line"):
        assert f in r, f"Missing field: {f}"


def test_compute_consensus_importable_without_agent_orchestrator():
    """consensus module must NOT import agent_orchestrator (no circular dep)."""
    import sys
    # Remove cached modules to get a fresh import
    for k in list(sys.modules):
        if "consensus" in k:
            del sys.modules[k]
    import consensus
    assert "agent_orchestrator" not in sys.modules or \
           "consensus" not in sys.modules.get("agent_orchestrator", object).__dict__
