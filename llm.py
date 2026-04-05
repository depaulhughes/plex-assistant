import json
from typing import Any, Dict, List, Optional

from config import client


def classify_question_intent(question: str) -> str:
    q = question.lower().strip()

    if any(token in q for token in ["noise or real", "real issue or noise", "just noise", "false alarm", "actually concerning", "meaningful or mostly noise"]):
        return "noise_vs_real_issue"
    if any(token in q for token in ["do i need to act", "should i act", "act now", "need action", "should we escalate", "do we need to escalate"]):
        return "act_no_act_decision"
    if any(token in q for token in ["what would fail first", "fail first", "failure path", "break first", "next failure mode"]):
        return "failure_path"
    if any(token in q for token in ["localized or broad", "localized or broader", "client-specific or system-wide", "system wide or client", "scope", "broader issue"]):
        return "scope_assessment"
    if any(token in q for token in ["can i handle another stream", "another stream", "capacity", "headroom", "margin", "safe to add", "can handle more"]):
        return "capacity_check"
    if any(token in q for token in ["what should i do", "what do i do", "next step", "next check", "troubleshoot", "troubleshooting", "how should i investigate"]):
        return "troubleshooting_next_steps"
    if any(token in q for token in ["compare", "difference", "vs", "versus", "rather than", "or is it"]):
        return "comparison"
    if any(token in q for token in ["should i worry", "risk", "urgent", "severity", "impact", "escalat"]):
        return "risk_assessment"
    if any(token in q for token in ["what should i do", "next step", "next check", "fix", "action", "do next"]):
        return "action"
    if any(token in q for token in ["optimiz", "improve", "prevent", "avoid", "reduce buffering", "best way"]):
        return "optimization"
    if any(token in q for token in ["what's happening", "whats happening", "right now", "status", "current state"]):
        return "status_check"
    if any(token in q for token in ["why", "explain", "how come", "what does"]):
        return "root_cause"
    if any(token in q for token in ["diagnos", "cause", "client issue", "server issue", "upload", "bottleneck", "problem"]):
        return "root_cause"
    return "general"


def _concise_sessions(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    sessions = state.get("plex", {}).get("sessions", [])
    concise_sessions = []
    for session in sessions:
        concise_sessions.append(
            {
                "title": session.get("title"),
                "client": session.get("player_product") or session.get("tautulli_product"),
                "state": session.get("tautulli_state") or session.get("player_state"),
                "decision": session.get("decision"),
                "bitrate_kbps": session.get("bitrate_kbps"),
                "container": session.get("container"),
                "audio_codec": session.get("audio_codec"),
                "subtitle_codec": session.get("subtitle_codec"),
            }
        )
    return concise_sessions


def _compact_alerts(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    compact = []
    for alert in state.get("alerts", [])[:4]:
        compact.append(
            {
                "type": alert.get("alert_type"),
                "severity": alert.get("severity"),
                "title": alert.get("title"),
                "message": alert.get("message"),
            }
        )
    return compact


def _compact_recent_history(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    compact = []
    for event in state.get("recent_history_events", [])[-5:]:
        compact.append(
            {
                "timestamp": event.get("timestamp"),
                "diagnosis": event.get("diagnosis"),
                "severity": event.get("severity"),
                "scope": event.get("scope"),
                "state_change": (event.get("state_change") or {}).get("change_type"),
                "affected_sessions": event.get("affected_sessions", [])[:2],
                "affected_clients": event.get("affected_clients", [])[:2],
            }
        )
    return compact


def _history_recurrence_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    diagnosis_presentation = state.get("diagnosis_presentation", {})
    current_diagnosis = diagnosis_presentation.get("primary_diagnosis")
    if not current_diagnosis:
        return {"recurring": False}

    current_clients = set(
        item
        for item in state.get("facts", {}).get("affected_session_client_names", [])
        if item
    )
    if not current_clients:
        current_clients = set(
            session.get("client_name")
            for session in state.get("facts", {}).get("session_facts", [])
            if session.get("is_transcode") and session.get("client_name")
        )

    match_count = 0
    matched_clients = set()
    for event in state.get("recent_history_events", [])[-5:]:
        if event.get("diagnosis") != current_diagnosis:
            continue
        event_clients = set(item for item in event.get("affected_clients", []) if item)
        overlap = current_clients & event_clients
        if overlap:
            match_count += 1
            matched_clients.update(overlap)

    return {
        "recurring": match_count >= 2,
        "match_count": match_count,
        "matched_clients": sorted(matched_clients),
        "summary": (
            "Recent history shows the same diagnosis recurring on the same client."
            if match_count >= 2 and matched_clients
            else ""
        ),
    }


def _top_counts(values: List[str], limit: int = 3) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for value in values:
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return [{"label": label, "count": count} for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def _history_pattern_context(state: Dict[str, Any]) -> Dict[str, Any]:
    events = state.get("recent_history_events", [])[-20:]
    diagnoses = [str(event.get("diagnosis") or "") for event in events]
    severities = [str(event.get("severity") or "info") for event in events]
    scopes = [str(event.get("scope") or "unknown") for event in events]
    clients = [str(client) for event in events for client in (event.get("affected_clients") or []) if client]
    sessions = [str(session) for event in events for session in (event.get("affected_sessions") or []) if session]
    state_changes = [str((event.get("state_change") or {}).get("change_type") or "unknown") for event in events]
    localized_count = sum(1 for scope in scopes if scope in {"session_specific", "client_specific", "localized"})
    broad_count = sum(1 for scope in scopes if scope in {"system_wide", "multi_session", "broad"})
    diagnosis_streaks: List[Dict[str, Any]] = []
    previous = None
    streak = 0
    for diagnosis in diagnoses:
        if diagnosis and diagnosis == previous:
            streak += 1
        else:
            if previous and streak > 1:
                diagnosis_streaks.append({"diagnosis": previous, "count": streak})
            previous = diagnosis
            streak = 1
    if previous and streak > 1:
        diagnosis_streaks.append({"diagnosis": previous, "count": streak})

    return {
        "recent_event_count": len(events),
        "diagnosis_frequency": _top_counts(diagnoses, limit=5),
        "severity_distribution": _top_counts(severities, limit=5),
        "scope_distribution": _top_counts(scopes, limit=5),
        "most_affected_clients": _top_counts(clients, limit=5),
        "most_affected_sessions": _top_counts(sessions, limit=5),
        "localized_issue_count": localized_count,
        "broader_issue_count": broad_count,
        "state_change_counts": _top_counts(state_changes, limit=5),
        "repeated_diagnosis_streaks": diagnosis_streaks[:5],
    }


def _alerts_urgency_context(state: Dict[str, Any]) -> Dict[str, Any]:
    active_alerts = state.get("alerts", []) or []
    recent_alerts = state.get("recent_alert_history", [])[-20:]
    active_types = [str(alert.get("alert_type") or alert.get("title") or "") for alert in active_alerts]
    recent_types = [str(alert.get("alert_type") or alert.get("title") or "") for alert in recent_alerts]
    severities = [str(alert.get("severity") or "info") for alert in recent_alerts]
    clustered = any(count["count"] >= 2 for count in _top_counts(recent_types, limit=10))
    highest_recent_severity = "info"
    if any(value == "critical" for value in severities):
        highest_recent_severity = "critical"
    elif any(value == "warning" for value in severities):
        highest_recent_severity = "warning"
    buffering_related = any("buffer" in alert_type.lower() for alert_type in active_types + recent_types)
    upload_related = any(any(token in alert_type.lower() for token in ["upload", "wan", "bandwidth"]) for alert_type in active_types + recent_types)
    return {
        "active_alert_count": len(active_alerts),
        "active_alert_types": _top_counts(active_types, limit=5),
        "recent_alert_frequency": len(recent_alerts),
        "recent_alert_types": _top_counts(recent_types, limit=5),
        "highest_recent_severity": highest_recent_severity,
        "clustered_occurrences": clustered,
        "current_alerts_active": bool(active_alerts),
        "buffering_related": buffering_related or bool(state.get("structured_diagnosis", {}).get("buffering_confirmed")),
        "upload_pressure_related": upload_related or bool(state.get("facts", {}).get("sustained_upload_high")),
    }


def build_assistant_context(
    state: Dict[str, Any],
    response_mode: str = "operator",
    page_context: str = "home",
    intent: str = "status",
    context_mode: str = "full",
    conversation_history: Optional[List[Dict[str, str]]] = None,
    ask_source: str = "",
    ask_section: str = "",
    ask_prompt_key: str = "",
) -> str:
    diagnosis_presentation = state.get("diagnosis_presentation", {})
    structured_diagnosis = state.get("structured_diagnosis", {})
    issue_metadata = state.get("issue_metadata", {})
    action_plan = state.get("action_plan", {})
    manager_summary = state.get("manager_summary", {})
    history_summary = state.get("history_summary", {})
    state_change = state.get("state_change", {})
    facts = state.get("facts", {})
    playback_quality = manager_summary.get("playback_quality", {}) or {}

    context: Dict[str, Any] = {
        "response_mode": response_mode,
        "page_context": page_context,
        "intent": intent,
        "ask_context": {
            "ask_source": ask_source,
            "current_section": ask_section,
            "suggested_prompt_key": ask_prompt_key,
        },
        "system_health": {
            "service_health": manager_summary.get("service_health"),
            "severity": issue_metadata.get("severity") or diagnosis_presentation.get("severity"),
            "scope": issue_metadata.get("scope") or diagnosis_presentation.get("scope"),
            "confidence": issue_metadata.get("confidence") or diagnosis_presentation.get("confidence"),
            "escalation_needed": manager_summary.get("escalation_needed"),
            "state_change": state_change.get("change_type"),
        },
        "delivery_state": {
            "primary_diagnosis_label": manager_summary.get("current_diagnosis_label")
            or playback_quality.get("delivery_diagnosis_label")
            or diagnosis_presentation.get("primary_diagnosis_label"),
            "primary_diagnosis_code": diagnosis_presentation.get("primary_diagnosis"),
            "playback_quality_score": playback_quality.get("quality_score"),
            "playback_quality_label": playback_quality.get("quality_label"),
            "capacity_headroom": playback_quality.get("headroom_label") or issue_metadata.get("capacity_headroom"),
            "capacity_headroom_summary": playback_quality.get("headroom_summary") or issue_metadata.get("capacity_headroom_summary"),
            "delivery_confidence": playback_quality.get("delivery_confidence_label"),
            "delivery_confidence_summary": playback_quality.get("delivery_confidence_summary"),
            "recurrence_risk": playback_quality.get("recurrence_risk_label"),
            "recurrence_summary": playback_quality.get("recurrence_summary"),
            "recent_window_active": playback_quality.get("recent_window_active"),
            "recovered": playback_quality.get("recovered"),
            "memory_pattern": playback_quality.get("memory_pattern"),
            "memory_weight": playback_quality.get("memory_weight"),
            "burst_sensitive_upload": facts.get("upload_is_bursty"),
            "sustained_upload_high": facts.get("sustained_upload_high"),
            "score_driver_summary": playback_quality.get("score_driver_summary"),
            "score_driver_items": playback_quality.get("score_driver_items", []),
        },
        "active_issues": {
            "buffering_confirmed": structured_diagnosis.get("buffering_confirmed"),
            "buffering_sessions": structured_diagnosis.get("buffering_sessions", []),
            "buffering_risk_detected": structured_diagnosis.get("buffering_risk_detected"),
            "alerts": _compact_alerts(state),
        },
        "likely_cause": {
            "primary_diagnosis": diagnosis_presentation.get("primary_diagnosis"),
            "primary_diagnosis_label": manager_summary.get("current_diagnosis_label")
            or playback_quality.get("delivery_diagnosis_label")
            or diagnosis_presentation.get("primary_diagnosis_label"),
            "contributing_factors": diagnosis_presentation.get("operator_contributing_factors")
            or diagnosis_presentation.get("dashboard_contributing_factors")
            or [],
            "supporting_text": diagnosis_presentation.get("supporting_text"),
        },
        "ruled_out": structured_diagnosis.get("ruled_out", []),
        "current_sessions_summary": _concise_sessions(state),
        "recommended_action": {
            "primary_action": diagnosis_presentation.get("primary_action") or action_plan.get("primary_action"),
            "next_checks": action_plan.get("next_checks", [])[:3],
            "recommended_actions": action_plan.get("recommended_actions", [])[:3],
            "escalate_if": action_plan.get("escalate_if", [])[:3],
            "success_signals": action_plan.get("success_signals", [])[:3],
        },
        "trend_summary": {
            "summary": manager_summary.get("trend_summary"),
            "events_last_24h": history_summary.get("events_last_24h"),
            "warning_or_higher_last_24h": history_summary.get("warning_or_higher_last_24h"),
            "top_diagnosis_last_24h": history_summary.get("top_diagnosis_last_24h"),
            "top_affected_client_last_24h": history_summary.get("top_affected_client_last_24h"),
            "recent_events": _compact_recent_history(state),
            "recurrence": _history_recurrence_summary(state),
            "recent_playback_note": manager_summary.get("recent_playback_note"),
        },
        "history_pattern_summary": _history_pattern_context(state),
        "alerts_urgency_summary": _alerts_urgency_context(state),
        "operational_signals": {
            "active_sessions": state.get("plex", {}).get("active_sessions", 0),
            "transcodes": state.get("plex", {}).get("transcodes", 0),
            "direct_plays": state.get("plex", {}).get("direct_plays", 0),
            "plex_upload_mbps": state.get("system", {}).get("plex_upload_mbps"),
            "total_upload_mbps": state.get("system", {}).get("total_upload_mbps"),
            "host_cpu_percent": state.get("system", {}).get("host_cpu_percent"),
            "host_ram_percent": state.get("system", {}).get("host_ram_percent"),
            "iowait_percent": state.get("system", {}).get("iowait_percent"),
            "mixed_session_health": facts.get("has_mixed_session_health"),
            "same_content_healthy_elsewhere": facts.get("same_content_playing_elsewhere_successfully"),
            "upload_is_bursty": facts.get("upload_is_bursty"),
            "sustained_upload_high": facts.get("sustained_upload_high"),
        },
        "ruled_out_capacity_constraints": {
            "cpu_not_primary": (state.get("system", {}).get("host_cpu_percent", 0) or 0) < 60,
            "ram_not_primary": (state.get("system", {}).get("host_ram_percent", 0) or 0) < 85,
            "disk_not_primary": (state.get("system", {}).get("iowait_percent", 0) or 0) < 8,
        },
        "conversation_memory": conversation_history or [],
    }

    if context_mode == "full":
        context["detailed_facts"] = {
            "healthy_session_titles": facts.get("healthy_session_titles", []),
            "affected_session_client_names": facts.get("affected_session_client_names", []),
            "single_session_buffering_while_others_healthy": facts.get("single_session_buffering_while_others_healthy"),
        }

    return json.dumps(context, indent=2)


def build_follow_up_questions(state: Dict[str, Any], intent: str, response_mode: str = "operator", page_context: str = "home") -> List[str]:
    diagnosis_presentation = state.get("diagnosis_presentation", {})
    primary_diagnosis = diagnosis_presentation.get("primary_diagnosis")
    scope = diagnosis_presentation.get("scope")
    alerts = state.get("alerts", [])

    suggestions: List[str] = []

    if page_context == "history":
        if response_mode == "manager":
            suggestions.extend([
                "Is this history actually concerning?",
                "Does this look like a real recurring problem or just noise?",
                "What should I keep an eye on from recent history?",
            ])
        else:
            suggestions.extend([
                "What pattern do these recent diagnosis events show?",
                "Which client is most often affected?",
                "Are these mostly buffering or compatibility events?",
            ])
    elif page_context == "alerts":
        if response_mode == "manager":
            suggestions.extend([
                "Do I need to act on these alerts?",
                "How urgent is this alert history?",
                "Are these alerts meaningful or mostly noise?",
            ])
        else:
            suggestions.extend([
                "Why are these alerts firing?",
                "Are these alerts related to buffering or WAN pressure?",
                "Do the recent alerts form a pattern?",
            ])

    if intent in {"status", "status_check", "risk_assessment", "noise_vs_real_issue", "capacity_check"}:
        suggestions.extend(["Is this a client issue or a server issue?", "What should I do next?"])
    if intent in {"diagnosis", "explanation", "comparison", "root_cause", "failure_path", "scope_assessment"}:
        suggestions.extend(["What evidence supports that diagnosis?", "What causes have been ruled out?"])
    if intent in {"action", "optimization", "act_no_act_decision", "troubleshooting_next_steps"}:
        suggestions.extend(["What would confirm improvement?", "When should I escalate this?"])
    if primary_diagnosis == "client_network_path_sensitivity":
        suggestions.append("Why does this look client-specific instead of server-wide?")
    if primary_diagnosis == "upload_saturation":
        suggestions.append("Is upload saturation affecting multiple sessions right now?")
    if scope in {"client_specific", "session_specific"}:
        suggestions.append("Is the rest of the service healthy right now?")
    if alerts:
        suggestions.append("What do the active alerts mean right now?")
    if response_mode == "manager":
        suggestions.append("Do we need to escalate this operationally?")

    unique: List[str] = []
    for item in suggestions:
        if item not in unique:
            unique.append(item)
    return unique[:4]


def answer_with_llm(
    question: str,
    state: dict,
    rule_based_answer: str,
    response_mode: str = "operator",
    page_context: str = "home",
    context_mode: str = "full",
    intent: str = "status",
    conversation_history: Optional[List[Dict[str, str]]] = None,
    ask_source: str = "",
    ask_section: str = "",
    ask_prompt_key: str = "",
) -> str:
    if client is None:
        return "OPENAI_API_KEY is missing from your .env file."

    llm_context = build_assistant_context(
        state,
        response_mode=response_mode,
        page_context=page_context,
        intent=intent,
        context_mode=context_mode,
        conversation_history=conversation_history,
        ask_source=ask_source,
        ask_section=ask_section,
        ask_prompt_key=ask_prompt_key,
    )

    mode_instructions = {
        "operator": (
            "Respond like an operator assistant. Be technical, real-time, and root-cause oriented. "
            "Anchor the answer in the active sessions, telemetry evidence, ruled-out causes, severity, scope, confidence, and the most useful next checks."
        ),
        "manager": (
            "Respond like a manager-facing service advisor. Be concise, decision-focused, and impact-oriented. "
            "Prioritize service health, user impact, urgency, recommended action, and escalation. Avoid deep codec or per-stream detail unless it materially changes the decision."
        ),
    }
    page_instructions = {
        "home": "Answer like a dashboard copilot: balanced, readable, and focused on what is happening, why the score changed, whether buffering risk is present, and what the user should do next.",
        "operator": "Answer like an operator copilot: technical, evidence-dense, bottleneck-oriented, and explicit about what to verify next.",
        "manager": "Answer like a manager copilot: concise, decision-oriented, risk-aware, and focused on whether action is needed.",
        "history": "When on History, prioritize pattern interpretation over current-state boilerplate. Use structured historical summaries as source of truth and explain repetition, concentration by client or session, and whether the pattern looks worsening, stable, or isolated.",
        "alerts": "When on Alerts, prioritize urgency and signal triage over generic system summaries. Use structured alert summaries as source of truth and explain whether alerts are isolated, clustered, recurring, informational, or operationally meaningful.",
    }
    page_mode_instructions = {
        ("history", "operator"): "Default to technical pattern analysis: recurring diagnoses, affected clients, localized versus broad patterns, and whether the sequence suggests repeated instability or isolated incidents.",
        ("history", "manager"): "Default to decision framing: whether the recent history suggests a real ongoing concern, stable background noise, or a pattern worth monitoring operationally.",
        ("alerts", "operator"): "Default to signal triage and trigger interpretation: which alerts are firing, why they likely fired, and whether they correlate with buffering, WAN pressure, or client-specific issues.",
        ("alerts", "manager"): "Default to urgency and action framing: whether any action is needed, how urgent the alert stream is, and whether it suggests recurring operational risk or mostly background noise.",
    }
    section_instructions = {
        "primary_diagnosis": "Prioritize why the diagnosis fits this telemetry and why it is more appropriate than alternatives.",
        "playback_quality": "Prioritize the main score drivers, what pulled the score down or up, and what that means right now.",
        "capacity_headroom": "Prioritize WAN margin, what is currently consuming headroom, and what would improve or tighten it.",
        "recent_playback_note": "Prioritize what happened recently, what has recovered, and what risk remains.",
        "active_alerts": "Prioritize the current alert state, which alerts actually matter now, and whether action is needed.",
        "resource_pressure_analysis": "Prioritize which constraint matters most right now and why other resources are or are not contributing.",
        "failure_path_analysis": "Prioritize the most plausible next failure path if conditions worsen and why it would fail first.",
        "session_level_reasoning": "Prioritize why the current session pattern looks localized or broader and how the active sessions support that conclusion.",
        "manager_summary": "Prioritize the executive takeaway, practical delivery risk, and whether action is needed.",
        "recommendation_ladder": "Prioritize why the current recommendation level fits and what would move it up or down.",
        "escalation_triggers": "Prioritize the exact conditions that would justify escalation from the current state.",
        "history_pattern": "Prioritize the recent diagnosis pattern, recurrence, and what the timeline suggests operationally.",
        "current_alerts": "Prioritize whether any current alerts need action and what they imply right now.",
        "recent_alert_history": "Prioritize recent alert patterns, recurrence, and whether they suggest a real underlying issue.",
    }
    intent_instructions = {
        "status": "Lead with what is happening right now and how broad the impact is.",
        "status_check": "Lead with what is happening right now and how broad the impact is.",
        "diagnosis": "Focus on the most likely cause, the strongest supporting evidence, and the key ruled-out alternatives.",
        "root_cause": "Focus on the most likely cause, the strongest supporting evidence, and the key ruled-out alternatives.",
        "action": "Focus on the most useful next actions, checks, escalation criteria, and what would confirm improvement.",
        "troubleshooting_next_steps": "Focus on the most useful next actions, checks, escalation criteria, and what would confirm improvement.",
        "explanation": "Explain why the diagnosis fits in clear grounded terms without becoming speculative.",
        "comparison": "Compare the leading explanations directly and explain why one fits better than the other.",
        "risk_assessment": "Focus on urgency, user impact, whether this is localized or broad, and whether it needs escalation.",
        "optimization": "Focus on practical ways to improve resilience or reduce recurrence, grounded in the current system state.",
        "noise_vs_real_issue": "Decide whether the current pattern is meaningful or mostly noise, then justify that verdict with concrete evidence and a recommended posture.",
        "act_no_act_decision": "Give a clear recommendation about whether action is needed now, what supports that recommendation, and what would change it.",
        "failure_path": "Focus on the most likely next failure mode, why it would fail first, what is ruled out, and what to watch next.",
        "scope_assessment": "Focus on whether the issue looks localized or broad, who or what is most affected, and what would verify the scope.",
        "capacity_check": "Focus on current margin, what is consuming it, what would tighten it next, and whether adding more load looks safe.",
        "general": "Answer in the most natural structured shape for the question instead of forcing a generic status template.",
    }
    response_schema_instructions = {
        "status": "Use this response shape unless a more specific cue overrides it: Current State, Recent Behavior, Risk / What Could Happen Next, What to Do.",
        "status_check": "Use this response shape unless a more specific cue overrides it: Current State, Recent Behavior, Risk / What Could Happen Next, What to Do.",
        "noise_vs_real_issue": "Use this response shape: Verdict, Why, Evidence, Recommended Posture.",
        "act_no_act_decision": "Use this response shape: Recommendation, Why Now / Why Not, What Would Change The Recommendation, Escalation Trigger.",
        "failure_path": "Use this response shape: Most Likely Failure Mode, Why, What Is Ruled Out, What To Watch Next.",
        "scope_assessment": "Use this response shape: Scope, Most Likely Affected Client Or Group, Why It Looks Localized Or Broad, Verification Step.",
        "capacity_check": "Use this response shape: Current Margin, What Is Consuming It, What Would Tighten It Next, Is Another Stream Safe?.",
        "troubleshooting_next_steps": "Use this response shape: Best Next Step, Why This Check Matters, What To Inspect, What Outcome Would Change The Conclusion.",
        "root_cause": "Use this response shape: Most Likely Cause, Why It Fits, What Is Ruled Out, Best Next Check.",
        "risk_assessment": "Use this response shape: Risk Level, Why It Matters, What Is Containing It, What Would Escalate It.",
        "general": "Choose the smallest useful structured shape for the actual question. Do not force the standard four-part status layout if another schema is more natural.",
    }

    instructions = (
        "You are a Plex observability assistant. "
        "You must treat the structured computed playback and delivery fields as the source of truth. "
        "Always consider primary diagnosis, playback quality, capacity headroom, delivery confidence, recurrence risk, recent instability or recovery memory, and WAN burst sensitivity or near-cap upload. "
        "Do not rely only on whether buffering is active right now. "
        "Your answers must separate: 1. current state 2. recent behavior 3. forward risk. "
        "If playback is stable now but recent instability, tight headroom, or recurrence risk is still present, do not call the system healthy or say there is no issue. "
        "Use recovery-aware wording such as stable but fragile, recovered/monitor, burst-sensitive, near-cap WAN margin, recurrence risk, and guarded or limited headroom when the structured fields support it. "
        "You must use the provided telemetry, facts, and structured diagnosis as the source of truth. "
        "Structured diagnosis ALWAYS overrides rule-based analysis if there is any conflict. "
        "Do not guess or invent issues. "
        "Only say buffering is currently happening if buffering_confirmed is true. "
        "If buffering_confirmed is false but buffering_risk_detected is true, describe it as a possible or weak buffering-related pattern, not confirmed buffering. "
        "Never contradict structured diagnosis. "
        "Use rule-based analysis only as supporting evidence, not as the final conclusion. "
        "Bursty upload can be the primary explanation for intermittent or recurring playback instability even when average load looks serviceable. "
        "Treat burst-sensitive WAN delivery, guarded headroom, or elevated recurrence risk as meaningful current conditions, not minor footnotes. "
        "If one session is buffering and another is healthy, do not casually describe the issue as a global server bottleneck. "
        "If upload_saturation is not the structured diagnosis, do not describe upload as the bottleneck. "
        "If the same media is playing successfully on another client, do not describe the issue as a file compatibility issue. Prefer a client or network-path explanation. "
        "If the most_likely_cause is client_file_compatibility_issue, explain that this is a pattern-based diagnosis, "
        "not absolute proof, and only mention contributing factors such as Mac client, MP4 container, MOV_TEXT subtitles, or AC3 audio if they are actually present in the affected session facts. "
        "If the most_likely_cause is client_network_path_sensitivity, explain that this suggests the client and remote delivery path "
        "appear less tolerant of bursty throughput or long-distance network variability, even though the server itself looks healthy and another client may be playing fine. "
        "If only one session is in buffering_sessions, explicitly say that other active sessions are playing normally when supported by telemetry. "
        "Never describe bursty upload alone as proof of saturation. "
        "When analyzing problems, clearly separate: "
        "1. what is confirmed by telemetry, "
        "2. what is ruled out, and "
        "3. what is most likely (based on structured diagnosis). "
        "When diagnosing issues, prefer this section order: Status, Confirmed by telemetry, Ruled out, Most likely cause, Why this diagnosis fits, Best next checks. "
        "Use issue_metadata as the source of truth for severity, scope, and confidence. "
        "Use diagnosis_presentation as the source of truth for primary diagnosis, contributing factors, and primary action presentation. "
        "Use delivery_state and manager_summary as the source of truth for playback quality, headroom, delivery confidence, recurrence risk, and recovery-aware diagnosis wording. "
        "Use action_plan for recommended operator actions, escalation criteria, and success signals. "
        "Use manager_summary when the user asks for a high-level service view or operational summary. "
        "When on History, answer from structured historical pattern summaries instead of falling back to generic present-state boilerplate. "
        "When on Alerts, answer from structured alert urgency summaries instead of only describing whether alerts are active right now. "
        "If recent history shows the same diagnosis on the same client repeatedly, you may mention that recurrence explicitly, but only when the provided recent history supports it. "
        "When mentioning recurrence, frame it as a localized recurring pattern rather than a broader service issue unless telemetry shows broader scope. "
        "If the user asks from an operator perspective, focus on diagnosis, scope, severity, actions, and escalation criteria. "
        "If the user asks from a manager perspective, focus on service health, user impact, urgency, and the top recommended action. "
        "Treat page_context as a strong presentation cue. Home should be balanced, Operator should be technical, and Manager should be decision-focused. "
        "If response_mode is operator, use operator-style detail by default. "
        "If response_mode is manager, use a concise manager-style summary by default. "
        "Operator and manager answers must feel meaningfully different in style and emphasis. "
        "Operator answers should prioritize mechanics, root cause, subsystem pressure, telemetry evidence, and what to inspect next. "
        "Manager answers should prioritize user impact, urgency, operational significance, action recommendation, and whether to escalate or monitor. "
        "Keep the same primary diagnosis, severity, scope, and confidence across response modes. Only presentation should differ. "
        "You may mention a small number of contributing factors when they are present, but do not replace the primary diagnosis with them. "
        "Use conversation_memory only to maintain short conversational continuity. Do not let prior turns override the current telemetry snapshot. "
        "The Ask Plex assistant is embedded inside a Plex observability dashboard. It is not a general assistant. It must act like a page-aware observability copilot. "
        "If ask_context.current_section is present, treat that card or section as a first-class context signal and answer it directly before broadening to the overall page. "
        "Do not let different card actions collapse into the same generic page summary. "
        "When the user asks what to do, give concrete next checks or actions grounded in telemetry instead of generic filler. "
        "Keep answers structured but not rigid. Not every answer needs the same four sections. "
        "Choose the response shape that best fits the detected intent and the current page lens. "
        "Be concise but specific."
    )

    prompt = f"""
User question:
{question}

Rule-based analysis:
{rule_based_answer}

Response mode:
{response_mode}

Page context:
{page_context}

Intent:
{intent}

Context mode:
{context_mode}

Ask source:
{ask_source or "general"}

Ask section:
{ask_section or "general"}

Ask prompt key:
{ask_prompt_key or "none"}

IMPORTANT:
- Use the telemetry, facts, and structured diagnosis as the source of truth.
- Treat delivery_state as the best high-level summary of playback quality, headroom, delivery confidence, recurrence risk, and recovery status.
- On History, prioritize history_pattern_summary and trend_summary.
- On Alerts, prioritize alerts_urgency_summary and active_issues.alerts.
- Use page_context to tune the framing and level of detail.
- If ask_context.current_section is present, answer that section's question first and keep the answer anchored to that card.
- If ask_context.suggested_prompt_key is present, treat it as a strong cue for what kind of explanation the user wants.
- If buffering_confirmed is false, do not state that buffering is currently happening.
- If buffering_confirmed is true, say that clearly.
- Do not mention any stream unless it exists in current_sessions_summary.
- Explicitly mention ruled-out causes when helpful.
- If upload is bursty and delivery_state indicates guarded or high recurrence risk, explain that burst-sensitive WAN behavior is a meaningful current risk even if active buffering is not happening at this exact moment.
- If one session is buffering and another is healthy, frame the issue as session-specific unless the structured diagnosis clearly indicates a system-wide bottleneck.
- If the same media is playing successfully on another client, do not frame the issue as a true file compatibility problem.
- Prefer likely_cause.primary_diagnosis for the final answer.
- Use the structured context fields directly instead of inventing your own taxonomy.
- Distinguish between a weak buffering signal and confirmed buffering.
- Use issue_metadata, diagnosis_presentation, action_plan, history_summary, alerts, and manager_summary when relevant.
- If response_mode is manager, keep the answer concise and service-health oriented.
- If response_mode is manager, mention contributing factors only briefly and only when they add clarity.
- If response_mode is operator, include useful technical detail, severity, scope, confidence, and next checks.
- If response_mode is operator, you may include up to 3 contributing factors when clearly supported.
- For recovered-but-fragile or burst-sensitive states, do not say “healthy,” “no issue,” or “operating normally” if delivery_state shows reduced confidence, guarded headroom, or non-low recurrence risk.
- Use plain section titles only. Do not prefix section titles with markdown heading syntax like #, ##, or ###.
- Choose the response structure that best fits the detected intent instead of forcing the same section pattern every time.
- Let Operator and Manager answers differ clearly in framing even when they share the same underlying facts.
- In the What to do section, prefer concrete next checks and actions grounded in the telemetry.
- If context_mode is web_ask, answer from the compact current-state summary without assuming missing details beyond what is present.
- {mode_instructions.get(response_mode, mode_instructions["operator"])}
- {page_instructions.get(page_context, page_instructions["home"])}
- {page_mode_instructions.get((page_context, response_mode), "Keep the page purpose and selected lens central to the answer.")}
- {intent_instructions.get(intent, intent_instructions["status"])}
- {response_schema_instructions.get(intent, response_schema_instructions["general"])}
- {section_instructions.get(ask_section, "Keep the answer anchored to the requested section and the current telemetry.")}

Live system context:
{llm_context}
"""

    try:
        response = client.responses.create(
            model="gpt-4o-mini",
            instructions=instructions,
            input=prompt,
            store=False,
        )
        return response.output_text
    except Exception as e:
        return (
            f"LLM mode failed: {e}\n\n"
            f"Falling back to rule-based answer:\n\n"
            f"{rule_based_answer}"
        )
