"""token_log.py -- AI token usage telemetry."""
from database import db_conn


def log_token_usage(module: str, model: str, input_tokens: int, output_tokens: int,
                    cached_tokens: int = 0) -> None:
    """Record API token usage to the token_usage table."""
    try:
        with db_conn() as conn:
            conn.execute(
                "INSERT INTO token_usage (module, model, input_tokens, output_tokens, cached_tokens) "
                "VALUES (?,?,?,?,?)",
                (module, model, input_tokens, output_tokens, cached_tokens)
            )
            conn.commit()
    except Exception:
        pass  # never let logging errors break the calling code
