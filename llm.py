import json

from config import client


def build_llm_context(state: dict) -> str:
    context = {
        "system": state["system"],
        "plex_summary": {
            "active_sessions": state["plex"]["active_sessions"],
            "transcodes": state["plex"]["transcodes"],
            "direct_plays": state["plex"]["direct_plays"],
        },
        "sessions": state["plex"]["sessions"],
        "tautulli_activity": state["plex"]["tautulli_activity"],
        "facts": state.get("facts", {}),
        "diagnosis": state["diagnosis"],
        "structured_diagnosis": state.get("structured_diagnosis", {}),
        "issue_metadata": state.get("issue_metadata", {}),
        "action_plan": state.get("action_plan", {}),
        "diagnosis_presentation": state.get("diagnosis_presentation", {}),
        "history_summary": state.get("history_summary", {}),
        "state_change": state.get("state_change", {}),
        "alerts": state.get("alerts", []),
        "manager_summary": state.get("manager_summary", {}),
    }

    return json.dumps(context, indent=2)


def build_web_ask_context(state: dict, response_mode: str = "operator") -> str:
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

    facts = state.get("facts", {})
    concise_facts = {
        "buffering_session_count": facts.get("buffering_session_count"),
        "healthy_playing_session_count": facts.get("healthy_playing_session_count"),
        "has_mixed_session_health": facts.get("has_mixed_session_health"),
        "single_session_buffering_while_others_healthy": facts.get("single_session_buffering_while_others_healthy"),
        "sustained_upload_high": facts.get("sustained_upload_high"),
        "upload_is_bursty": facts.get("upload_is_bursty"),
        "upload_is_stable": facts.get("upload_is_stable"),
        "same_content_playing_elsewhere_successfully": facts.get("same_content_playing_elsewhere_successfully"),
        "affected_session_client_names": facts.get("affected_session_client_names", []),
        "healthy_session_titles": facts.get("healthy_session_titles", []),
    }

    context = {
        "response_mode": response_mode,
        "system": state.get("system", {}),
        "plex_summary": {
            "active_sessions": state.get("plex", {}).get("active_sessions", 0),
            "transcodes": state.get("plex", {}).get("transcodes", 0),
            "direct_plays": state.get("plex", {}).get("direct_plays", 0),
        },
        "sessions": concise_sessions,
        "facts": concise_facts,
        "structured_diagnosis": state.get("structured_diagnosis", {}),
        "diagnosis_presentation": state.get("diagnosis_presentation", {}),
        "issue_metadata": state.get("issue_metadata", {}),
        "action_plan": state.get("action_plan", {}),
        "history_summary": state.get("history_summary", {}),
        "alerts": state.get("alerts", []),
        "manager_summary": state.get("manager_summary", {}),
    }

    return json.dumps(context, indent=2)


def answer_with_llm(
    question: str,
    state: dict,
    rule_based_answer: str,
    response_mode: str = "operator",
    context_mode: str = "full",
) -> str:
    if client is None:
        return "OPENAI_API_KEY is missing from your .env file."

    if context_mode == "web_ask":
        llm_context = build_web_ask_context(state, response_mode=response_mode)
    else:
        llm_context = build_llm_context(state)

    instructions = (
        "You are a Plex observability assistant. "
        "You must use the provided telemetry, facts, and structured diagnosis as the source of truth. "
        "Structured diagnosis ALWAYS overrides rule-based analysis if there is any conflict. "
        "Do not guess or invent issues. "
        "Only say buffering is currently happening if buffering_confirmed is true. "
        "If buffering_confirmed is false but buffering_risk_detected is true, describe it as a possible or weak buffering-related pattern, not confirmed buffering. "
        "Never contradict structured diagnosis. "
        "Use rule-based analysis only as supporting evidence, not as the final conclusion. "
        "Do not treat bursty upload as a bottleneck. "
        "Only consider upload a bottleneck if it is sustained near capacity, primarily driven by Plex traffic, "
        "remaining upload headroom is extremely low, and the pattern is stable rather than bursty. "
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
        "Use action_plan for recommended operator actions, escalation criteria, and success signals. "
        "Use manager_summary when the user asks for a high-level service view or operational summary. "
        "If the user asks from an operator perspective, focus on diagnosis, scope, severity, actions, and escalation criteria. "
        "If the user asks from a manager perspective, focus on service health, user impact, urgency, and the top recommended action. "
        "If response_mode is operator, use operator-style detail by default. "
        "If response_mode is manager, use a concise manager-style summary by default. "
        "Keep the same primary diagnosis, severity, scope, and confidence across response modes. Only presentation should differ. "
        "You may mention a small number of contributing factors when they are present, but do not replace the primary diagnosis with them. "
        "If no issue is confirmed, explicitly state that the system is healthy. "
        "Be concise but specific."
    )

    prompt = f"""
User question:
{question}

Rule-based analysis:
{rule_based_answer}

Response mode:
{response_mode}

Context mode:
{context_mode}

IMPORTANT:
- Use the telemetry, facts, and structured diagnosis as the source of truth.
- If buffering_confirmed is false, do not state that buffering is currently happening.
- If buffering_confirmed is true, say that clearly.
- Do not mention any stream unless it exists in sessions[].
- Explicitly mention ruled-out causes when helpful.
- If upload is bursty but not sustained, do not describe it as a bottleneck.
- If one session is buffering and another is healthy, frame the issue as session-specific unless the structured diagnosis clearly indicates a system-wide bottleneck.
- If the same media is playing successfully on another client, do not frame the issue as a true file compatibility problem.
- Prefer structured_diagnosis.most_likely_cause for the final answer.
- Use diagnosis_presentation.primary_diagnosis as the presentation anchor, consistent across modes.
- Distinguish between a weak buffering signal and confirmed buffering.
- Only attribute buffering to sessions listed in buffering_sessions.
- Do not describe sessions in buffering_signal_sessions as definitely buffering unless they are also in buffering_sessions.
- Use the requested section order when diagnosing issues: Status, Confirmed by telemetry, Ruled out, Most likely cause, Why this diagnosis fits, Best next checks.
- Use issue_metadata, diagnosis_presentation, action_plan, history_summary, alerts, and manager_summary when they are relevant to the user’s question.
- If response_mode is manager, keep the answer concise and service-health oriented.
- If response_mode is manager, mention contributing factors only briefly and only when they add clarity.
- If response_mode is operator, include useful technical detail, severity, scope, confidence, and next checks.
- If response_mode is operator, you may include up to 3 contributing factors when clearly supported.
- For risk-only or no-confirmed-issue states, use calm monitoring-oriented wording and do not present the situation as an active incident.
- If context_mode is web_ask, answer from the compact current-state summary without assuming missing details beyond what is present.

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
