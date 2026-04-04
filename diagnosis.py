from config import (
    CPU_WARN_PERCENT,
    HIGH_BITRATE_KBPS,
    IOWAIT_WARN_PERCENT,
    NON_PLEX_UPLOAD_WARN_MBPS,
    RAM_WARN_PERCENT,
    UPLOAD_LIMIT_MBPS,
    UPLOAD_WARN_MBPS,
)


def _system_is_healthy(system: dict) -> bool:
    return (
        system.get("host_cpu_percent", 0) < CPU_WARN_PERCENT
        and system.get("host_ram_percent", 0) < RAM_WARN_PERCENT
        and system.get("iowait_percent", 0) < IOWAIT_WARN_PERCENT
    )


def _strict_upload_saturation_evidence(facts: dict, system_healthy: bool, transcodes_active: bool) -> bool:
    return (
        facts.get("buffering_detected")
        and facts.get("sustained_upload_high")
        and facts.get("upload_is_stable")
        and not facts.get("upload_is_bursty")
        and facts.get("upload_mostly_plex")
        and facts.get("remaining_upload_headroom_mbps", 0) < 2
        and facts.get("recent_upload_avg_mbps", 0) > UPLOAD_WARN_MBPS
        and not facts.get("has_mixed_session_health")
        and not facts.get("single_session_buffering_while_others_healthy")
        and not (
            system_healthy
            and not transcodes_active
            and facts.get("healthy_playing_session_count", 0) > 0
        )
    )


def _system_pressure_profile(system: dict, facts: dict) -> dict:
    # Mild pressure means we should watch localized issues more closely, but not
    # automatically treat them as broad service failures.
    mild_pressure = any(
        [
            system.get("host_cpu_percent", 0) >= CPU_WARN_PERCENT,
            system.get("host_ram_percent", 0) >= RAM_WARN_PERCENT,
            system.get("iowait_percent", 0) >= IOWAIT_WARN_PERCENT,
            system.get("plex_cpu_host_percent", 0) >= 35,
        ]
    )

    # Severe pressure is reserved for states that can plausibly degrade service
    # beyond one session.
    severe_pressure = any(
        [
            system.get("host_cpu_percent", 0) >= 85,
            system.get("host_ram_percent", 0) >= 92,
            system.get("iowait_percent", 0) >= 20,
            system.get("plex_cpu_host_percent", 0) >= 60,
            facts.get("sustained_upload_high", False),
        ]
    )

    return {
        "mild_pressure": mild_pressure,
        "severe_pressure": severe_pressure,
    }


def _transcode_impact_profile(session_facts: list[dict]) -> dict:
    transcode_sessions = [sf for sf in session_facts if sf.get("is_transcode")]
    audio_only_transcodes = [
        sf
        for sf in transcode_sessions
        if sf.get("audio_decision") == "transcode"
        and sf.get("video_decision") != "transcode"
        and sf.get("container_decision") != "transcode"
    ]
    video_transcodes = [
        sf
        for sf in transcode_sessions
        if sf.get("video_decision") == "transcode" or sf.get("container_decision") == "transcode"
    ]
    subtitle_transcodes = [
        sf
        for sf in transcode_sessions
        if sf.get("subtitle_decision") == "transcode" or sf.get("is_image_subtitle")
    ]

    return {
        "transcode_sessions": transcode_sessions,
        "transcode_session_count": len(transcode_sessions),
        "audio_only_transcode_count": len(audio_only_transcodes),
        "video_transcode_count": len(video_transcodes),
        "subtitle_transcode_count": len(subtitle_transcodes),
    }


def _shared_resource_constraint(system: dict, facts: dict) -> bool:
    upload_limit = float(UPLOAD_LIMIT_MBPS or 0) if UPLOAD_LIMIT_MBPS else 0
    avg_upload_saturation_percent = (
        (float(facts.get("recent_upload_avg_mbps", 0) or 0) / upload_limit) * 100
        if upload_limit > 0
        else 0
    )
    return any(
        [
            facts.get("sustained_upload_saturation", False),
            avg_upload_saturation_percent >= 85,
            system.get("host_cpu_percent", 0) >= 85,
            system.get("plex_cpu_host_percent", 0) >= 60,
            system.get("iowait_percent", 0) >= 20,
        ]
    )


def _finalize_scope(
    diagnosis: str,
    scope: str,
    severity: str,
    facts: dict,
    transcode_profile: dict,
    shared_constraint: bool,
) -> str:
    if (
        diagnosis == "transcoding"
        and severity == "info"
        and transcode_profile["transcode_session_count"] <= 1
        and not shared_constraint
        and facts.get("healthy_playing_session_count", 0) > 0
    ):
        # Apply the localized clamp at the shared metadata layer so every
        # downstream consumer, including history logging, sees the same scope.
        return "session_specific"
    return scope


def _impact_assessment(
    state: dict,
    structured_diagnosis: dict,
    scope: str,
    transcode_profile: dict,
    pressure: dict,
    affected_ratio: float,
) -> dict:
    facts = state.get("facts", {})
    system = state.get("system", {})
    plex = state.get("plex", {})
    buffering_count = facts.get("buffering_session_count", 0)
    healthy_sessions = facts.get("healthy_playing_session_count", 0)
    active_sessions = int(plex.get("active_sessions", 0) or 0)
    upload_limit = float(UPLOAD_LIMIT_MBPS or 0) if UPLOAD_LIMIT_MBPS else 0
    avg_upload_saturation_percent = (
        (float(facts.get("recent_upload_avg_mbps", 0) or 0) / upload_limit) * 100
        if upload_limit > 0
        else 0
    )

    score = 0
    driver_weights: dict[str, int] = {}

    def add_driver(name: str, points: int) -> None:
        nonlocal score
        score += points
        driver_weights[name] = driver_weights.get(name, 0) + points

    if buffering_count > 0:
        add_driver("buffering_confirmed", min(25 + (buffering_count - 1) * 15, 55))

    if transcode_profile["video_transcode_count"] > 0:
        add_driver("video_transcoding", min(12 * transcode_profile["video_transcode_count"], 28))
    elif transcode_profile["audio_only_transcode_count"] > 0:
        add_driver("audio_compatibility_transcode", min(4 * transcode_profile["audio_only_transcode_count"], 10))
    elif transcode_profile["subtitle_transcode_count"] > 0:
        add_driver("subtitle_transcoding", min(6 * transcode_profile["subtitle_transcode_count"], 12))

    # Resource pressure only drives impact when telemetry suggests the host is
    # actually working harder, not just because a diagnosis exists.
    if pressure["severe_pressure"]:
        add_driver("host_pressure", 28)
    elif pressure["mild_pressure"]:
        add_driver("host_pressure", 12)

    if facts.get("sustained_upload_saturation") or avg_upload_saturation_percent >= 95:
        add_driver("upload", 30)
    elif facts.get("sustained_upload_high") or avg_upload_saturation_percent >= 85:
        add_driver("upload", 22)
    elif avg_upload_saturation_percent >= 70:
        add_driver("upload", 14)
    elif avg_upload_saturation_percent >= 50:
        add_driver("upload", 7)
    elif avg_upload_saturation_percent >= 20:
        # Keep sub-50% upload in the low-impact range, but acknowledge that it
        # still consumes some real delivery budget.
        add_driver("upload", 2)
    elif facts.get("burst_upload_saturation"):
        # Short-lived spikes are informative, but should not dominate impact
        # unless they coincide with sustained pressure or real playback pain.
        add_driver("upload", 1)
    elif facts.get("upload_is_bursty"):
        score += 1

    if scope in {"system_wide", "service_wide"}:
        add_driver("scope_breadth", 12)
    elif scope in {"session_specific", "client_specific"}:
        score -= 6

    if active_sessions > 0:
        # Healthy active playback is not "free" even when no issue is confirmed.
        # Reserve literal zero for effectively idle states with negligible work.
        add_driver("active_playback_load", min(2 + active_sessions, 6))

    if affected_ratio >= 0.5:
        score += 10
    elif affected_ratio <= 0.2:
        score -= 4

    if healthy_sessions > 0:
        # Healthy direct-play/direct-stream sessions are strong evidence that
        # overall service capacity remains comfortable.
        score -= min(healthy_sessions * 3, 9)

    if (
        transcode_profile["audio_only_transcode_count"] > 0
        and buffering_count == 0
        and not pressure["mild_pressure"]
        and not facts.get("sustained_upload_high")
    ):
        # Low-cost compatibility workarounds should stay calm on a healthy NAS.
        score = min(score, 18)

    if structured_diagnosis.get("most_likely_cause") == "none_detected":
        score = min(score, 12 if active_sessions > 0 else 8)

    if active_sessions == 0 and score <= 3:
        score = max(0, score)
    elif active_sessions > 0:
        score = max(score, 5)

    score = max(0, min(100, int(round(score))))

    if score <= 15:
        impact_label = "Minimal"
    elif score <= 35:
        impact_label = "Low"
    elif score <= 60:
        impact_label = "Moderate"
    elif score <= 80:
        impact_label = "High"
    else:
        impact_label = "Severe"

    if score <= 20:
        capacity_headroom = "Comfortable"
    elif score <= 40:
        capacity_headroom = "Available"
    elif score <= 65:
        capacity_headroom = "Reduced"
    else:
        capacity_headroom = "Tight"

    dominant_driver = max(driver_weights.items(), key=lambda item: item[1])[0] if driver_weights else "none"
    driver_labels = {
        "buffering_confirmed": "buffering is the primary current impact driver",
        "video_transcoding": "video transcoding is the primary current impact driver",
        "audio_compatibility_transcode": "light compatibility transcoding is the primary current impact driver",
        "subtitle_transcoding": "subtitle-driven transcoding is the primary current impact driver",
        "host_pressure": "host or Plex pressure is the primary current impact driver",
        "upload": "upload utilization is the primary current impact driver",
        "scope_breadth": "multi-session breadth is the primary current impact driver",
        "active_playback_load": "active playback load is the primary current impact driver",
        "none": "no meaningful playback strain is currently confirmed",
    }
    secondary_drivers = [name for name, _ in sorted(driver_weights.items(), key=lambda item: item[1], reverse=True) if name != dominant_driver][:2]
    if dominant_driver == "none":
        driver_summary = driver_labels["none"]
    elif secondary_drivers:
        secondary_labels = {
            "buffering_confirmed": "buffering is also contributing",
            "video_transcoding": "video transcoding is also contributing",
            "audio_compatibility_transcode": "compatibility transcoding is also contributing",
            "subtitle_transcoding": "subtitle-driven transcoding is also contributing",
            "host_pressure": "host pressure is also contributing",
            "upload": "upload utilization is also contributing",
            "scope_breadth": "multi-session breadth is also contributing",
            "active_playback_load": "healthy playback load is also contributing",
        }
        driver_summary = "{}; {}.".format(
            driver_labels.get(dominant_driver, driver_labels["none"]).capitalize(),
            secondary_labels.get(secondary_drivers[0], "other factors are also contributing"),
        )
    else:
        driver_summary = driver_labels.get(dominant_driver, driver_labels["none"]).capitalize() + "."

    return {
        "impact_score": score,
        "impact_label": impact_label,
        "capacity_headroom": capacity_headroom,
        "dominant_impact_factor": dominant_driver,
        "impact_driver_summary": driver_summary,
        "capacity_headroom_summary": (
            "Current telemetry suggests the system can comfortably absorb more playback load."
            if capacity_headroom == "Comfortable"
            else "Current telemetry suggests there is still usable playback headroom."
            if capacity_headroom == "Available"
            else "Current telemetry suggests available playback headroom is narrowing."
            if capacity_headroom == "Reduced"
            else "Current telemetry suggests the system is close to its comfortable playback limit."
        ),
    }


def diagnose(state: dict) -> dict:
    reasons = []
    bottleneck = "none"
    health = "healthy"

    host_cpu = state["system"]["host_cpu_percent"]
    host_ram = state["system"]["host_ram_percent"]
    plex_upload = state["system"]["plex_upload_mbps"]
    total_upload = state["system"]["total_upload_mbps"]
    iowait = state["system"]["iowait_percent"]
    transcodes = state["plex"]["transcodes"]
    plex_cpu_host = state["system"]["plex_cpu_host_percent"]

    if plex_upload > UPLOAD_WARN_MBPS and host_cpu < (CPU_WARN_PERCENT / 2):
        bottleneck = "upload"
        health = "warning"
        reasons.append("Plex upload is near your home upload ceiling.")

    if transcodes > 0 and plex_cpu_host > 25:
        bottleneck = "transcoding"
        health = "warning"
        reasons.append("Active transcodes are increasing Plex CPU load.")

    if iowait > IOWAIT_WARN_PERCENT:
        bottleneck = "disk_io"
        health = "warning"
        reasons.append("Disk wait is elevated, suggesting storage bottleneck.")

    if host_ram > RAM_WARN_PERCENT:
        bottleneck = "memory_pressure"
        health = "warning"
        reasons.append("Host RAM usage is high.")

    if total_upload - plex_upload > NON_PLEX_UPLOAD_WARN_MBPS:
        reasons.append("Non-Plex upload traffic appears to be present.")

    if not reasons:
        reasons.append("CPU, RAM, upload, and disk wait all look healthy.")

    return {
        "health": health,
        "bottleneck": bottleneck,
        "reasoning": reasons,
    }


def diagnose_buffering(state: dict) -> list[str]:
    reasons = []

    system = state["system"]
    facts = state.get("facts", {})
    sessions = state["plex"]["sessions"]

    if facts.get("buffering_detected"):
        reasons.append(
            f'Buffering is currently confirmed for: {", ".join(facts.get("buffering_sessions", []))}.'
        )
    else:
        reasons.append("Buffering is not currently confirmed by telemetry.")

    if not sessions:
        return reasons + ["No active Plex sessions were detected."]

    if state["plex"]["transcodes"] > 0:
        reasons.append("One or more active sessions are transcoding, which increases buffering risk.")

    if system["host_cpu_percent"] > CPU_WARN_PERCENT:
        reasons.append("Host CPU is elevated, so server processing could be contributing.")

    if system["host_ram_percent"] > RAM_WARN_PERCENT:
        reasons.append("Host RAM usage is high, which could contribute to instability or contention.")

    if system["iowait_percent"] > IOWAIT_WARN_PERCENT:
        reasons.append("Disk wait is elevated, suggesting storage I/O may be slowing playback.")

    if (
        facts.get("sustained_upload_high")
        and facts.get("upload_mostly_plex")
        and facts.get("upload_is_stable")
        and facts.get("remaining_upload_headroom_mbps", 0) < 2
        and facts.get("recent_upload_avg_mbps", 0) > UPLOAD_WARN_MBPS
    ):
        reasons.append(
            f'Upload has been consistently near capacity over the last minute '
            f'(avg {facts.get("recent_upload_avg_mbps", 0)} Mbps, max {facts.get("recent_upload_max_mbps", 0)} Mbps), '
            'which strongly suggests upload saturation is a real bottleneck.'
        )
    elif facts.get("sustained_upload_high") and facts.get("non_plex_upload_present"):
        reasons.append(
            f'Upload is high (avg {facts.get("recent_upload_avg_mbps", 0)} Mbps), '
            f'but a significant portion (~{round(facts.get("non_plex_upload_mbps", 0), 2)} Mbps) '
            'is not from Plex. This suggests other network activity is contributing to the load.'
        )
    elif facts.get("upload_is_bursty"):
        reasons.append(
            f'Upload is bursty (std dev {facts.get("upload_std_dev", 0)} Mbps), '
            'which is typical for Plex streaming and not inherently a bottleneck.'
        )
    elif facts.get("brief_upload_spike"):
        reasons.append(
            f'Upload showed a brief spike (max {facts.get("recent_upload_max_mbps", 0)} Mbps), '
            'but it was not sustained and does not indicate a bottleneck.'
        )

    for s in sessions:
        title = s.get("title", "Unknown")
        decision = (s.get("decision") or "").lower()
        subtitle_codec = (s.get("subtitle_codec") or "").lower()
        player = (s.get("player_product") or "").lower()
        bitrate = s.get("bitrate_kbps", 0)
        bitrate_mbps = round((bitrate or 0) / 1000, 2)

        if subtitle_codec in {"pgs", "vobsub"}:
            reasons.append(f'{title} is using image subtitles ({subtitle_codec}), which often force transcoding on many clients.')

        if bitrate > HIGH_BITRATE_KBPS:
            reasons.append(f'{title} has a high bitrate (~{bitrate_mbps} Mbps), which requires strong network conditions.')

        if "roku" in player:
            reasons.append(f'{title} is playing on Roku, which is more likely to hit compatibility-related playback issues.')

        if decision == "directplay" and subtitle_codec == "srt":
            reasons.append(f'{title} is direct playing with SRT subtitles, which is an optimal playback path.')

    return reasons


def build_structured_diagnosis(state: dict) -> dict:
    facts = state.get("facts", {})
    system = state.get("system", {})
    sessions = facts.get("session_facts", [])

    ruled_out = []
    risk_factors = []
    confidence = "low"
    most_likely_cause = "unknown"

    buffering_confirmed = facts.get("buffering_detected", False)
    buffering_signal_detected = facts.get("buffering_signal_detected", False)
    transcodes_active = state["plex"]["transcodes"] > 0
    system_healthy = _system_is_healthy(system)
    mixed_session_health = facts.get("has_mixed_session_health", False)
    single_session_buffering_while_others_healthy = facts.get(
        "single_session_buffering_while_others_healthy", False
    )
    strong_delivery_mismatch = (
        facts.get("buffering_session_count", 0) > 0
        and facts.get("buffering_sessions_delivery_issue_count", 0) == facts.get("buffering_session_count", 0)
    )
    compatibility_evidence = (
        facts.get("buffering_sessions_have_compatibility_pattern")
        and facts.get("buffering_sessions_have_file_trait_risk")
        and facts.get("buffering_sessions_have_client_trait_risk")
    )
    same_content_playing_elsewhere_successfully = facts.get(
        "same_content_playing_elsewhere_successfully", False
    )
    path_sensitivity_evidence = (
        (
            facts.get("buffering_sessions_have_path_sensitivity")
            or facts.get("upload_is_bursty")
            or facts.get("buffering_sessions_have_delivery_issue")
        )
        and facts.get("buffering_sessions_have_client_trait_risk")
        and (
            mixed_session_health
            or single_session_buffering_while_others_healthy
            or facts.get("session_specific_issue_likely")
        )
    )
    strict_upload_saturation = _strict_upload_saturation_evidence(
        facts,
        system_healthy=system_healthy,
        transcodes_active=transcodes_active,
    )
    upload_contradiction_guard = (
        single_session_buffering_while_others_healthy
        and not transcodes_active
        and system_healthy
        and facts.get("upload_is_bursty")
    )

    if not transcodes_active:
        ruled_out.append("transcoding")

    if system.get("host_cpu_percent", 0) < CPU_WARN_PERCENT:
        ruled_out.append("cpu")

    if system.get("host_ram_percent", 0) < RAM_WARN_PERCENT:
        ruled_out.append("ram")

    if system.get("iowait_percent", 0) < IOWAIT_WARN_PERCENT:
        ruled_out.append("disk_io")

    if (
        facts.get("remaining_upload_headroom_mbps", 0) >= 5
        and not facts.get("sustained_upload_high")
    ):
        ruled_out.append("upload_saturation")

    if facts.get("upload_is_bursty"):
        ruled_out.append("stable_upload_saturation")

    if upload_contradiction_guard:
        ruled_out.append("upload_saturation")

    if buffering_confirmed:
        risk_factors.append("buffering_confirmed")

    if facts.get("sustained_upload_high"):
        risk_factors.append("sustained_upload_high")

    if facts.get("upload_is_bursty"):
        risk_factors.append("bursty_upload_pattern")

    if facts.get("upload_mostly_plex"):
        risk_factors.append("upload_mostly_plex")

    if facts.get("non_plex_upload_present"):
        risk_factors.append("non_plex_upload_present")

    if facts.get("compatibility_pattern_detected"):
        risk_factors.append("compatibility_pattern_detected")

    if facts.get("path_sensitivity_detected"):
        risk_factors.append("path_sensitivity_detected")

    if mixed_session_health:
        risk_factors.append("mixed_session_health")

    if single_session_buffering_while_others_healthy:
        risk_factors.append("single_session_buffering_while_others_healthy")

    if same_content_playing_elsewhere_successfully:
        risk_factors.append("same_content_playing_elsewhere_successfully")

    for sf in sessions:
        if sf.get("is_image_subtitle"):
            risk_factors.append("image_subtitles")
        if sf.get("client_is_roku"):
            risk_factors.append("roku_client")
        if sf.get("client_is_browser"):
            risk_factors.append("browser_client")
        if sf.get("delivery_below_expected"):
            risk_factors.append("delivery_throughput_issue")
        if sf.get("is_buffering"):
            risk_factors.append("session_buffering")
        if sf.get("looks_healthy_now"):
            risk_factors.append("healthy_direct_play_session")
        if sf.get("client_trait_risk"):
            risk_factors.append("sensitive_client_type")

    if not buffering_confirmed:
        most_likely_cause = "none_detected"
        confidence = "low"

    if transcodes_active:
        most_likely_cause = "transcoding"
        confidence = "high"

    elif strict_upload_saturation:
        most_likely_cause = "upload_saturation"
        confidence = "high"

    elif (
        buffering_confirmed
        and not transcodes_active
        and system_healthy
        and mixed_session_health
        and path_sensitivity_evidence
        and not strict_upload_saturation
    ):
        most_likely_cause = "client_network_path_sensitivity"
        confidence = "medium"

    elif (
        buffering_confirmed
        and not transcodes_active
        and system_healthy
        and compatibility_evidence
        and not same_content_playing_elsewhere_successfully
        and not strict_upload_saturation
    ):
        most_likely_cause = "client_file_compatibility_issue"
        confidence = "medium"

    elif (
        buffering_confirmed
        and not transcodes_active
        and system_healthy
        and path_sensitivity_evidence
        and not strict_upload_saturation
    ):
        most_likely_cause = "client_network_path_sensitivity"
        confidence = "medium"

    elif (
        buffering_confirmed
        and strong_delivery_mismatch
        and not transcodes_active
        and not mixed_session_health
    ):
        most_likely_cause = "network_throughput_issue"
        confidence = "high"

    elif (
        buffering_confirmed
        and not transcodes_active
        and system_healthy
    ):
        most_likely_cause = "client_or_network"
        confidence = "medium"

    return {
        "buffering_confirmed": buffering_confirmed,
        "buffering_signal_detected": buffering_signal_detected,
        "buffering_sessions": facts.get("buffering_sessions", []),
        "buffering_signal_sessions": facts.get("buffering_signal_sessions", []),
        "buffering_risk_detected": facts.get("buffering_risk_detected", False),
        "buffering_risk_sessions": facts.get("buffering_risk_sessions", []),
        "most_likely_cause": most_likely_cause,
        "confidence": confidence,
        "system_wide_issue_likely": facts.get("system_wide_issue_likely", False),
        "session_specific_issue_likely": facts.get("session_specific_issue_likely", False),
        "ruled_out": sorted(set(ruled_out)),
        "risk_factors": sorted(set(risk_factors)),
    }


def classify_issue_metadata(state: dict, structured_diagnosis: dict) -> dict:
    diagnosis = structured_diagnosis.get("most_likely_cause", "unknown")
    confidence = structured_diagnosis.get("confidence", "low")
    facts = state.get("facts", {})
    plex = state.get("plex", {})
    system = state.get("system", {})
    session_facts = facts.get("session_facts", [])
    buffering_confirmed = structured_diagnosis.get("buffering_confirmed", False)
    buffering_session_count = facts.get("buffering_session_count", 0)
    active_sessions = plex.get("active_sessions", 0)
    transcode_profile = _transcode_impact_profile(session_facts)
    pressure = _system_pressure_profile(system, facts)
    shared_constraint = _shared_resource_constraint(system, facts)
    multi_session_impact = buffering_session_count > 1 or transcode_profile["transcode_session_count"] > 1
    affected_ratio = (
        max(buffering_session_count, transcode_profile["transcode_session_count"]) / active_sessions
        if active_sessions > 0
        else 0
    )

    scope = "unknown"
    severity = "info"
    diagnosis_family = "unknown"

    if diagnosis == "none_detected":
        scope = "unknown"
        severity = "info"
        diagnosis_family = "healthy_or_risk_only"
    elif diagnosis in {"client_network_path_sensitivity", "client_file_compatibility_issue"}:
        scope = "client_specific"
        # Client compatibility/path issues stay calm unless pain is confirmed.
        severity = "warning" if buffering_confirmed else "info"
        diagnosis_family = "client_specific"
    elif diagnosis == "network_throughput_issue":
        scope = "system_wide" if facts.get("buffering_session_count", 0) > 1 or shared_constraint else "session_specific"
        if buffering_session_count > 1 or pressure["severe_pressure"]:
            severity = "critical"
        elif buffering_confirmed:
            severity = "warning"
        else:
            severity = "info"
        diagnosis_family = "delivery"
    elif diagnosis == "upload_saturation":
        scope = "system_wide"
        severity = "critical"
        diagnosis_family = "capacity"
    elif diagnosis == "transcoding":
        # Prefer session-specific unless there is explicit evidence that the
        # issue is broadening beyond one playback path.
        scope = (
            "system_wide"
            if buffering_session_count > 1
            or shared_constraint
            or (
                transcode_profile["transcode_session_count"] > 1
                and (buffering_confirmed or pressure["mild_pressure"])
            )
            else "session_specific"
        )

        # Only treat transcoding as severe when it is materially harming playback
        # or pushing the host/service under real load.
        if multi_session_impact and (buffering_confirmed or pressure["severe_pressure"]):
            severity = "critical"
        elif transcode_profile["video_transcode_count"] > 0:
            # A single stable video transcode is meaningful but not automatically
            # broad or severe on a healthy NAS.
            severity = "warning"
            if buffering_confirmed or pressure["severe_pressure"]:
                severity = "critical"
            elif transcode_profile["transcode_session_count"] == 1 and not pressure["mild_pressure"]:
                severity = "warning"
        elif transcode_profile["subtitle_transcode_count"] > 0:
            # Subtitle/container compatibility can be normal low-cost adaptation
            # when playback remains stable.
            severity = "warning" if buffering_confirmed else "info"
        elif transcode_profile["audio_only_transcode_count"] > 0:
            # Audio-only compatibility transcodes are usually expected and low-impact
            # unless they coincide with confirmed playback pain or host pressure.
            severity = "warning" if buffering_confirmed or pressure["mild_pressure"] else "info"
        else:
            severity = "warning" if buffering_confirmed else "info"
        diagnosis_family = "processing"
    elif diagnosis == "client_or_network":
        scope = "session_specific" if facts.get("session_specific_issue_likely") else "unknown"
        severity = "warning" if structured_diagnosis.get("buffering_confirmed") else "info"
        diagnosis_family = "client_or_network"

    if (
        structured_diagnosis.get("system_wide_issue_likely")
        and diagnosis != "none_detected"
        and (buffering_session_count > 1 or shared_constraint or pressure["severe_pressure"])
    ):
        scope = "service_wide" if severity == "critical" else "system_wide"

    # If nothing is actually buffering and the service is otherwise healthy,
    # downgrade to the calmest tier to avoid alarmist compatibility-only states.
    if facts.get("buffering_session_count", 0) == 0 and not structured_diagnosis.get("buffering_confirmed"):
        severity = "info"

    scope = _finalize_scope(
        diagnosis,
        scope,
        severity,
        facts,
        transcode_profile,
        shared_constraint,
    )

    impact = _impact_assessment(
        state,
        structured_diagnosis,
        scope,
        transcode_profile,
        pressure,
        affected_ratio,
    )

    return {
        "scope": scope,
        "severity": severity,
        "confidence": confidence,
        "diagnosis_family": diagnosis_family,
        **impact,
    }
