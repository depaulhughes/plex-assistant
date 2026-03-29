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
    }

    return json.dumps(context, indent=2)


def answer_with_llm(question: str, state: dict, rule_based_answer: str) -> str:
    if client is None:
        return "OPENAI_API_KEY is missing from your .env file."

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
        "If no issue is confirmed, explicitly state that the system is healthy. "
        "Be concise but specific."
    )

    prompt = f"""
User question:
{question}

Rule-based analysis:
{rule_based_answer}

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
- Distinguish between a weak buffering signal and confirmed buffering.
- Only attribute buffering to sessions listed in buffering_sessions.
- Do not describe sessions in buffering_signal_sessions as definitely buffering unless they are also in buffering_sessions.
- Use the requested section order when diagnosing issues: Status, Confirmed by telemetry, Ruled out, Most likely cause, Why this diagnosis fits, Best next checks.

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
