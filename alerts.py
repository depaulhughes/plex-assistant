import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from config import ALERT_COOLDOWN_MINUTES, ALERT_LOG_PATH, ENABLE_ALERT_LOGGING


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_jsonl(path: str) -> list[dict]:
    file_path = Path(path)
    if not file_path.exists():
        return []

    rows = []
    with file_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def load_recent_alert_history(limit: int = 200) -> list[dict]:
    rows = _read_jsonl(ALERT_LOG_PATH)
    return rows[-limit:]


def _alert_impact_size(alert: dict) -> int:
    sessions = alert.get("affected_sessions", [])
    clients = alert.get("affected_clients", [])
    return len(sessions) + len(clients)


def should_emit_alert(alert: dict, recent_alert_history: list[dict]) -> bool:
    now = _parse_timestamp(alert.get("triggered_at")) or datetime.now(timezone.utc)
    cooldown = timedelta(minutes=ALERT_COOLDOWN_MINUTES)
    current_impact = _alert_impact_size(alert)

    for prior in reversed(recent_alert_history):
        comparable = (
            prior.get("cooldown_key") == alert.get("cooldown_key")
            or (
                prior.get("alert_type") == alert.get("alert_type")
                and prior.get("diagnosis") == alert.get("diagnosis")
            )
        )
        if not comparable:
            continue

        prior_time = _parse_timestamp(prior.get("triggered_at"))
        if prior_time is None:
            continue

        if alert.get("severity") == "critical" and prior.get("severity") != "critical":
            return True

        if _alert_impact_size(prior) < current_impact:
            return True

        if set(prior.get("affected_sessions", [])) != set(alert.get("affected_sessions", [])):
            return True

        if set(prior.get("affected_clients", [])) != set(alert.get("affected_clients", [])):
            return True

        if now - prior_time < cooldown:
            return False

        break

    return True


def evaluate_alerts(current_state: dict, history_summary: dict, recent_alert_history: list[dict]) -> list[dict]:
    structured = current_state.get("structured_diagnosis", {})
    metadata = current_state.get("issue_metadata", {})
    facts = current_state.get("facts", {})
    diagnosis = structured.get("most_likely_cause")
    alerts = []
    now = _utc_now_iso()

    candidates = []

    if facts.get("buffering_session_count", 0) > 1:
        candidates.append(
            {
                "alert_type": "multi_session_buffering",
                "severity": "critical",
                "title": "Multiple sessions are buffering",
                "message": "More than one session is currently confirmed buffering.",
                "triggered_at": now,
                "diagnosis": diagnosis,
                "affected_sessions": structured.get("buffering_sessions", []),
                "affected_clients": facts.get("affected_session_client_names", []),
                "cooldown_key": f"multi_session_buffering:{','.join(sorted(structured.get('buffering_sessions', [])))}",
            }
        )

    if diagnosis == "upload_saturation":
        candidates.append(
            {
                "alert_type": "sustained_upload_saturation",
                "severity": "critical",
                "title": "Sustained Plex upload saturation detected",
                "message": "Upload appears sustained, Plex-driven, and critically headroom-constrained.",
                "triggered_at": now,
                "diagnosis": diagnosis,
                "affected_sessions": structured.get("buffering_sessions", []),
                "affected_clients": facts.get("affected_session_client_names", []),
                "cooldown_key": "sustained_upload_saturation",
            }
        )

    if diagnosis == "transcoding" and metadata.get("severity") == "critical":
        candidates.append(
            {
                "alert_type": "repeated_transcode_overload",
                "severity": "critical",
                "title": "Transcoding load is operationally significant",
                "message": "Current transcoding load looks broad enough to risk wider service impact.",
                "triggered_at": now,
                "diagnosis": diagnosis,
                "affected_sessions": structured.get("buffering_sessions", []),
                "affected_clients": facts.get("affected_session_client_names", []),
                "cooldown_key": "repeated_transcode_overload",
            }
        )

    if (
        diagnosis == "client_network_path_sensitivity"
        and history_summary.get("repeated_client_network_path_sensitivity")
        and facts.get("affected_session_client_names")
    ):
        client = facts["affected_session_client_names"][0]
        candidates.append(
            {
                "alert_type": "repeated_client_specific_issue",
                "severity": "warning",
                "title": "Repeated client-specific playback issue",
                "message": f"{client} has shown repeated client/network-path sensitivity in recent history.",
                "triggered_at": now,
                "diagnosis": diagnosis,
                "affected_sessions": structured.get("buffering_sessions", []),
                "affected_clients": [client],
                "cooldown_key": f"repeated_client_specific_issue:{client}",
            }
        )

    for alert in candidates:
        if should_emit_alert(alert, recent_alert_history):
            alerts.append(alert)

    return alerts


def log_alerts(alerts: list[dict]) -> None:
    if not alerts or not ENABLE_ALERT_LOGGING:
        return

    file_path = Path(ALERT_LOG_PATH)
    with file_path.open("a", encoding="utf-8") as handle:
        for alert in alerts:
            handle.write(json.dumps(alert, sort_keys=True) + "\n")
