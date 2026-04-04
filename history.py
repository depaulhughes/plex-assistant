import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from config import (
    ENABLE_HISTORY_LOGGING,
    HISTORY_LOG_PATH,
    HISTORY_LOOKBACK_LIMIT,
    LOG_HEALTHY_SNAPSHOTS,
    LOG_ONLY_ON_CHANGE,
    MIN_EVENT_LOG_INTERVAL_SECONDS,
    MIN_HEALTHY_LOG_INTERVAL_SECONDS,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_jsonl(path: str, limit: Optional[int] = None) -> list[dict]:
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

    if limit is not None:
        return rows[-limit:]
    return rows


def load_recent_history(limit: int = HISTORY_LOOKBACK_LIMIT) -> list[dict]:
    return _read_jsonl(HISTORY_LOG_PATH, limit=limit)


def build_issue_fingerprint_from_parts(
    diagnosis: str,
    severity: str,
    scope: str,
    buffering_sessions: list[str],
    affected_clients: list[str],
) -> str:
    normalized_sessions = ",".join(sorted(str(item) for item in buffering_sessions if item))
    normalized_clients = ",".join(sorted(str(item) for item in affected_clients if item))
    return "|".join(
        [
            diagnosis or "unknown",
            severity or "unknown",
            scope or "unknown",
            normalized_sessions,
            normalized_clients,
        ]
    )


def build_issue_fingerprint(state: dict) -> str:
    structured = state.get("structured_diagnosis", {})
    metadata = state.get("issue_metadata", {})
    relevant_titles, relevant_clients = _relevant_issue_entities(state)
    return build_issue_fingerprint_from_parts(
        diagnosis=structured.get("most_likely_cause", "unknown"),
        severity=metadata.get("severity", "unknown"),
        scope=metadata.get("scope", "unknown"),
        buffering_sessions=relevant_titles,
        affected_clients=relevant_clients,
    )


def _relevant_issue_entities(state: dict) -> tuple[list[str], list[str]]:
    structured = state.get("structured_diagnosis", {})
    facts = state.get("facts", {})
    diagnosis = structured.get("most_likely_cause", "unknown")
    session_facts = facts.get("session_facts", [])

    if diagnosis == "transcoding":
        relevant_sessions = [sf for sf in session_facts if sf.get("is_transcode")]
        return (
            [sf.get("title") for sf in relevant_sessions if sf.get("title")],
            [sf.get("client_name") for sf in relevant_sessions if sf.get("client_name")],
        )

    buffering_sessions = structured.get("buffering_sessions", []) or facts.get("buffering_sessions", [])
    affected_clients = facts.get("affected_session_client_names", [])
    if buffering_sessions or affected_clients:
        return list(buffering_sessions), list(affected_clients)

    risk_titles = facts.get("buffering_risk_sessions", [])
    risk_sessions = [sf for sf in session_facts if sf.get("title") in risk_titles]
    return (
        [sf.get("title") for sf in risk_sessions if sf.get("title")],
        [sf.get("client_name") for sf in risk_sessions if sf.get("client_name")],
    )


def build_diagnosis_event(state: dict) -> dict:
    structured = state.get("structured_diagnosis", {})
    metadata = state.get("issue_metadata", {})
    facts = state.get("facts", {})
    manager_summary = state.get("manager_summary", {})
    issue_fingerprint = build_issue_fingerprint(state)
    affected_sessions, affected_clients = _relevant_issue_entities(state)

    return {
        "timestamp": _utc_now_iso(),
        "diagnosis": structured.get("most_likely_cause"),
        "severity": metadata.get("severity"),
        "scope": metadata.get("scope"),
        "confidence": metadata.get("confidence"),
        "active_session_count": state.get("plex", {}).get("active_sessions", 0),
        "buffering_session_count": facts.get("buffering_session_count", 0),
        "healthy_session_count": facts.get("healthy_playing_session_count", 0),
        "transcode_count": state.get("plex", {}).get("transcodes", 0),
        "affected_session_count": len(affected_sessions),
        "affected_sessions": affected_sessions,
        "buffering_sessions": structured.get("buffering_sessions", []),
        "healthy_sessions": facts.get("healthy_session_titles", []),
        "affected_clients": affected_clients,
        "issue_fingerprint": issue_fingerprint,
        "state_change": state.get("state_change", {}),
        "system": {
            "host_cpu_percent": state.get("system", {}).get("host_cpu_percent"),
            "host_ram_percent": state.get("system", {}).get("host_ram_percent"),
            "plex_cpu_host_percent": state.get("system", {}).get("plex_cpu_host_percent"),
            "plex_upload_mbps": state.get("system", {}).get("plex_upload_mbps"),
            "total_upload_mbps": state.get("system", {}).get("total_upload_mbps"),
            "iowait_percent": state.get("system", {}).get("iowait_percent"),
        },
        "facts": {
            "has_mixed_session_health": facts.get("has_mixed_session_health", False),
            "single_session_buffering_while_others_healthy": facts.get(
                "single_session_buffering_while_others_healthy", False
            ),
            "sustained_upload_saturation": facts.get("sustained_upload_saturation", False),
            "burst_upload_saturation": facts.get("burst_upload_saturation", False),
            "sustained_upload_high": facts.get("sustained_upload_high", False),
            "upload_is_bursty": facts.get("upload_is_bursty", False),
            "upload_is_stable": facts.get("upload_is_stable", False),
            "system_wide_issue_likely": facts.get("system_wide_issue_likely", False),
            "session_specific_issue_likely": facts.get("session_specific_issue_likely", False),
            "same_content_playing_elsewhere_successfully": facts.get(
                "same_content_playing_elsewhere_successfully", False
            ),
        },
        "manager_summary": {
            "service_health": manager_summary.get("service_health"),
            "impact_summary": manager_summary.get("impact_summary"),
            "escalation_needed": manager_summary.get("escalation_needed"),
        },
    }


def _scope_evidence_strength(event: dict) -> int:
    facts = event.get("facts") or {}
    system = event.get("system") or {}
    if event.get("buffering_session_count", 0) > 1:
        return 3
    if event.get("affected_session_count", 0) > 1:
        return 3
    if facts.get("sustained_upload_high"):
        return 3
    if (system.get("total_upload_mbps") or 0) and (system.get("plex_upload_mbps") or 0):
        try:
            total_upload = float(system.get("total_upload_mbps") or 0)
            if total_upload > 0:
                from config import UPLOAD_LIMIT_MBPS

                upload_saturation_percent = (total_upload / float(UPLOAD_LIMIT_MBPS)) * 100
                if upload_saturation_percent >= 85:
                    return 3
                if upload_saturation_percent >= 70:
                    return 2
        except Exception:
            pass
    if (system.get("host_cpu_percent") or 0) >= 85 or (system.get("plex_cpu_host_percent") or 0) >= 60:
        return 3
    if (system.get("host_cpu_percent") or 0) >= 60 or (system.get("plex_cpu_host_percent") or 0) >= 35:
        return 2
    return 1


def _stabilize_event_scope(event: dict, recent_history: list[dict]) -> dict:
    if not recent_history:
        return event

    last_event = recent_history[-1]
    if not last_event:
        return event

    if event.get("diagnosis") != last_event.get("diagnosis"):
        return event
    if event.get("severity") != last_event.get("severity"):
        return event
    if event.get("affected_sessions") != last_event.get("affected_sessions"):
        return event
    if event.get("affected_clients") != last_event.get("affected_clients"):
        return event

    current_scope = event.get("scope")
    last_scope = last_event.get("scope")
    if current_scope == last_scope:
        return event

    # Avoid scope churn for the same issue shape unless telemetry now clearly
    # shows a shared bottleneck or broader affected set.
    if _scope_evidence_strength(event) < 3:
        event["scope"] = last_scope
        event["issue_fingerprint"] = build_issue_fingerprint_from_parts(
            diagnosis=event.get("diagnosis", "unknown"),
            severity=event.get("severity", "unknown"),
            scope=event.get("scope", "unknown"),
            buffering_sessions=event.get("affected_sessions", []),
            affected_clients=event.get("affected_clients", []),
        )
    return event


def _seconds_since(event: Optional[dict], now: datetime) -> Optional[float]:
    if event is None:
        return None
    last_time = _parse_timestamp(event.get("timestamp"))
    if last_time is None:
        return None
    return (now - last_time).total_seconds()

def classify_state_change(current_state: dict, recent_history: list[dict]) -> dict:
    structured = current_state.get("structured_diagnosis", {})
    metadata = current_state.get("issue_metadata", {})
    current_fingerprint = build_issue_fingerprint(current_state)
    last_event = recent_history[-1] if recent_history else None
    current_buffering_count = len(structured.get("buffering_sessions", []))
    current_severity = metadata.get("severity", "info")
    current_scope = metadata.get("scope", "unknown")
    current_diagnosis = structured.get("most_likely_cause", "unknown")
    system = current_state.get("system", {})
    facts = current_state.get("facts", {})

    if last_event is None:
        if current_diagnosis == "none_detected":
            return {"change_type": "no_material_change", "reason": "no_prior_history"}
        return {"change_type": "new_issue", "reason": "first_notable_issue"}

    last_diagnosis = last_event.get("diagnosis", "unknown")
    last_severity = last_event.get("severity", "info")
    last_scope = last_event.get("scope", "unknown")
    last_buffering_count = last_event.get("buffering_session_count", 0)
    last_fingerprint = last_event.get("issue_fingerprint") or build_issue_fingerprint_from_parts(
        diagnosis=last_diagnosis,
        severity=last_severity,
        scope=last_scope,
        buffering_sessions=last_event.get("buffering_sessions", []),
        affected_clients=last_event.get("affected_clients", []),
    )

    severity_rank = {"info": 0, "warning": 1, "critical": 2}

    if last_diagnosis == "none_detected" and current_diagnosis != "none_detected":
        return {"change_type": "new_issue", "reason": "issue_started"}
    if last_diagnosis != "none_detected" and current_diagnosis == "none_detected":
        return {"change_type": "resolved_issue", "reason": "issue_cleared"}
    if current_fingerprint == last_fingerprint:
        return {"change_type": "ongoing_issue" if current_diagnosis != "none_detected" else "no_material_change", "reason": "same_issue_fingerprint"}
    if severity_rank.get(current_severity, 0) > severity_rank.get(last_severity, 0):
        return {"change_type": "worsening_issue", "reason": "severity_increased"}
    if current_buffering_count > last_buffering_count:
        return {"change_type": "worsening_issue", "reason": "more_sessions_affected"}
    if severity_rank.get(current_severity, 0) < severity_rank.get(last_severity, 0):
        return {"change_type": "improving_issue", "reason": "severity_decreased"}
    if current_buffering_count < last_buffering_count:
        return {"change_type": "improving_issue", "reason": "fewer_sessions_affected"}
    scope_change_is_material = (
        current_scope != last_scope
        and (
            current_buffering_count > 1
            or facts.get("sustained_upload_high")
            or (system.get("host_cpu_percent", 0) >= 85)
            or (system.get("plex_cpu_host_percent", 0) >= 60)
        )
    )
    if scope_change_is_material or current_diagnosis != last_diagnosis:
        return {"change_type": "new_issue", "reason": "issue_shape_changed"}
    return {"change_type": "no_material_change", "reason": "minor_variation_only"}


def should_log_diagnosis_event(current_event: dict, recent_history: list[dict]) -> bool:
    if not ENABLE_HISTORY_LOGGING:
        return False

    last_event = recent_history[-1] if recent_history else None
    now = _parse_timestamp(current_event.get("timestamp")) or datetime.now(timezone.utc)
    seconds_since_last = _seconds_since(last_event, now)
    diagnosis = current_event.get("diagnosis")
    healthy_state = diagnosis == "none_detected"
    low_impact_state = (
        current_event.get("severity") == "info"
        and diagnosis in {"transcoding", "client_file_compatibility_issue", "client_network_path_sensitivity"}
        and current_event.get("buffering_session_count", 0) == 0
        and not (current_event.get("facts") or {}).get("sustained_upload_high", False)
    )

    if last_event is None:
        return not healthy_state or LOG_HEALTHY_SNAPSHOTS

    if healthy_state and not LOG_HEALTHY_SNAPSHOTS:
        if last_event.get("diagnosis") != "none_detected":
            return True
        return False

    current_change_type = (current_event.get("state_change") or {}).get("change_type")
    if current_change_type in {"new_issue", "worsening_issue", "improving_issue", "resolved_issue"}:
        return True

    # For a small private Plex setup, low-impact compatibility states should be
    # logged once and then stay quiet unless something materially changes.
    if low_impact_state and current_change_type == "ongoing_issue":
        return False

    if current_event.get("issue_fingerprint") != last_event.get("issue_fingerprint"):
        return True
    if current_event.get("diagnosis") != last_event.get("diagnosis"):
        return True
    if current_event.get("severity") != last_event.get("severity"):
        return True
    if current_event.get("scope") != last_event.get("scope"):
        return True
    if current_event.get("buffering_session_count") != last_event.get("buffering_session_count"):
        return True
    if current_event.get("affected_session_count") != last_event.get("affected_session_count"):
        return True
    if current_event.get("affected_clients") != last_event.get("affected_clients"):
        return True

    if LOG_ONLY_ON_CHANGE:
        return False

    min_interval = MIN_HEALTHY_LOG_INTERVAL_SECONDS if healthy_state else MIN_EVENT_LOG_INTERVAL_SECONDS
    if seconds_since_last is None:
        return True
    return seconds_since_last >= min_interval


def log_diagnosis_event(state: dict, recent_history: list[dict]) -> Optional[dict]:
    event = build_diagnosis_event(state)
    event = _stabilize_event_scope(event, recent_history)
    if not should_log_diagnosis_event(event, recent_history):
        return None

    file_path = Path(HISTORY_LOG_PATH)
    with file_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")

    return event


def summarize_recent_history(recent_history: list[dict]) -> dict:
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)

    recent_day = []
    for event in recent_history:
        timestamp = _parse_timestamp(event.get("timestamp"))
        if timestamp is not None and timestamp >= day_ago:
            recent_day.append(event)

    diagnosis_counts = {}
    affected_clients = {}
    warning_or_higher = 0

    for event in recent_day:
        diagnosis = event.get("diagnosis") or "unknown"
        diagnosis_counts[diagnosis] = diagnosis_counts.get(diagnosis, 0) + 1

        if event.get("severity") in {"warning", "critical"}:
            warning_or_higher += 1

        for client in event.get("affected_clients", []):
            affected_clients[client] = affected_clients.get(client, 0) + 1

    top_diagnosis = None
    if diagnosis_counts:
        top_diagnosis = max(diagnosis_counts.items(), key=lambda item: item[1])[0]

    top_client = None
    if affected_clients:
        top_client = max(affected_clients.items(), key=lambda item: item[1])[0]

    return {
        "events_last_24h": len(recent_day),
        "warning_or_higher_last_24h": warning_or_higher,
        "diagnosis_counts_last_24h": diagnosis_counts,
        "top_diagnosis_last_24h": top_diagnosis,
        "affected_client_counts_last_24h": affected_clients,
        "top_affected_client_last_24h": top_client,
        "repeated_client_network_path_sensitivity": diagnosis_counts.get("client_network_path_sensitivity", 0) >= 2,
        "repeated_client_file_compatibility_issue": diagnosis_counts.get("client_file_compatibility_issue", 0) >= 2,
        "repeated_upload_saturation": diagnosis_counts.get("upload_saturation", 0) >= 2,
        "repeated_transcoding": diagnosis_counts.get("transcoding", 0) >= 2,
    }
