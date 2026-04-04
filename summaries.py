from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from config import (
    HOME_RECENT_INCIDENT_COOLDOWN_MINUTES,
    HOME_RECENT_INCIDENT_STRONG_MINUTES,
    PLAYBACK_INSTABILITY_HALF_LIFE_MINUTES,
    PLAYBACK_INSTABILITY_MEMORY_MINUTES,
    UPLOAD_LIMIT_MBPS,
)


def _humanize_diagnosis(diagnosis: str) -> str:
    return diagnosis.replace("_", " ")


_OPERATOR_FACTOR_LABELS = {
    "mac_client_sensitivity": "Mac client sensitivity",
    "desktop_client_sensitivity": "Desktop client sensitivity",
    "tv_client_compatibility": "TV client compatibility",
    "device_specific_playback_limitation": "Device-specific playback limitation",
    "audio_codec_compatibility": "Audio codec compatibility",
    "subtitle_compatibility": "Subtitle compatibility",
    "light_audio_transcode": "Light audio transcode",
    "client_file_compatibility_traits": "file compatibility traits",
    "bursty_upload_pattern": "bursty upload pattern",
    "delivery_throughput_mismatch": "delivery throughput mismatch",
    "mixed_session_health": "mixed session health",
    "non_plex_upload_present": "non-Plex upload present",
    "same_content_healthy_elsewhere": "same content healthy elsewhere",
    "image_subtitles_present": "image subtitles present",
    "high_bitrate_session": "high bitrate session",
}

_MANAGER_FACTOR_LABELS = {
    "mac_client_sensitivity": "desktop-Mac playback sensitivity is present",
    "desktop_client_sensitivity": "desktop playback sensitivity is present",
    "tv_client_compatibility": "TV client compatibility factors are present",
    "device_specific_playback_limitation": "device-specific playback limitations are present",
    "audio_codec_compatibility": "audio compatibility factors are present",
    "subtitle_compatibility": "subtitle compatibility factors are present",
    "light_audio_transcode": "a light compatibility transcode is present",
    "client_file_compatibility_traits": "supporting file-format traits present",
    "bursty_upload_pattern": "delivery variability is present",
    "delivery_throughput_mismatch": "throughput mismatch is present",
    "mixed_session_health": "the issue appears localized rather than broad",
    "non_plex_upload_present": "other upload traffic may be contributing",
    "same_content_healthy_elsewhere": "the same content is healthy on another client",
    "image_subtitles_present": "subtitle-related playback factors are present",
    "high_bitrate_session": "the affected session is relatively demanding",
}

_DASHBOARD_FACTOR_LABELS = {
    "mac_client_sensitivity": "Mac client sensitivity",
    "desktop_client_sensitivity": "Desktop client sensitivity",
    "tv_client_compatibility": "TV client compatibility",
    "device_specific_playback_limitation": "Device-specific playback limitation",
    "audio_codec_compatibility": "Audio codec compatibility",
    "subtitle_compatibility": "Subtitle compatibility",
    "light_audio_transcode": "Light audio transcode",
    "client_file_compatibility_traits": "File compatibility traits",
    "bursty_upload_pattern": "Bursty delivery pattern",
    "delivery_throughput_mismatch": "Delivery mismatch",
    "mixed_session_health": "Localized session impact",
    "non_plex_upload_present": "Other upload traffic",
    "same_content_healthy_elsewhere": "Same content healthy elsewhere",
    "image_subtitles_present": "Image subtitles present",
    "high_bitrate_session": "High bitrate session",
}

_HISTORY_DIAGNOSIS_LABELS = {
    "none_detected": "No issue detected",
    "client_network_path_sensitivity": "Client network path sensitivity",
    "client_file_compatibility_issue": "Client/file playback compatibility issue",
    "client_or_network": "Localized client or network issue",
    "network_throughput_issue": "Delivery throughput issue",
    "upload_saturation": "Upload saturation",
    "transcoding": "Transcoding",
}

_STATE_CHANGE_LABELS = {
    "new_issue": "New issue detected",
    "ongoing_issue": "Issue ongoing",
    "worsening_issue": "Impact increasing",
    "improving_issue": "Impact improving",
    "resolved_issue": "Issue resolved",
    "no_material_change": "No material change",
}

_SEVERITY_DISPLAY_LABELS = {
    "info": "Low",
    "warning": "Medium",
    "critical": "High",
}


def _presentation_diagnosis_label(primary_diagnosis: str, contributing_factors: list) -> str:
    if primary_diagnosis == "client_network_path_sensitivity":
        if "client_file_compatibility_traits" in contributing_factors:
            return "Client-specific playback sensitivity"
        return "Client/network path sensitivity"
    if primary_diagnosis == "client_file_compatibility_issue":
        if "sensitive_client_type" in contributing_factors or "bursty_upload_pattern" in contributing_factors:
            return "Client/file playback compatibility issue"
        return "Client/file compatibility issue"
    if primary_diagnosis == "client_or_network":
        return "Localized client or network issue"
    if primary_diagnosis == "network_throughput_issue":
        return "Delivery throughput issue"
    if primary_diagnosis == "upload_saturation":
        return "Upload saturation"
    if primary_diagnosis == "transcoding":
        if "light_audio_transcode" in contributing_factors:
            return "Light audio transcode for client compatibility"
        if "subtitle_compatibility" in contributing_factors:
            return "Subtitle-driven client compatibility transcode"
        return "Transcoding"
    if primary_diagnosis == "none_detected":
        return "No confirmed issue"
    return _humanize_diagnosis(primary_diagnosis).capitalize()


def diagnosis_display_label(diagnosis: str) -> str:
    return _HISTORY_DIAGNOSIS_LABELS.get(diagnosis, _humanize_diagnosis(diagnosis).capitalize())


def state_change_display_label(change_type: str) -> str:
    return _STATE_CHANGE_LABELS.get(change_type, _humanize_diagnosis(change_type).capitalize())


def severity_display_label(severity: str) -> str:
    return _SEVERITY_DISPLAY_LABELS.get(severity, _humanize_diagnosis(severity).capitalize())


def _format_history_timestamp(value: str) -> str:
    if not value:
        return "unknown"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(EASTERN_TZ)
        return dt.strftime("%b %-d, %Y, %-I:%M %p ET")
    except Exception:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(EASTERN_TZ)
            # Fallback for platforms that do not support %-d / %-I.
            return dt.strftime("%b %d, %Y, %I:%M %p ET").replace(" 0", " ").replace(" 0", " ")
        except Exception:
            return value


def _diagnosis_supporting_text(primary_diagnosis: str, contributing_factors: list, buffering_confirmed: bool) -> str:
    if not buffering_confirmed and primary_diagnosis == "none_detected":
        return "Playback is healthy and no active issue is currently confirmed."

    if primary_diagnosis in {"client_network_path_sensitivity", "client_file_compatibility_issue"}:
        client_specific_factor_present = any(
            factor in contributing_factors
            for factor in [
                "mac_client_sensitivity",
                "desktop_client_sensitivity",
                "tv_client_compatibility",
                "device_specific_playback_limitation",
            ]
        )
        if client_specific_factor_present and "bursty_upload_pattern" in contributing_factors:
            return "This looks primarily like a localized playback issue, with device sensitivity and bursty delivery also contributing."
        if client_specific_factor_present and "client_file_compatibility_traits" in contributing_factors:
            return "This looks primarily like a localized playback issue, with device sensitivity and file playback traits both contributing."
        if "client_file_compatibility_traits" in contributing_factors:
            return "This appears localized to the affected client/session, with file playback traits also contributing."
        if "bursty_upload_pattern" in contributing_factors:
            return "This appears localized to the affected client/session, with bursty delivery also contributing."

    if primary_diagnosis == "transcoding":
        if "light_audio_transcode" in contributing_factors and "tv_client_compatibility" in contributing_factors:
            return "This appears to be a light audio transcode driven by TV client compatibility rather than heavy server-side transcoding load."
        if "light_audio_transcode" in contributing_factors and "audio_codec_compatibility" in contributing_factors:
            return "This appears to be a light audio-only transcode for client compatibility, with limited system impact."
        if "subtitle_compatibility" in contributing_factors:
            return "This transcode appears driven by subtitle or playback compatibility rather than broad server strain."

    if primary_diagnosis == "client_or_network":
        return "This appears localized rather than service-wide."

    return ""


def _relevant_session_facts(state: dict, structured_diagnosis: dict) -> list:
    facts = state.get("facts", {})
    all_sessions = facts.get("session_facts", [])
    diagnosis = structured_diagnosis.get("most_likely_cause", "unknown")

    buffering_titles = set(facts.get("buffering_session_titles", []) or facts.get("buffering_sessions", []) or [])
    risk_titles = set(facts.get("buffering_risk_sessions", []) or [])

    if diagnosis == "transcoding":
        relevant = [sf for sf in all_sessions if sf.get("is_transcode")]
        if relevant:
            return relevant

    relevant = [sf for sf in all_sessions if sf.get("title") in buffering_titles]
    if relevant:
        return relevant

    relevant = [sf for sf in all_sessions if sf.get("title") in risk_titles]
    if relevant:
        return relevant

    if diagnosis == "transcoding":
        return [sf for sf in all_sessions if sf.get("audio_decision") == "transcode" or sf.get("subtitle_decision") == "transcode"]

    return all_sessions


def build_contributing_factors(state: dict, structured_diagnosis: dict, issue_metadata: dict) -> list:
    facts = state.get("facts", {})
    sessions = _relevant_session_facts(state, structured_diagnosis)
    diagnosis = structured_diagnosis.get("most_likely_cause", "unknown")
    buffering_confirmed = structured_diagnosis.get("buffering_confirmed", False)

    client_specific_factor = None
    if any(sf.get("client_is_mac") for sf in sessions):
        client_specific_factor = "mac_client_sensitivity"
    elif any(sf.get("client_is_desktop_app") for sf in sessions):
        client_specific_factor = "desktop_client_sensitivity"
    elif any(sf.get("client_is_tv_app") for sf in sessions):
        client_specific_factor = "tv_client_compatibility"
    elif sessions:
        client_specific_factor = "device_specific_playback_limitation"

    factor_conditions = {
        "mac_client_sensitivity": client_specific_factor == "mac_client_sensitivity",
        "desktop_client_sensitivity": client_specific_factor == "desktop_client_sensitivity",
        "tv_client_compatibility": client_specific_factor == "tv_client_compatibility",
        "device_specific_playback_limitation": client_specific_factor == "device_specific_playback_limitation",
        "audio_codec_compatibility": any(sf.get("audio_decision") == "transcode" for sf in sessions),
        "subtitle_compatibility": any(
            sf.get("subtitle_decision") == "transcode" or sf.get("is_image_subtitle") for sf in sessions
        ),
        "light_audio_transcode": any(
            sf.get("is_transcode")
            and sf.get("audio_decision") == "transcode"
            and sf.get("video_decision") != "transcode"
            and sf.get("container_decision") != "transcode"
            for sf in sessions
        ),
        "client_file_compatibility_traits": (
            any(sf.get("file_trait_risk") or sf.get("compatibility_pattern") for sf in sessions)
            or (not buffering_confirmed and any(sf.get("file_trait_risk") for sf in sessions))
        ),
        "bursty_upload_pattern": facts.get("upload_is_bursty"),
        "delivery_throughput_mismatch": (
            any(sf.get("delivery_below_expected") for sf in sessions)
            or (
                not buffering_confirmed
                and any(sf.get("delivery_below_expected") for sf in sessions)
            )
        ),
        "mixed_session_health": facts.get("has_mixed_session_health"),
        "non_plex_upload_present": facts.get("non_plex_upload_present"),
        "same_content_healthy_elsewhere": facts.get("same_content_playing_elsewhere_successfully"),
        "image_subtitles_present": any(sf.get("is_image_subtitle") for sf in sessions),
        "high_bitrate_session": any(sf.get("bitrate_high") for sf in sessions),
    }

    if diagnosis == "none_detected" and not buffering_confirmed:
        # In healthy states, do not elevate passive client or file traits into
        # current contributing factors unless there is explicit playback impact.
        return []

    priority_by_diagnosis = {
        "client_network_path_sensitivity": [
            "mac_client_sensitivity",
            "desktop_client_sensitivity",
            "tv_client_compatibility",
            "device_specific_playback_limitation",
            "same_content_healthy_elsewhere",
            "client_file_compatibility_traits",
            "bursty_upload_pattern",
            "delivery_throughput_mismatch",
            "mixed_session_health",
            "high_bitrate_session",
        ],
        "client_file_compatibility_issue": [
            "client_file_compatibility_traits",
            "subtitle_compatibility",
            "audio_codec_compatibility",
            "mac_client_sensitivity",
            "desktop_client_sensitivity",
            "tv_client_compatibility",
            "device_specific_playback_limitation",
            "image_subtitles_present",
            "mixed_session_health",
            "high_bitrate_session",
        ],
        "transcoding": [
            "light_audio_transcode",
            "audio_codec_compatibility",
            "subtitle_compatibility",
            "tv_client_compatibility",
            "device_specific_playback_limitation",
            "client_file_compatibility_traits",
            "high_bitrate_session",
        ],
        "upload_saturation": [
            "bursty_upload_pattern",
            "non_plex_upload_present",
            "high_bitrate_session",
        ],
        "network_throughput_issue": [
            "delivery_throughput_mismatch",
            "mixed_session_health",
            "high_bitrate_session",
            "mac_client_sensitivity",
            "desktop_client_sensitivity",
            "tv_client_compatibility",
            "device_specific_playback_limitation",
        ],
        "client_or_network": [
            "mac_client_sensitivity",
            "desktop_client_sensitivity",
            "tv_client_compatibility",
            "device_specific_playback_limitation",
            "delivery_throughput_mismatch",
            "mixed_session_health",
            "same_content_healthy_elsewhere",
            "client_file_compatibility_traits",
        ],
        "none_detected": [
            "mac_client_sensitivity",
            "desktop_client_sensitivity",
            "tv_client_compatibility",
            "device_specific_playback_limitation",
            "client_file_compatibility_traits",
            "high_bitrate_session",
        ],
    }

    selected = []
    for factor in priority_by_diagnosis.get(
        diagnosis,
        [
            "device_specific_playback_limitation",
            "client_file_compatibility_traits",
            "delivery_throughput_mismatch",
            "mixed_session_health",
        ],
    ):
        if factor_conditions.get(factor):
            selected.append(factor)
        if len(selected) >= 3:
            break

    if not buffering_confirmed and diagnosis == "none_detected":
        return selected[:2]

    return selected[:3]


def build_diagnosis_presentation(
    state: dict,
    structured_diagnosis: dict,
    issue_metadata: dict,
    action_plan: dict,
) -> dict:
    primary_diagnosis = structured_diagnosis.get("most_likely_cause", "unknown")
    contributing_factors = build_contributing_factors(state, structured_diagnosis, issue_metadata)
    primary_diagnosis_label = _presentation_diagnosis_label(primary_diagnosis, contributing_factors)

    return {
        "primary_diagnosis": primary_diagnosis,
        "primary_diagnosis_label": primary_diagnosis_label,
        "supporting_text": _diagnosis_supporting_text(
            primary_diagnosis,
            contributing_factors,
            structured_diagnosis.get("buffering_confirmed", False),
        ),
        "contributing_factors": contributing_factors,
        "operator_contributing_factors": [
            _OPERATOR_FACTOR_LABELS.get(factor, factor.replace("_", " "))
            for factor in contributing_factors
        ],
        "dashboard_contributing_factors": [
            _DASHBOARD_FACTOR_LABELS.get(factor, factor.replace("_", " "))
            for factor in contributing_factors[:2]
        ],
        "manager_contributing_factors": [
            _MANAGER_FACTOR_LABELS.get(factor, factor.replace("_", " "))
            for factor in contributing_factors
        ],
        "severity": issue_metadata.get("severity", "info"),
        "severity_display_label": severity_display_label(issue_metadata.get("severity", "info")),
        "scope": issue_metadata.get("scope", "unknown"),
        "confidence": issue_metadata.get("confidence", "low"),
        "primary_action": action_plan.get("primary_action", "No immediate action needed."),
    }


def _manager_user_impact_label(facts: dict, severity: str, scope: str, playback_quality: Optional[dict] = None) -> str:
    playback_quality = playback_quality or {}
    buffering_count = facts.get("buffering_session_count", 0)
    if buffering_count > 1 or scope in {"system_wide", "service_wide"}:
        return "broad"
    if buffering_count == 1:
        return "moderate"
    if playback_quality.get("recent_window_active"):
        return "minor"
    if severity == "warning":
        return "minor"
    return "none"


def _manager_action_urgency(
    severity: str,
    scope: str,
    escalation_needed: bool,
    facts: dict,
    playback_quality: Optional[dict] = None,
) -> str:
    playback_quality = playback_quality or {}
    if severity == "critical" or escalation_needed:
        return "act_now"
    if severity == "warning" and (facts.get("buffering_session_count", 0) > 0 or scope in {"system_wide", "service_wide"}):
        return "investigate_now"
    if playback_quality.get("recent_window_active") or playback_quality.get("recurrence_risk_label") in {"Guarded", "High"}:
        return "watch"
    if severity == "warning" or facts.get("buffering_risk_detected"):
        return "watch"
    return "none"


def _manager_trend_judgment(state_change: dict, facts: dict, playback_quality: Optional[dict] = None) -> str:
    playback_quality = playback_quality or {}
    change_type = state_change.get("change_type", "no_material_change")
    if change_type == "worsening_issue":
        return "worsening"
    if change_type == "improving_issue":
        return "improving"
    if playback_quality.get("recovered") and playback_quality.get("recurrence_risk_label") in {"Guarded", "High"}:
        return "intermittent"
    if change_type == "ongoing_issue":
        return "stable"
    if facts.get("upload_is_bursty") and not facts.get("sustained_upload_high"):
        return "intermittent"
    return "stable"


def _manager_why_not_worse(facts: dict, system: dict, structured_diagnosis: dict) -> list:
    reasons = []
    if not structured_diagnosis.get("buffering_confirmed"):
        reasons.append("No active buffering is confirmed.")
    if facts.get("buffering_session_count", 0) <= 1:
        reasons.append("Only one session appears affected.")
    if system.get("host_cpu_percent", 0) < 60 and system.get("host_ram_percent", 0) < 85 and system.get("iowait_percent", 0) < 8:
        reasons.append("CPU, RAM, and disk I/O remain normal.")
    if not facts.get("sustained_upload_high"):
        reasons.append("Upload is not saturated.")
    if facts.get("healthy_playing_session_count", 0) > 0:
        reasons.append("Other sessions are still healthy.")
    return reasons[:5]


def _manager_escalation_triggers(action_plan: dict, scope: str) -> list:
    triggers = list(action_plan.get("escalate_if", [])[:3])
    triggers.extend(
        [
            "2+ sessions buffering.",
            "Sustained upload saturation near capacity.",
            "Rising Plex CPU with active transcodes.",
        ]
    )
    if scope in {"session_specific", "client_specific"}:
        triggers.append("Scope broadens from session-specific to multi-session.")
    unique = []
    for item in triggers:
        if item not in unique:
            unique.append(item)
    return unique[:4]


def _manager_affected_scope_summary(facts: dict, scope: str) -> dict:
    active_sessions = max(facts.get("healthy_playing_session_count", 0) + facts.get("buffering_session_count", 0), 0)
    return {
        "users_affected": facts.get("buffering_session_count", 0),
        "active_sessions": active_sessions,
        "top_affected_client": (facts.get("affected_session_client_names") or ["none"])[0],
        "other_sessions_healthy": facts.get("healthy_playing_session_count", 0) > 0,
        "playback_progressing": facts.get("buffering_session_count", 0) == 0,
        "scope_label": scope,
    }


def _manager_recommendation_ladder(
    diagnosis: str,
    severity: str,
    action_plan: dict,
    playback_quality: Optional[dict] = None,
) -> dict:
    playback_quality = playback_quality or {}
    if severity == "critical":
        bucket = "urgent_intervention"
        reason = "Confirmed service impact or strong infrastructure pressure is present."
    elif playback_quality.get("recent_window_active") and playback_quality.get("recurrence_risk_label") in {"Guarded", "High"}:
        bucket = "monitor"
        reason = "Playback recovered, but recent WAN-limited instability suggests reduced delivery margin."
    elif diagnosis == "upload_saturation":
        bucket = "investigate_capacity"
        reason = "Capacity pressure is the leading risk."
    elif diagnosis in {"client_file_compatibility_issue", "client_network_path_sensitivity", "transcoding"}:
        bucket = "investigate_compatibility" if severity == "warning" else "monitor"
        reason = "The issue appears localized and compatibility-related."
    elif severity == "warning":
        bucket = "monitor"
        reason = "The issue is limited but worth watching."
    else:
        bucket = "do_nothing"
        reason = "No urgent operational action is currently needed."
    return {
        "bucket": bucket,
        "reason": reason,
        "primary_action": action_plan.get("primary_action", "No immediate action needed."),
    }


def _manager_impact_breakdown(facts: dict, system: dict, diagnosis: str, severity: str, scope: str) -> dict:
    return {
        "user_experience_impact": "moderate" if facts.get("buffering_session_count", 0) > 0 else ("minor" if severity == "warning" else "none"),
        "server_health_impact": "elevated" if system.get("host_cpu_percent", 0) >= 60 or system.get("plex_cpu_host_percent", 0) >= 35 else "low",
        "network_risk": "elevated" if facts.get("sustained_upload_high") else ("watch" if facts.get("upload_is_bursty") else "low"),
        "compatibility_friction": "present" if diagnosis in {"client_file_compatibility_issue", "client_network_path_sensitivity", "transcoding"} else "low",
        "service_wide_risk": "elevated" if scope in {"system_wide", "service_wide"} else "low",
    }


def _recent_issue_context(state: dict, current_diagnosis: str) -> dict:
    memory = _instability_memory_profile(state)
    current_state_change = (state.get("state_change") or {}).get("change_type")
    if current_state_change == "resolved_issue" and not memory.get("memory_active"):
        memory = {
            **memory,
            "memory_active": True,
            "memory_pattern": "blip",
            "last_minutes_since_instability": 0,
            "latest_issue_diagnosis": current_diagnosis,
        }
    if not memory.get("memory_active"):
        return {"recent_issue_active": False}

    minutes_since = memory.get("last_minutes_since_instability")
    if minutes_since is None:
        return {"recent_issue_active": False}

    stage = "recovered_recent" if minutes_since <= HOME_RECENT_INCIDENT_STRONG_MINUTES else "recovered_monitor"
    issue_label = diagnosis_display_label(memory.get("latest_issue_diagnosis") or current_diagnosis).lower()
    facts = state.get("facts", {})
    heavy_playback = state.get("plex", {}).get("active_sessions", 0) >= 2 or float(state.get("system", {}).get("total_upload_mbps", 0) or 0) >= 20

    if minutes_since == 0:
        time_text = "just now"
    elif minutes_since == 1:
        time_text = "1 minute ago"
    else:
        time_text = f"{minutes_since} minutes ago"

    if stage == "recovered_recent":
        note = "Playback is currently stable, but {} recovered {} after heavier Plex delivery conditions.".format(
            issue_label,
            time_text,
        )
    else:
        note = "Playback is stable now, but brief {} occurred {}. Continue monitoring if heavier sessions begin.".format(
            issue_label,
            time_text,
        )

    if memory.get("memory_pattern") == "recurring":
        note += " Recent history suggests this is a recurring playback pattern rather than a one-off blip."
    elif memory.get("memory_pattern") == "clustered":
        note += " The recent pattern looks clustered rather than isolated."

    if facts.get("healthy_playing_session_count", 0) > 0 and heavy_playback:
        note += " Recent behavior suggests tighter WAN delivery headroom during heavier playback."

    return {
        "recent_issue_active": True,
        "recent_issue_stage": stage,
        "minutes_since_recent_issue": minutes_since,
        "recent_playback_note": note,
    }


def _instability_memory_profile(state: dict) -> dict:
    recent_history = state.get("recent_history_events", []) or []
    now = datetime.now(timezone.utc)
    facts = state.get("facts", {})
    system = state.get("system", {})
    upload_limit = float(UPLOAD_LIMIT_MBPS or 0) if UPLOAD_LIMIT_MBPS else 0
    current_upload_percent = (
        (float(system.get("total_upload_mbps", 0) or 0) / upload_limit) * 100
        if upload_limit > 0
        else 0
    )
    avg_upload_percent = (
        (float(facts.get("recent_upload_avg_mbps", 0) or 0) / upload_limit) * 100
        if upload_limit > 0
        else 0
    )

    weighted_score = 0.0
    recent_buffer_events = 0
    recent_instability_events = 0
    burst_events = 0
    latest_minutes = None
    latest_issue_diagnosis = None

    for event in recent_history:
        raw_ts = event.get("timestamp")
        if not raw_ts:
            continue
        try:
            event_time = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except Exception:
            continue

        minutes_ago = (now - event_time).total_seconds() / 60
        if minutes_ago < 0 or minutes_ago > PLAYBACK_INSTABILITY_MEMORY_MINUTES:
            continue

        event_buffer_count = int(event.get("buffering_session_count", 0) or 0)
        event_diagnosis = event.get("diagnosis")
        event_facts = event.get("facts") or {}
        event_system = event.get("system") or {}
        event_upload_percent = (
            (float(event_system.get("total_upload_mbps", 0) or 0) / upload_limit) * 100
            if upload_limit > 0
            else 0
        )
        decay = 0.5 ** (minutes_ago / max(PLAYBACK_INSTABILITY_HALF_LIFE_MINUTES, 0.5))

        if event_buffer_count > 0:
            base_weight = 1.4 + min((event_buffer_count - 1) * 0.45, 1.0)
            recent_buffer_events += 1
        elif event_diagnosis not in {None, "none_detected"}:
            base_weight = 0.65
        else:
            continue

        if event_facts.get("burst_upload_saturation"):
            base_weight += 0.15
            burst_events += 1
        elif event_facts.get("upload_is_bursty"):
            base_weight += 0.08
        if event_facts.get("sustained_upload_saturation") or event_facts.get("sustained_upload_high") or event_upload_percent >= 95:
            base_weight += 0.75
        elif event_upload_percent >= 85:
            base_weight += 0.55
        elif event_upload_percent >= 70:
            base_weight += 0.35
        elif event_upload_percent >= 55:
            base_weight += 0.2

        weighted_score += base_weight * decay
        recent_instability_events += 1

        if latest_minutes is None or minutes_ago < latest_minutes:
            latest_minutes = int(minutes_ago)
            latest_issue_diagnosis = event_diagnosis

    if facts.get("buffering_detected"):
        weighted_score += 1.6
    if facts.get("sustained_upload_saturation") or facts.get("sustained_upload_high"):
        weighted_score += 0.9
    elif avg_upload_percent >= 85:
        weighted_score += 0.7
    elif avg_upload_percent >= 70:
        weighted_score += 0.45
    elif facts.get("burst_upload_saturation"):
        weighted_score += 0.12
    elif facts.get("upload_is_bursty"):
        weighted_score += 0.05

    if recent_buffer_events >= 3 or weighted_score >= 2.7:
        memory_pattern = "recurring"
    elif recent_buffer_events >= 2 or weighted_score >= 1.4:
        memory_pattern = "clustered"
    elif weighted_score >= 0.35:
        memory_pattern = "blip"
    else:
        memory_pattern = "none"

    return {
        "weighted_score": round(weighted_score, 2),
        "memory_active": weighted_score >= 0.25,
        "memory_pattern": memory_pattern,
        "recent_buffer_events": recent_buffer_events,
        "recent_instability_events": recent_instability_events,
        "burst_events": burst_events,
        "last_minutes_since_instability": latest_minutes,
        "latest_issue_diagnosis": latest_issue_diagnosis,
    }


def _recent_playback_quality(state: dict, current_diagnosis: str) -> dict:
    facts = state.get("facts", {})
    system = state.get("system", {})
    active_sessions = state.get("plex", {}).get("active_sessions", 0)
    memory = _instability_memory_profile(state)
    recent_buffer_count = memory.get("recent_buffer_events", 0)
    recent_instability_count = memory.get("recent_instability_events", 0)
    memory_weight = float(memory.get("weighted_score", 0) or 0)
    last_minutes = memory.get("last_minutes_since_instability")
    recovered = memory.get("memory_active") and not facts.get("buffering_detected", False)
    memory_pattern = memory.get("memory_pattern", "none")

    upload_limit = float(UPLOAD_LIMIT_MBPS or 0) if UPLOAD_LIMIT_MBPS else 0
    avg_upload_saturation_percent = (
        (float(facts.get("recent_upload_avg_mbps", 0) or 0) / upload_limit) * 100
        if upload_limit > 0
        else 0
    )
    current_upload_saturation_percent = (
        (float(system.get("total_upload_mbps", 0) or 0) / upload_limit) * 100
        if upload_limit > 0
        else 0
    )
    sustained_wan_constraint = bool(facts.get("sustained_upload_saturation") or facts.get("sustained_upload_high"))
    burst_only_upload = bool(facts.get("burst_upload_saturation") and not sustained_wan_constraint)
    average_serviceable_but_spiky = (
        facts.get("upload_is_bursty")
        and 35 <= avg_upload_saturation_percent < 75
        and not sustained_wan_constraint
    )

    score = 100
    if active_sessions == 0:
        score = 98
    elif active_sessions == 1:
        score -= 8
    else:
        score -= min(12 + (active_sessions - 2) * 4, 24)

    if sustained_wan_constraint and avg_upload_saturation_percent >= 85:
        score -= 28
    elif sustained_wan_constraint and avg_upload_saturation_percent >= 70:
        score -= 18
    elif avg_upload_saturation_percent >= 50:
        score -= 10
    elif avg_upload_saturation_percent >= 20:
        score -= 5

    if burst_only_upload:
        score -= 1
    elif facts.get("upload_is_bursty"):
        # Bursty delivery can feel fine on average but still leave playback
        # vulnerable to short stalls when upload briefly spikes.
        score -= 4 if avg_upload_saturation_percent >= 60 else 2

    if facts.get("buffering_detected", False):
        score -= 38
    if recent_buffer_count > 0 or memory_pattern in {"clustered", "recurring"} or sustained_wan_constraint:
        score -= min(26, int(round(memory_weight * 11)))
    elif memory_pattern == "blip":
        score -= min(4, int(round(memory_weight * 4)))

    if facts.get("healthy_playing_session_count", 0) > 0:
        score += min(6, facts.get("healthy_playing_session_count", 0) * 2)

    score = max(0, min(100, score))

    fragile_delivery = (
        sustained_wan_constraint
        or avg_upload_saturation_percent >= 70
        or (recent_buffer_count > 0 and avg_upload_saturation_percent >= 50)
        or (recent_buffer_count > 0 and facts.get("upload_is_bursty"))
        or memory_pattern in {"clustered", "recurring"}
    )
    recurrence_risk = "low"
    if facts.get("buffering_detected", False) and fragile_delivery:
        recurrence_risk = "high"
    elif memory_pattern == "recurring":
        recurrence_risk = "high"
    elif (recent_buffer_count > 0 and fragile_delivery) or memory_pattern == "clustered":
        recurrence_risk = "guarded"
    elif sustained_wan_constraint and avg_upload_saturation_percent >= 70:
        recurrence_risk = "guarded"
    elif memory_pattern == "blip":
        recurrence_risk = "watch"

    if (
        facts.get("buffering_detected")
        and not sustained_wan_constraint
        and facts.get("upload_is_bursty")
        and avg_upload_saturation_percent >= 50
    ):
        quality_label = "Intermittently unstable"
    elif recovered and recurrence_risk in {"guarded", "high"}:
        quality_label = "Recovered, recurrence risk remains"
    elif not facts.get("buffering_detected", False) and facts.get("upload_is_bursty") and avg_upload_saturation_percent >= 60 and not burst_only_upload:
        quality_label = "Stable now, but burst-sensitive"
    elif score >= 92:
        quality_label = "Excellent"
    elif score >= 78:
        quality_label = "Stable"
    elif score >= 60:
        quality_label = "Watch"
    else:
        quality_label = "Degraded"

    if facts.get("buffering_detected", False) and fragile_delivery:
        headroom_label = "Limited"
    elif recovered and recurrence_risk in {"guarded", "high"}:
        headroom_label = "Usable but fragile"
    elif avg_upload_saturation_percent >= 85 or sustained_wan_constraint:
        headroom_label = "Tight"
    elif recent_buffer_count > 0 or avg_upload_saturation_percent >= 70 or (sustained_wan_constraint and avg_upload_saturation_percent >= 60):
        headroom_label = "Guarded"
    elif avg_upload_saturation_percent >= 50:
        headroom_label = "Moderate"
    else:
        headroom_label = "Comfortable"

    if facts.get("buffering_detected", False):
        note = "Playback is currently buffering and delivery confidence is reduced."
    elif average_serviceable_but_spiky and recent_buffer_count > 0:
        note = "Average upload is serviceable, but short spikes are causing intermittent delivery instability."
        if recovered:
            note += " Playback is mostly stable between spikes, but burst behavior leaves limited smooth-delivery margin."
    elif recent_buffer_count > 0 and last_minutes is not None:
        note = "Brief buffering was detected {} time{} in the last {} minute{}.".format(
            recent_buffer_count,
            "" if recent_buffer_count == 1 else "s",
            max(last_minutes, 1),
            "" if max(last_minutes, 1) == 1 else "s",
        )
        if recovered:
            note += " Playback is currently stable, but recent buffering indicates reduced delivery confidence."
    elif recent_instability_count > 0 and last_minutes is not None:
        note = "Playback is currently stable, but minor instability occurred recently."
    elif average_serviceable_but_spiky and active_sessions > 0 and not burst_only_upload:
        note = "Average upload is serviceable, but burst behavior leaves limited smooth-delivery margin."
    elif active_sessions > 0:
        note = "Playback is currently stable and the system is handling active Plex delivery normally."
    else:
        note = "No active playback is currently running."

    if recovered and recurrence_risk in {"guarded", "high"} and "reduced delivery confidence" not in note:
        note += " Playback recovered, but recent WAN tightness suggests buffering may recur."
    elif not facts.get("buffering_detected", False) and recurrence_risk == "guarded" and active_sessions > 0:
        note = "Playback is currently stable, but recent burst behavior suggests renewed buffering is plausible if conditions hold."
    elif burst_only_upload and recent_buffer_count == 0 and not facts.get("buffering_detected", False):
        note = "Playback is currently stable. Short upload spikes were observed, but they do not look like a sustained delivery constraint."

    if headroom_label == "Comfortable":
        headroom_summary = "Current WAN delivery margin looks comfortable for the present playback load."
    elif headroom_label == "Moderate":
        headroom_summary = "Average WAN load is serviceable, but additional heavy playback would reduce margin."
    elif headroom_label == "Guarded":
        headroom_summary = (
            "Average load is serviceable, but recent burst behavior suggests buffering may recur."
            if average_serviceable_but_spiky
            else "Playback is mostly stable now, but recent upload behavior suggests reduced delivery margin."
        )
    elif headroom_label == "Usable but fragile":
        headroom_summary = "Usable but fragile headroom remains. Similar upload spikes could trigger renewed playback instability."
    elif headroom_label == "Limited":
        headroom_summary = "Current headroom is limited. Additional heavy WAN playback could trigger more buffering."
    else:
        headroom_summary = "Current margin is tight enough that additional load could quickly degrade playback."

    if recurrence_risk == "high":
        recurrence_summary = "Recurrence risk is elevated because buffering or clustered instability is occurring under tight or volatile WAN delivery conditions."
    elif recurrence_risk == "guarded":
        recurrence_summary = (
            "If current conditions remain unchanged, buffering may recur during future upload spikes."
            if average_serviceable_but_spiky
            else "Recurrence risk remains guarded because recent instability coincided with weak or burst-sensitive WAN delivery margin."
        )
    elif recurrence_risk == "watch":
        recurrence_summary = "Delivery remains worth watching because a recent blip or tighter upload margin could still reappear."
    else:
        recurrence_summary = "No strong sign of near-term recurrence is currently present."

    if (
        (recovered or recent_buffer_count > 0 or memory_pattern in {"clustered", "recurring"})
        and recurrence_risk in {"guarded", "high"}
    ):
        if facts.get("upload_is_bursty") and avg_upload_saturation_percent >= 55:
            delivery_diagnosis_label = "Recovered instability under tight WAN conditions" if recovered else "Burst-sensitive WAN delivery"
        elif avg_upload_saturation_percent >= 70 or sustained_wan_constraint:
            delivery_diagnosis_label = "Recovered WAN-limited delivery" if recovered else "Intermittent delivery instability"
        else:
            delivery_diagnosis_label = "Intermittent delivery instability"
    elif not facts.get("buffering_detected", False) and facts.get("upload_is_bursty") and avg_upload_saturation_percent >= 60 and not burst_only_upload:
        delivery_diagnosis_label = "Burst-sensitive WAN delivery"
    else:
        delivery_diagnosis_label = None

    score_drivers = []
    if recent_buffer_count > 0:
        score_drivers.append("recent buffering")
    elif recent_instability_count > 0:
        score_drivers.append("recent instability memory")
    if facts.get("upload_is_bursty"):
        score_drivers.append("upload burstiness")
    if sustained_wan_constraint or avg_upload_saturation_percent >= 70:
        score_drivers.append("near-cap WAN margin")
    elif avg_upload_saturation_percent >= 50:
        score_drivers.append("tighter WAN margin")
    if active_sessions >= 2:
        score_drivers.append("multiple active sessions")
    if facts.get("buffering_detected"):
        score_drivers.append("active buffering")
    if (
        system.get("host_cpu_percent", 0) < 60
        and system.get("host_ram_percent", 0) < 85
        and system.get("iowait_percent", 0) < 8
    ):
        score_drivers.append("CPU/RAM/disk not primary")
    score_drivers = score_drivers[:4]
    if score_drivers:
        if len(score_drivers) == 1:
            driver_sentence = score_drivers[0].capitalize() + "."
        elif len(score_drivers) == 2:
            driver_sentence = "{} and {} are the main drivers.".format(score_drivers[0].capitalize(), score_drivers[1])
        else:
            driver_sentence = "{} are the main drivers.".format(", ".join(score_drivers[:-1]) + ", and " + score_drivers[-1])
            driver_sentence = driver_sentence[0].upper() + driver_sentence[1:]
    else:
        driver_sentence = "No major negative drivers are currently standing out."

    if facts.get("buffering_detected", False):
        delivery_confidence = "Low"
        delivery_confidence_summary = "Playback smoothness is currently reduced by active buffering, even though infrastructure may still be healthy."
    elif recurrence_risk in {"guarded", "high"}:
        delivery_confidence = "Guarded"
        delivery_confidence_summary = "Playback is stable now, but delivery confidence remains guarded because upload volatility or recent buffering suggests renewed stalls are plausible."
    elif recurrence_risk == "watch":
        delivery_confidence = "Watch"
        delivery_confidence_summary = "Playback is currently serviceable, but recent behavior suggests delivery confidence is not fully settled."
    elif burst_only_upload:
        delivery_confidence = "High"
        delivery_confidence_summary = "Playback is currently stable. Upload is bursty, but the pattern does not currently look sustained enough to threaten delivery."
    else:
        delivery_confidence = "High"
        delivery_confidence_summary = "Playback is currently stable and delivery confidence is high."

    return {
        "quality_score": score,
        "quality_label": quality_label,
        "headroom_label": headroom_label,
        "headroom_summary": headroom_summary,
        "recent_buffer_count": recent_buffer_count,
        "recent_instability_count": recent_instability_count,
        "recent_window_active": bool(memory.get("memory_active")),
        "recovered": recovered,
        "fragile_delivery": fragile_delivery,
        "last_minutes_since_instability": last_minutes,
        "note": note,
        "memory_weight": memory_weight,
        "memory_pattern": memory_pattern,
        "delivery_confidence_label": delivery_confidence,
        "delivery_confidence_summary": delivery_confidence_summary,
        "recurrence_risk_label": recurrence_risk.capitalize(),
        "recurrence_summary": recurrence_summary,
        "score_driver_items": score_drivers,
        "score_driver_summary": driver_sentence,
        "delivery_diagnosis_label": delivery_diagnosis_label,
        "manager_diagnosis_label": (
            delivery_diagnosis_label
            if delivery_diagnosis_label
            else "Recent playback instability recovered"
            if recovered and recent_buffer_count > 0
            else "Minor playback instability"
            if facts.get("buffering_detected", False) and current_diagnosis == "none_detected"
            else "Intermittent delivery instability"
            if recurrence_risk in {"guarded", "high"} and current_diagnosis == "none_detected"
            else None
        ),
        "home_diagnosis_label": (
            delivery_diagnosis_label
            if delivery_diagnosis_label
            else "Recent playback instability recovered"
            if recovered and recent_buffer_count > 0 and current_diagnosis == "none_detected"
            else "Minor playback instability"
            if facts.get("buffering_detected", False) and current_diagnosis == "none_detected"
            else "Stable now, but burst-sensitive"
            if recurrence_risk in {"guarded", "high"} and current_diagnosis == "none_detected"
            else None
        ),
    }


def build_manager_summary(
    state: dict,
    structured_diagnosis: dict,
    issue_metadata: dict,
    action_plan: dict,
    history_summary: dict,
    state_change: dict,
    diagnosis_presentation: dict,
) -> dict:
    facts = state.get("facts", {})
    diagnosis = diagnosis_presentation.get("primary_diagnosis", structured_diagnosis.get("most_likely_cause", "unknown"))
    severity = diagnosis_presentation.get("severity", issue_metadata.get("severity", "info"))
    scope = diagnosis_presentation.get("scope", issue_metadata.get("scope", "unknown"))
    recent_issue = _recent_issue_context(state, diagnosis)
    playback_quality = _recent_playback_quality(state, diagnosis)

    if severity == "critical":
        service_health = "Critical"
        service_health_tone = "critical"
    elif playback_quality.get("recent_window_active") and (
        diagnosis == "none_detected"
        or playback_quality.get("recent_buffer_count", 0) > 0
        or playback_quality.get("recurrence_risk_label") in {"Guarded", "High"}
    ):
        service_health = "Recovered / Monitor"
        service_health_tone = "recovered"
    elif severity == "warning" and scope in {"client_specific", "session_specific"}:
        service_health = "Healthy (localized issue)"
        service_health_tone = "localized"
    elif severity == "warning":
        service_health = "Degraded"
        service_health_tone = "warning"
    else:
        service_health = "Healthy"
        service_health_tone = "healthy"

    if diagnosis == "none_detected" and playback_quality.get("recent_window_active"):
        impact_summary = playback_quality.get("note")
    elif diagnosis == "transcoding" and playback_quality.get("recurrence_risk_label") in {"Guarded", "High"}:
        impact_summary = "A localized compatibility transcode is active, but recent WAN tightness suggests playback could become unstable again under similar spikes."
    elif playback_quality.get("recurrence_risk_label") in {"Guarded", "High"} and facts.get("healthy_playing_session_count", 0) > 0:
        impact_summary = "Playback is currently available, but recent WAN tightness suggests limited delivery margin and possible recurrence."
    elif diagnosis == "none_detected":
        impact_summary = "Playback is healthy and no broader service risk is indicated."
    elif diagnosis == "transcoding" and severity == "info":
        impact_summary = "A limited client compatibility transcode is active with no broad service impact."
    elif diagnosis == "transcoding" and severity == "warning":
        impact_summary = "A localized transcode is active and should be monitored, but broad service impact is not currently indicated."
    elif facts.get("buffering_session_count", 0) > 1:
        impact_summary = (
            f'{facts.get("buffering_session_count", 0)} sessions are currently affected, '
            "which suggests broader service impact."
        )
    elif facts.get("buffering_session_count", 0) == 1:
        impact_summary = "1 session is currently affected, with no evidence of a broad service failure."
    else:
        impact_summary = "An issue is present, but active user impact appears limited."

    change_type = state_change.get("change_type", "no_material_change")
    if change_type == "worsening_issue":
        trend_summary = "The current issue appears to be worsening."
    elif change_type == "improving_issue":
        trend_summary = "The current issue appears to be improving."
    elif change_type == "ongoing_issue":
        trend_summary = "The current issue is ongoing without a major shape change."
    elif change_type == "resolved_issue":
        trend_summary = "A previously active issue appears to have resolved."
    elif history_summary.get("events_last_24h", 0) > 0:
        top_diagnosis = history_summary.get("top_diagnosis_last_24h") or "unknown"
        trend_summary = (
            f'{history_summary.get("events_last_24h", 0)} notable events were logged in the last 24 hours; '
            f'the most common diagnosis was {top_diagnosis}.'
        )
    else:
        trend_summary = "No meaningful recent issue trend is available yet."

    contributing_summary = None
    manager_contributing_factors = diagnosis_presentation.get("manager_contributing_factors", [])
    if manager_contributing_factors:
        if len(manager_contributing_factors) >= 2:
            contributing_summary = (
                f"{manager_contributing_factors[0].capitalize()} and "
                f"{manager_contributing_factors[1]} are contributing."
            )
        else:
            contributing_summary = manager_contributing_factors[0].capitalize() + " is contributing."

    escalation_needed = severity == "critical" or bool(
        action_plan.get("escalate_if") and severity == "warning" and facts.get("buffering_session_count", 0) > 1
    )
    confidence = diagnosis_presentation.get("confidence", issue_metadata.get("confidence", "low"))
    user_impact = _manager_user_impact_label(facts, severity, scope, playback_quality)
    action_urgency = _manager_action_urgency(severity, scope, escalation_needed, facts, playback_quality)
    trend_judgment = _manager_trend_judgment(state_change, facts, playback_quality)
    why_not_worse = _manager_why_not_worse(facts, state.get("system", {}), structured_diagnosis)
    escalation_triggers = _manager_escalation_triggers(action_plan, scope)
    affected_scope_summary = _manager_affected_scope_summary(facts, scope)
    recommendation_ladder = _manager_recommendation_ladder(diagnosis, severity, action_plan, playback_quality)
    impact_breakdown = _manager_impact_breakdown(facts, state.get("system", {}), diagnosis, severity, scope)

    if severity == "critical":
        executive_conclusion = "Service quality is materially degraded and needs active intervention."
    elif playback_quality.get("recovered") and playback_quality.get("recurrence_risk_label") in {"Guarded", "High"}:
        executive_conclusion = "Playback is currently stable, but recent buffering indicates reduced delivery confidence under tighter WAN conditions."
    elif playback_quality.get("recent_window_active"):
        executive_conclusion = "Playback is currently stable, though recent instability suggests continued monitoring."
    elif scope in {"session_specific", "client_specific"} and severity == "info":
        executive_conclusion = "This is a localized low-impact issue with no current sign of broader service degradation."
    elif scope in {"session_specific", "client_specific"}:
        executive_conclusion = "This is a localized issue that warrants attention but does not currently threaten the broader service."
    else:
        executive_conclusion = "This issue should be monitored as a broader service risk."

    if diagnosis == "none_detected" and playback_quality.get("recent_window_active"):
        executive_conclusion = playback_quality.get("note")
    elif diagnosis == "none_detected":
        executive_conclusion = "No active issue is currently affecting service availability."

    return {
        "service_health": service_health,
        "service_health_tone": service_health_tone,
        "impact_summary": impact_summary,
        "issue_scope": scope,
        "severity": severity,
        "severity_display_label": severity_display_label(severity),
        "confidence": confidence,
        "current_diagnosis": diagnosis,
        "current_diagnosis_label": playback_quality.get(
            "manager_diagnosis_label",
            diagnosis_presentation.get("primary_diagnosis_label", _humanize_diagnosis(diagnosis)),
        ),
        "contributing_summary": contributing_summary,
        "recommended_action": build_manager_recommended_action(
            diagnosis_presentation.get("primary_action", action_plan.get("primary_action", "No immediate action needed.")),
            diagnosis,
            severity,
        ),
        "insight": build_manager_insight(state, {
            "service_health": service_health,
            "severity": severity,
            "scope": scope,
            "current_diagnosis": diagnosis,
            "impact_summary": impact_summary,
        }, diagnosis_presentation),
        "escalation_needed": escalation_needed,
        "trend_summary": trend_summary,
        "executive_decision_summary": {
            "user_impact": user_impact,
            "action_urgency": action_urgency,
            "confidence": confidence,
            "conclusion": executive_conclusion,
        },
        "why_this_is_not_worse": why_not_worse,
        "escalation_triggers": escalation_triggers,
        "affected_scope_summary": affected_scope_summary,
        "trend_judgment": trend_judgment,
        "recommendation_ladder": recommendation_ladder,
        "impact_breakdown": impact_breakdown,
        "recurrence_risk_label": playback_quality.get("recurrence_risk_label", "Low"),
        "recurrence_summary": playback_quality.get("recurrence_summary", ""),
        "recent_issue_active": recent_issue.get("recent_issue_active", False),
        "recent_issue_stage": recent_issue.get("recent_issue_stage"),
        "minutes_since_recent_issue": recent_issue.get("minutes_since_recent_issue"),
        "recent_playback_note": recent_issue.get("recent_playback_note", ""),
        "playback_quality": playback_quality,
    }


def build_manager_insight(state: dict, manager_summary: dict, diagnosis_presentation: dict) -> str:
    facts = state.get("facts", {})
    severity = manager_summary.get("severity", "info")
    scope = manager_summary.get("scope", manager_summary.get("issue_scope", "unknown"))
    diagnosis = manager_summary.get("current_diagnosis", diagnosis_presentation.get("primary_diagnosis", "unknown"))

    if diagnosis == "none_detected":
        playback_quality = manager_summary.get("playback_quality", {})
        if playback_quality.get("recent_window_active"):
            return playback_quality.get("note", "Playback is stable, but recent behavior still warrants monitoring.")
        if manager_summary.get("recent_issue_active"):
            return manager_summary.get("recent_playback_note", "Playback is stable, but recent behavior still warrants monitoring.")
        return "No active issue is currently affecting service availability."
    playback_quality = manager_summary.get("playback_quality", {})
    if playback_quality.get("recurrence_risk_label") in {"Guarded", "High"} and diagnosis in {"transcoding", "upload_saturation", "network_throughput_issue"}:
        return "Playback is functioning now, but recent WAN tightness suggests renewed buffering is plausible if similar delivery spikes return."
    if diagnosis == "transcoding" and severity == "info":
        return "This appears to be a limited session-specific compatibility transcode, not a broader service issue."
    if diagnosis == "transcoding" and severity == "warning" and facts.get("buffering_session_count", 0) == 0:
        return "This appears to be a localized transcode with limited current impact, but it should be watched for worsening playback quality."
    if severity == "critical":
        return "This appears to be affecting service quality more broadly and may require escalation."
    if scope in {"client_specific", "session_specific"} and facts.get("buffering_session_count", 0) <= 1:
        return "This appears isolated to a specific client or session and does not indicate a broad service issue."
    if scope in {"system_wide", "service_wide"}:
        return "This appears broader than a single client and should be monitored as a service-level issue."
    return "This issue currently appears limited, but it should be monitored for broader impact."


def build_manager_recommended_action(primary_action: str, diagnosis: str, severity: str = "info") -> str:
    action_overrides = {
        "client_file_compatibility_issue": "Validate playback on another client to confirm whether the issue is device-specific.",
        "client_network_path_sensitivity": "Validate playback on another client to confirm whether the issue is device-specific.",
        "client_or_network": "Validate playback on another client to check whether the issue remains localized.",
        "network_throughput_issue": "Compare delivered bandwidth against expected playback to confirm whether the issue is path-related.",
    }
    if diagnosis == "transcoding" and severity == "info":
        return "No urgent action is needed unless playback quality degrades."
    return action_overrides.get(diagnosis, primary_action)


def build_history_event_summary(event: dict) -> str:
    diagnosis = event.get("diagnosis", "unknown")
    scope = event.get("scope", "unknown")
    state_change = (event.get("state_change") or {}).get("change_type", "no_material_change")
    buffering_sessions = event.get("buffering_sessions", []) or []
    affected_sessions = event.get("affected_sessions", []) or buffering_sessions
    affected_clients = event.get("affected_clients", []) or []
    severity = event.get("severity", "info")
    transcode_count = event.get("transcode_count", 0)

    if state_change == "resolved_issue":
        return "Issue resolved and service returned to healthy."
    if diagnosis == "none_detected":
        return "No active issues detected."
    if diagnosis == "transcoding":
        if severity == "info":
            return "Low-impact client compatibility transcode with no broader service issue."
        if severity == "warning":
            return "Session-specific transcoding is active without broader service degradation."
        if transcode_count > 1:
            return "Multi-session transcoding is materially affecting playback."
        return "Transcoding is materially affecting playback."
    if diagnosis == "upload_saturation" and len(buffering_sessions) > 1:
        return "Multiple sessions buffering simultaneously."
    if diagnosis == "client_network_path_sensitivity":
        if affected_clients:
            return "Localized playback issue on {}.".format(affected_clients[0])
        return "Localized client-specific playback issue."
    if diagnosis == "client_file_compatibility_issue":
        if affected_clients:
            return "Localized playback compatibility issue on {}.".format(affected_clients[0])
        return "Localized playback compatibility issue."
    if scope in {"client_specific", "session_specific"} and len(affected_sessions) == 1:
        return "Single client buffering while others are healthy."
    if state_change == "ongoing_issue":
        return "Current issue remains ongoing."
    if state_change == "worsening_issue":
        return "Current issue is affecting more playback activity."
    if state_change == "improving_issue":
        return "Current issue appears to be improving."
    return "{}.".format(diagnosis_display_label(diagnosis))


def build_history_display_event(event: dict) -> dict:
    state_change = event.get("state_change") or {}
    diagnosis = event.get("diagnosis", "unknown")
    severity = event.get("severity", "info") or "info"
    scope = event.get("scope", "unknown") or "unknown"
    affected_sessions = event.get("affected_sessions", []) or event.get("buffering_sessions", []) or []
    affected_clients = event.get("affected_clients", []) or []

    return {
        **event,
        "timestamp_label": _format_history_timestamp(event.get("timestamp", "unknown")),
        "diagnosis_label": diagnosis_display_label(diagnosis),
        "summary_label": build_history_event_summary(event),
        "state_change_label": state_change_display_label(state_change.get("change_type", "no_material_change")),
        "severity_label": severity_display_label(severity),
        "severity_class": "severity-{}".format(severity),
        "scope_label": scope,
        "sessions_label": ", ".join(str(item) for item in affected_sessions if item) or "none",
        "clients_label": ", ".join(str(item) for item in affected_clients if item) or "none",
    }
EASTERN_TZ = ZoneInfo("America/New_York")
