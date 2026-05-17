# signal_scorer.py
"""
XGBoost win-probability scorer trained on historical analyzed_calls.
Min 20 labeled outcomes required. Silently returns None if untrained.
Model persisted via joblib.
"""
import json
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
        analysis = row.get("analysis_json") or {}
        if isinstance(analysis, str):
            analysis = json.loads(analysis)
        direction = 1.0 if row.get("direction", "long") == "long" else -1.0
        return [
            float(row.get("setup_score")          or 5),
            float(analysis.get("rsi")             or 50),
            float(analysis.get("macd_histogram")  or 0),
            float(analysis.get("ema_alignment")   or 0),
            float(analysis.get("adx")             or 20),
            float(analysis.get("wt_signal")       or 0),
            float(analysis.get("mfi")             or 50),
            float(analysis.get("cvd_trend")       or 0),
            float(analysis.get("volume_ratio")    or 1),
            direction,
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
                "SELECT setup_score, direction, outcome, analysis_json "
                "FROM analyzed_calls WHERE outcome IN ('won','lost') "
                "ORDER BY id DESC LIMIT 500"
            ).fetchall()
            if len(rows) < _MIN_SAMPLES:
                return False
            X, y = [], []
            for r in rows:
                row   = dict(zip(["setup_score", "direction", "outcome", "analysis_json"], r))
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
