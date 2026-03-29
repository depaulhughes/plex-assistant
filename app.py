import json
import sys

from clients import (
    build_tautulli_session_map,
    get_plex_sessions,
    get_tautulli_activity,
    prom_query_scalar,
)
from diagnosis import build_structured_diagnosis, diagnose, diagnose_buffering
from facts import derive_facts, get_recent_upload_analysis
from llm import answer_with_llm


def build_state() -> dict:
    sessions = get_plex_sessions()
    tautulli_activity = get_tautulli_activity()
    tautulli_session_map = build_tautulli_session_map(tautulli_activity)
    recent_upload = get_recent_upload_analysis(window_seconds=60)

    system = {
        "host_cpu_percent": round(
            prom_query_scalar('100 * (1 - (sum(rate(node_cpu_seconds_total{mode="idle"}[1m])) / sum(rate(node_cpu_seconds_total[1m]))))'),
            2,
        ),
        "host_ram_percent": round(
            prom_query_scalar('100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))'),
            2,
        ),
        "plex_cpu_host_percent": round(
            prom_query_scalar('(sum(rate(container_cpu_usage_seconds_total{name="plex-gpu-and-music-folder"}[1m])) / scalar(max(machine_cpu_cores))) * 100'),
            2,
        ),
        "plex_ram_gib": round(
            prom_query_scalar('container_memory_working_set_bytes{name="plex-gpu-and-music-folder"} / 1024 / 1024 / 1024'),
            2,
        ),
        "plex_upload_mbps": round(
            prom_query_scalar('sum(rate(container_network_transmit_bytes_total{name="plex-gpu-and-music-folder"}[1m])) * 8 / 1000000'),
            2,
        ),
        "total_upload_mbps": round(
            prom_query_scalar('sum(rate(node_network_transmit_bytes_total{device!~"lo"}[1m])) * 8 / 1000000'),
            2,
        ),
        "iowait_percent": round(
            prom_query_scalar('sum(rate(node_cpu_seconds_total{mode="iowait"}[1m])) * 100 / scalar(count(count(node_cpu_seconds_total{mode="idle"}) by (cpu)))'),
            2,
        ),
    }

    for s in sessions:
        session_key = str(s.get("session_key", "")).strip()
        t_session = tautulli_session_map.get(session_key, {})

        s["tautulli_state"] = (t_session.get("state") or "").lower()
        s["tautulli_bandwidth_kbps"] = int(t_session.get("bandwidth", 0) or 0)
        s["tautulli_stream_container_decision"] = (t_session.get("stream_container_decision") or "").lower()
        s["tautulli_stream_video_decision"] = (t_session.get("stream_video_decision") or "").lower()
        s["tautulli_stream_audio_decision"] = (t_session.get("stream_audio_decision") or "").lower()
        s["tautulli_stream_subtitle_decision"] = (t_session.get("stream_subtitle_decision") or "").lower()
        s["tautulli_quality_profile"] = t_session.get("quality_profile")
        s["tautulli_product"] = t_session.get("product")
        s["tautulli_player"] = t_session.get("player")

    transcodes = sum(1 for s in sessions if (s.get("decision") or "").lower() == "transcode")
    direct_plays = sum(1 for s in sessions if (s.get("decision") or "").lower() == "directplay")

    state = {
        "plex": {
            "active_sessions": len(sessions),
            "transcodes": transcodes,
            "direct_plays": direct_plays,
            "sessions": sessions,
            "tautulli_activity": tautulli_activity,
        },
        "system": system,
        "history": {
            "recent_upload": recent_upload,
        },
    }

    state["facts"] = derive_facts(state)
    state["diagnosis"] = diagnose(state)
    state["structured_diagnosis"] = build_structured_diagnosis(state)
    return state


def summarize(state: dict) -> str:
    lines = []
    lines.append(f'Health: {state["diagnosis"]["health"]}')
    lines.append(f'Bottleneck: {state["diagnosis"]["bottleneck"]}')
    lines.append("")
    lines.append(
        f'System: CPU {state["system"]["host_cpu_percent"]}% | '
        f'RAM {state["system"]["host_ram_percent"]}% | '
        f'Plex upload {state["system"]["plex_upload_mbps"]} Mbps | '
        f'Total upload {state["system"]["total_upload_mbps"]} Mbps'
    )
    lines.append(
        f'Plex: {state["plex"]["active_sessions"]} session(s), '
        f'{state["plex"]["transcodes"]} transcode(s), '
        f'{state["plex"]["direct_plays"]} direct play(s)'
    )

    for s in state["plex"]["sessions"]:
        lines.append(
            f'- {s["title"]} ({s["year"]}) | {s["decision"]} | '
            f'{s["bitrate_kbps"]} kbps | video={s["video_codec"]} | '
            f'audio={s["audio_codec"]} | subtitles={s["subtitle_codec"] or "none"}'
        )

    lines.append("")
    lines.extend(f'Reason: {r}' for r in state["diagnosis"]["reasoning"])
    return "\n".join(lines)


def answer_question(question: str, state: dict) -> str:
    q = question.lower().strip()

    if "buffer" in q:
        reasons = diagnose_buffering(state)
        return "Buffering analysis:\n- " + "\n- ".join(reasons)

    if "transcod" in q and "why" not in q:
        transcode_titles = [
            s["title"]
            for s in state["plex"]["sessions"]
            if (s.get("decision") or "").lower() == "transcode"
        ]
        if not transcode_titles:
            return "No active transcodes are currently detected."
        return "Active transcodes:\n- " + "\n- ".join(transcode_titles)

    if "why" in q and "transcod" in q:
        issues = []
        for s in state["plex"]["sessions"]:
            if (s.get("decision") or "").lower() == "transcode":
                subtitle = (s.get("subtitle_codec") or "").lower()
                if subtitle in {"pgs", "vobsub"}:
                    issues.append(f'{s["title"]} is transcoding due to image-based subtitles.')
                else:
                    issues.append(f'{s["title"]} is transcoding due to client codec incompatibility.')

        if not issues:
            return "Nothing is currently transcoding."

        return "\n".join(issues)

    if "what" in q and "happening" in q:
        plex = state["plex"]
        system = state["system"]

        lines = []

        lines.append(f'{plex.get("active_sessions", 0)} active session(s)')
        lines.append(f'{plex.get("transcodes", 0)} transcode(s), {plex.get("direct_plays", 0)} direct play(s)')
        lines.append(f'CPU {system.get("host_cpu_percent", 0)}% | RAM {system.get("host_ram_percent", 0)}%')
        lines.append(f'Plex upload {system.get("plex_upload_mbps", 0)} Mbps')

        if plex["sessions"]:
            lines.append("\nActive streams:")
            for s in plex["sessions"]:
                lines.append(
                    f'- {s["title"]} | {s["decision"]} | {s["video_codec"]}/{s["audio_codec"]} | subs={s["subtitle_codec"]}'
                )

        return "\n".join(lines)

    if "upload" in q or "bandwidth" in q:
        return (
            f'Plex upload is {state["system"]["plex_upload_mbps"]} Mbps and total upload is '
            f'{state["system"]["total_upload_mbps"]} Mbps.'
        )

    if "healthy" in q or "health" in q:
        return (
            f'Server health is {state["diagnosis"]["health"]}. '
            f'CPU is {state["system"]["host_cpu_percent"]}%, '
            f'RAM is {state["system"]["host_ram_percent"]}%, '
            f'iowait is {state["system"]["iowait_percent"]}%.'
        )

    return summarize(state)


if __name__ == "__main__":
    state = build_state()

    if len(sys.argv) > 1:
        args = sys.argv[1:]

        if args[0] == "--llm":
            question = " ".join(args[1:]).strip()
            if not question:
                print("Usage: python app.py --llm \"your question here\"")
            else:
                print(answer_with_llm(question, state, answer_question(question, state)))
        else:
            question = " ".join(args).strip()
            print(answer_question(question, state))
    else:
        print("=== HUMAN SUMMARY ===")
        print(summarize(state))
        print("\n=== RAW STATE JSON ===")
        print(json.dumps(state, indent=2))
