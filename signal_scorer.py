# signal_scorer.py
"""
XGBoost win-probability scorer trained on historical analyzed_calls.
Min 20 labeled outcomes required. Silently returns None if untrained.
Model persisted via joblib.
"""
import logging
import os
import time
import numpy as np

_log          = logging.getLogger(__name__)
_MIN_SAMPLES  = 20
_RETRAIN_TTL  = 86400   # 24 h
_MODEL_PATH   = os.path.join(os.path.dirname(__file__), ".ml_scorer.joblib")


def _extract_features(row: dict) -> list[float] | None:
    try:
        direction = 1.0 if row.get("direction", "long") == "long" else -1.0
        # Parse R:R ratio (e.g. "2.5:1" -> 2.5)
        rr = 0.0
        rr_str = str(row.get("rr_ratio") or "0")
        try:
            rr = float(rr_str.split(":")[0])
        except Exception:
            pass
        return [
            float(row.get("setup_score")     or 5),
            direction,
            min(rr, 10.0),  # cap at 10 to avoid outliers
            float(row.get("consensus_score") or 5),
        ]
    except Exception:
        return None


class SignalScorer:
    def __init__(self):
        self._model      = None
        self._trained_at = 0.0

    @property
    def is_trained(self) -> bool:
        return self._model is not None

    @staticmethod
    def _get_classifier():
        """Return XGBClassifier if available and loadable, else GradientBoostingClassifier."""
        try:
            from xgboost import XGBClassifier  # noqa: F401 — triggers native lib load
            return XGBClassifier(
                n_estimators=50, max_depth=3, learning_rate=0.1,
                eval_metric="logloss", random_state=42, verbosity=0,
            )
        except Exception:
            _log.info("signal_scorer: xgboost unavailable, using sklearn fallback")
            from sklearn.ensemble import GradientBoostingClassifier
            return GradientBoostingClassifier(
                n_estimators=50, max_depth=3, learning_rate=0.1, random_state=42
            )

    def train(self, conn) -> bool:
        try:
            rows = conn.execute(
                "SELECT setup_score, direction, outcome, rr_ratio, consensus_score "
                "FROM analyzed_calls WHERE outcome IN ('won','lost') "
                "AND setup_score IS NOT NULL "
                "ORDER BY id DESC LIMIT 500"
            ).fetchall()
            if len(rows) < _MIN_SAMPLES:
                return False
            X, y = [], []
            for r in rows:
                row   = dict(zip(["setup_score", "direction", "outcome", "rr_ratio", "consensus_score"], r))
                feats = _extract_features(row)
                if feats is None:
                    continue
                X.append(feats)
                y.append(1 if row["outcome"] == "won" else 0)
            if len(X) < _MIN_SAMPLES:
                return False
            model = self._get_classifier()
            model.fit(np.array(X), np.array(y))
            self._model      = model
            self._trained_at = time.time()
            try:
                import joblib
                joblib.dump(model, _MODEL_PATH)
            except Exception:
                pass
            return True
        except Exception as exc:
            _log.warning("signal_scorer.train: %s", exc)
            return False

    def predict(self, features: dict) -> float | None:
        if not self.is_trained:
            return None
        try:
            vec = _extract_features(features)
            if vec is None:
                return None
            return round(float(self._model.predict_proba([vec])[0][1]), 3)
        except Exception:
            return None


_global_scorer = SignalScorer()


def get_scorer(conn=None) -> SignalScorer:
    """Return module-level scorer; retrains every 24 h when conn is provided."""
    global _global_scorer
    needs = (not _global_scorer.is_trained or
             (conn is not None and
              time.time() - _global_scorer._trained_at > _RETRAIN_TTL))
    if needs and conn is not None:
        _global_scorer.train(conn)
    return _global_scorer
