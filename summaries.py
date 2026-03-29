def _humanize_diagnosis(diagnosis: str) -> str:
    return diagnosis.replace("_", " ")


_OPERATOR_FACTOR_LABELS = {
    "sensitive_client_type": "Mac client sensitivity",
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
    "sensitive_client_type": "device-specific playback factors present",
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
    "sensitive_client_type": "Mac client sensitivity",
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
    "transcoding": "Transcoding overload",
}

_STATE_CHANGE_LABELS = {
    "new_issue": "New issue detected",
    "ongoing_issue": "Issue ongoing",
    "worsening_issue": "Impact increasing",
    "improving_issue": "Impact improving",
    "resolved_issue": "Issue resolved",
    "no_material_change": "No material change",
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
    if primary_diagnosis == "none_detected":
        return "No confirmed issue"
    return _humanize_diagnosis(primary_diagnosis).capitalize()


def diagnosis_display_label(diagnosis: str) -> str:
    return _HISTORY_DIAGNOSIS_LABELS.get(diagnosis, _humanize_diagnosis(diagnosis).capitalize())


def state_change_display_label(change_type: str) -> str:
    return _STATE_CHANGE_LABELS.get(change_type, _humanize_diagnosis(change_type).capitalize())


def _diagnosis_supporting_text(primary_diagnosis: str, contributing_factors: list, buffering_confirmed: bool) -> str:
    if not buffering_confirmed and primary_diagnosis == "none_detected":
        if contributing_factors:
            return "No active issue is confirmed, but playback risk factors are present."
        return "No active issue is confirmed right now."

    if primary_diagnosis in {"client_network_path_sensitivity", "client_file_compatibility_issue"}:
        if "sensitive_client_type" in contributing_factors and "bursty_upload_pattern" in contributing_factors:
            return "This looks primarily like a localized playback issue, with device sensitivity and bursty delivery also contributing."
        if "sensitive_client_type" in contributing_factors and "client_file_compatibility_traits" in contributing_factors:
            return "This looks primarily like a localized playback issue, with device sensitivity and file playback traits both contributing."
        if "client_file_compatibility_traits" in contributing_factors:
            return "This appears localized to the affected client/session, with file playback traits also contributing."
        if "bursty_upload_pattern" in contributing_factors:
            return "This appears localized to the affected client/session, with bursty delivery also contributing."

    if primary_diagnosis == "client_or_network":
        return "This appears localized rather than service-wide."

    return ""


def build_contributing_factors(state: dict, structured_diagnosis: dict, issue_metadata: dict) -> list:
    facts = state.get("facts", {})
    sessions = facts.get("session_facts", [])
    diagnosis = structured_diagnosis.get("most_likely_cause", "unknown")
    buffering_confirmed = structured_diagnosis.get("buffering_confirmed", False)

    factor_conditions = {
        "sensitive_client_type": (
            facts.get("buffering_sessions_have_client_trait_risk")
            or facts.get("client_trait_risk_detected")
        ),
        "client_file_compatibility_traits": (
            facts.get("buffering_sessions_have_file_trait_risk")
            or facts.get("buffering_sessions_have_compatibility_pattern")
            or (not buffering_confirmed and facts.get("file_trait_risk_detected"))
        ),
        "bursty_upload_pattern": facts.get("upload_is_bursty"),
        "delivery_throughput_mismatch": (
            facts.get("buffering_sessions_have_delivery_issue")
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

    priority_by_diagnosis = {
        "client_network_path_sensitivity": [
            "sensitive_client_type",
            "same_content_healthy_elsewhere",
            "client_file_compatibility_traits",
            "bursty_upload_pattern",
            "delivery_throughput_mismatch",
            "mixed_session_health",
            "high_bitrate_session",
        ],
        "client_file_compatibility_issue": [
            "client_file_compatibility_traits",
            "sensitive_client_type",
            "image_subtitles_present",
            "mixed_session_health",
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
            "sensitive_client_type",
        ],
        "client_or_network": [
            "sensitive_client_type",
            "delivery_throughput_mismatch",
            "mixed_session_health",
            "same_content_healthy_elsewhere",
            "client_file_compatibility_traits",
        ],
        "none_detected": [
            "sensitive_client_type",
            "client_file_compatibility_traits",
            "high_bitrate_session",
        ],
    }

    selected = []
    for factor in priority_by_diagnosis.get(
        diagnosis,
        [
            "sensitive_client_type",
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
        "scope": issue_metadata.get("scope", "unknown"),
        "confidence": issue_metadata.get("confidence", "low"),
        "primary_action": action_plan.get("primary_action", "No immediate action needed."),
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

    if severity == "critical":
        service_health = "Critical"
        service_health_tone = "critical"
    elif severity == "warning" and scope in {"client_specific", "session_specific"}:
        service_health = "Healthy (localized issue)"
        service_health_tone = "localized"
    elif severity == "warning":
        service_health = "Degraded"
        service_health_tone = "warning"
    else:
        service_health = "Healthy"
        service_health_tone = "healthy"

    if diagnosis == "none_detected":
        impact_summary = "No active issues are currently confirmed."
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

    return {
        "service_health": service_health,
        "service_health_tone": service_health_tone,
        "impact_summary": impact_summary,
        "issue_scope": scope,
        "severity": severity,
        "current_diagnosis": diagnosis,
        "current_diagnosis_label": diagnosis_presentation.get("primary_diagnosis_label", _humanize_diagnosis(diagnosis)),
        "contributing_summary": contributing_summary,
        "recommended_action": build_manager_recommended_action(
            diagnosis_presentation.get("primary_action", action_plan.get("primary_action", "No immediate action needed.")),
            diagnosis,
        ),
        "insight": build_manager_insight(state, {
            "service_health": service_health,
            "severity": severity,
            "scope": scope,
            "current_diagnosis": diagnosis,
            "impact_summary": impact_summary,
        }, diagnosis_presentation),
        "escalation_needed": severity == "critical" or bool(action_plan.get("escalate_if") and severity == "warning" and facts.get("buffering_session_count", 0) > 1),
        "trend_summary": trend_summary,
    }


def build_manager_insight(state: dict, manager_summary: dict, diagnosis_presentation: dict) -> str:
    facts = state.get("facts", {})
    severity = manager_summary.get("severity", "info")
    scope = manager_summary.get("scope", manager_summary.get("issue_scope", "unknown"))
    diagnosis = manager_summary.get("current_diagnosis", diagnosis_presentation.get("primary_diagnosis", "unknown"))

    if diagnosis == "none_detected":
        return "No active issue is currently affecting service availability."
    if severity == "critical":
        return "This appears to be affecting service quality more broadly and may require escalation."
    if scope in {"client_specific", "session_specific"} and facts.get("buffering_session_count", 0) <= 1:
        return "This appears isolated to a specific client or session and does not indicate a broad service issue."
    if scope in {"system_wide", "service_wide"}:
        return "This appears broader than a single client and should be monitored as a service-level issue."
    return "This issue currently appears limited, but it should be monitored for broader impact."


def build_manager_recommended_action(primary_action: str, diagnosis: str) -> str:
    action_overrides = {
        "client_file_compatibility_issue": "Validate playback on another client to confirm whether the issue is device-specific.",
        "client_network_path_sensitivity": "Validate playback on another client to confirm whether the issue is device-specific.",
        "client_or_network": "Validate playback on another client to check whether the issue remains localized.",
        "network_throughput_issue": "Compare delivered bandwidth against expected playback to confirm whether the issue is path-related.",
    }
    return action_overrides.get(diagnosis, primary_action)


def build_history_event_summary(event: dict) -> str:
    diagnosis = event.get("diagnosis", "unknown")
    scope = event.get("scope", "unknown")
    state_change = (event.get("state_change") or {}).get("change_type", "no_material_change")
    buffering_sessions = event.get("buffering_sessions", []) or []
    affected_clients = event.get("affected_clients", []) or []

    if state_change == "resolved_issue":
        return "Issue resolved and service returned to healthy."
    if diagnosis == "none_detected":
        return "No active issues detected."
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
    if scope in {"client_specific", "session_specific"} and len(buffering_sessions) == 1:
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
    severity = event.get("severity", "info")

    return {
        **event,
        "diagnosis_label": diagnosis_display_label(diagnosis),
        "summary_label": build_history_event_summary(event),
        "state_change_label": state_change_display_label(state_change.get("change_type", "no_material_change")),
        "severity_class": "severity-{}".format(severity),
    }
