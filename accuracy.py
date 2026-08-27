"""Data-driven alert accuracy helpers.

The bot should be measured against human-verified outcomes before any ML model is
allowed to influence alerts. This module calculates precision, recall, F1 and
alert latency from labeled observations.
"""
from __future__ import annotations


def evaluate(rows):
    labeled = [r for r in rows if r.get("actual") in (True, False)]
    tp = sum(r.get("predicted") is True and r["actual"] is True for r in labeled)
    fp = sum(r.get("predicted") is True and r["actual"] is False for r in labeled)
    fn = sum(r.get("predicted") is False and r["actual"] is True for r in labeled)
    tn = sum(r.get("predicted") is False and r["actual"] is False for r in labeled)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (tp + tn) / len(labeled) if labeled else 0.0
    return {"samples": len(labeled), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4), "accuracy": round(accuracy, 4)}


def alert_latency_minutes(detected_at, posted_at):
    """Return detection delay in minutes when both timestamps are parseable."""
    from datetime import datetime
    if not detected_at or not posted_at:
        return None
    try:
        a = datetime.fromisoformat(detected_at.replace("Z", "+00:00"))
        b = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
        return round((a - b).total_seconds() / 60.0, 2)
    except Exception:
        return None
