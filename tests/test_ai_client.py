from unittest.mock import patch, MagicMock


def test_send_returns_text_and_cached(db):
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text='{"ok": true}')]
    mock_msg.usage.input_tokens = 100
    mock_msg.usage.output_tokens = 50
    mock_msg.usage.cache_read_input_tokens = 25

    with patch("ai_client._client") as mock_client:
        mock_client.messages.create.return_value = mock_msg
        from ai_client import send
        text, cached = send("test_module", "claude-sonnet-4-6",
                            [{"role": "user", "content": "hi"}], max_tokens=100)

    assert text == '{"ok": true}'
    assert cached == 25


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
