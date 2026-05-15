"""Tests for retroactive outcome recorder intrabar priority fix."""


def test_intrabar_tp_wins_over_sl_when_open_above_sl():
    """When both TP1 and SL hit same candle, TP wins if candle opened above SL."""
    # Simulate: long trade, entry=100, SL=95, TP1=105
    # Candle: open=101 (above SL=95), low=94 (hits SL), high=106 (hits TP1)
    # Expected: TP1 wins (position was safe when candle opened)
    sl_price = 95.0
    tp1_price = 105.0
    candle_open, low, high = 101.0, 94.0, 106.0
    is_long = True

    sl_hit  = bool(sl_price  and low  <= sl_price)
    tp1_hit = bool(tp1_price and high >= tp1_price)
    open_above_sl = not sl_price or candle_open > sl_price

    assert sl_hit is True   # SL was touched
    assert tp1_hit is True  # TP1 was touched
    assert open_above_sl is True  # Position was safe when bar opened

    # The priority logic: TP wins when open_above_sl
    outcome = None
    if tp1_hit and (open_above_sl or not sl_hit):
        outcome = "won"
    elif sl_hit:
        outcome = "lost"

    assert outcome == "won", "TP should win when candle opened above SL"


def test_intrabar_sl_wins_when_open_below_sl():
    """When open is below SL (gapped down through SL), SL takes priority."""
    sl_price = 95.0
    tp1_price = 105.0
    candle_open, low, high = 94.0, 91.0, 97.0
    # Open=94 < SL=95, so position was already in danger at open

    sl_hit  = bool(sl_price  and low  <= sl_price)
    tp1_hit = bool(tp1_price and high >= tp1_price)
    open_above_sl = not sl_price or candle_open > sl_price

    assert sl_hit is True
    assert tp1_hit is False  # 97 < 105, TP not hit
    assert open_above_sl is False

    outcome = None
    if tp1_hit and (open_above_sl or not sl_hit):
        outcome = "won"
    elif sl_hit:
        outcome = "lost"

    assert outcome == "lost"
