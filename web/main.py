from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from alerts import load_recent_alert_history
from app import answer_question_result_from_state, build_state
from config import (
    ASK_STATE_CACHE_SECONDS,
    GRAFANA_BASE_URL,
    GRAFANA_DASHBOARD_SLUG,
    GRAFANA_DASHBOARD_UID,
    GRAFANA_DEFAULT_RANGE,
    GRAFANA_PANELS,
    GRAFANA_PUBLIC_DASHBOARD_URL,
)
from history import load_recent_history
from summaries import build_history_display_event, severity_display_label


BASE_DIR = Path(__file__).resolve().parent
STATIC_ASSET_VERSION = "20260403-ask-mobile-fix-1"
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Plex Assistant UI")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

_STATE_SNAPSHOT_CACHE: Dict[str, Any] = {
    "generated_at": None,
    "snapshot": None,
}
_ASK_CONVERSATIONS: Dict[str, List[Dict[str, str]]] = {}
ASK_SESSION_COOKIE = "plex_assistant_chat_session"
MAX_ASK_MEMORY_TURNS = 4

PAGE_CONFIG = {
    "home": {
        "template": "dashboard.html",
        "title": "Dashboard",
        "response_mode": "operator",
        "path": "/",
        "allow_mode_toggle": True,
        "locked_ask_mode": "",
        "locked_mode_label": "",
        "ask_helper_text": "Use Ask Plex as a dashboard copilot for playback quality, headroom, score changes, and what to do next.",
        "ask_placeholder": "Ask about current playback, score changes, headroom, or next steps...",
        "quick_questions": [
            "What's happening right now?",
            "Why did my score change?",
            "Am I at risk of buffering?",
            "Can I handle another stream?",
        ],
    },
    "operator": {
        "template": "operator.html",
        "title": "Operator View",
        "response_mode": "operator",
        "path": "/operator",
        "allow_mode_toggle": False,
        "locked_ask_mode": "operator",
        "locked_mode_label": "Operator analysis (telemetry-first)",
        "ask_helper_text": "Ask for bottlenecks, system mechanics, failure paths, and telemetry-grounded troubleshooting steps.",
        "ask_placeholder": "Ask about bottlenecks, constraints, root cause, or what to verify next...",
        "quick_questions": [
            "What is the bottleneck?",
            "Is WAN actually constrained?",
            "What would fail first?",
            "Why is this not a CPU issue?",
        ],
    },
    "manager": {
        "template": "manager.html",
        "title": "Manager View",
        "response_mode": "manager",
        "path": "/manager",
        "allow_mode_toggle": False,
        "locked_ask_mode": "manager",
        "locked_mode_label": "Manager view (decision + risk)",
        "ask_helper_text": "Ask for delivery risk, decision support, urgency, and the most useful next action.",
        "ask_placeholder": "Ask about delivery risk, whether to act, and what to watch next...",
        "quick_questions": [
            "Do I need to act?",
            "What is the delivery risk?",
            "Is this a real issue or just noise?",
            "What should I watch next?",
        ],
    },
    "history": {
        "template": "history.html",
        "title": "History",
        "response_mode": "operator",
        "path": "/history",
        "allow_mode_toggle": True,
        "locked_ask_mode": "",
        "locked_mode_label": "",
        "ask_helper_text": "Ask about recent diagnosis patterns, recovered incidents, recurring clients, and what history suggests operationally.",
        "ask_placeholder": "Ask about recent patterns, recurrence, or what changed over time...",
        "quick_questions": [
            "Summarize the recent diagnosis pattern.",
            "What changed most recently?",
            "Is this pattern concerning or mostly noise?",
            "What decision takeaway should I keep from this history?",
        ],
        "quick_questions_by_mode": {
            "operator": [
                "What pattern do these recent diagnosis events show?",
                "Which client is most often affected?",
                "Does history suggest a recurring issue?",
                "Are these mostly buffering or compatibility events?",
            ],
            "manager": [
                "Is this history actually concerning?",
                "Should I worry about this trend?",
                "Does this look like a real recurring problem or just noise?",
                "What should I keep an eye on from recent history?",
            ],
        },
    },
    "alerts": {
        "template": "alerts.html",
        "title": "Alerts",
        "response_mode": "operator",
        "path": "/alerts",
        "allow_mode_toggle": True,
        "locked_ask_mode": "",
        "locked_mode_label": "",
        "ask_helper_text": "Ask whether alerts need action, what they mean, and whether they suggest a recurring problem or just noise.",
        "ask_placeholder": "Ask whether alerts matter, need action, or suggest a recurring issue...",
        "quick_questions": [
            "Why did this alert trigger?",
            "Do any current alerts need action?",
            "Are these alerts just noise or a real pattern?",
            "What is the decision takeaway from the current alerts?",
        ],
        "quick_questions_by_mode": {
            "operator": [
                "Why are these alerts firing?",
                "Are these alerts related to buffering or WAN pressure?",
                "Do the recent alerts form a pattern?",
                "Are these alerts isolated or clustered?",
            ],
            "manager": [
                "Do I need to act on these alerts?",
                "How urgent is this alert history?",
                "Are these alerts meaningful or mostly noise?",
                "What should I watch next from these alerts?",
            ],
        },
    },
}

TREND_TIME_RANGES = [
    {"key": "15m", "label": "Last 15m", "from": "now-15m"},
    {"key": "1h", "label": "Last 1h", "from": "now-1h"},
    {"key": "6h", "label": "Last 6h", "from": "now-6h"},
    {"key": "24h", "label": "Last 24h", "from": "now-24h"},
    {"key": "7d", "label": "Last 7d", "from": "now-7d"},
]


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _safe_label(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return fallback


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_timestamp(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return value


def _base_page_context(request: Request, page_title: str) -> Dict[str, Any]:
    generated_at = _now_iso()
    return {
        "request": request,
        "page_title": page_title,
        "active_path": request.url.path,
        "static_asset_version": STATIC_ASSET_VERSION,
        "ok": True,
        "generated_at": generated_at,
        "generated_at_label": _format_timestamp(generated_at),
        "error": None,
    }


def _safe_build_state() -> Dict[str, Any]:
    generated_at = _now_iso()
    try:
        state = build_state()
        return {
            "ok": True,
            "generated_at": generated_at,
            "state": state,
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "generated_at": generated_at,
            "state": None,
            "error": str(exc),
        }


def _display_sessions(sessions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for raw_session in sessions:
        session = _as_dict(raw_session)
        rows.append(
            {
                "title_label": "{}{}".format(
                    _safe_label(session.get("title"), "Unknown"),
                    " ({})".format(_safe_label(session.get("year"))) if session.get("year") else "",
                ),
                "client_label": _safe_label(
                    session.get("player_product") or session.get("tautulli_product"),
                    "unknown",
                ),
                "state_label": _safe_label(session.get("tautulli_state") or session.get("player_state"), "unknown"),
                "is_buffering": _safe_label(session.get("tautulli_state")).lower() == "buffering",
                "decision_label": _safe_label(session.get("decision"), "unknown"),
                "bitrate_label": "{} kbps".format(_safe_label(session.get("bitrate_kbps"), "0")),
                "traits_label": "{} / {} / {}".format(
                    _safe_label(session.get("container"), "unknown"),
                    _safe_label(session.get("audio_codec"), "unknown"),
                    _safe_label(session.get("subtitle_codec"), "no subtitles"),
                ),
            }
        )
    return rows


def _display_alerts(alerts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for raw_alert in alerts:
        alert = _as_dict(raw_alert)
        severity_label = _safe_label(alert.get("severity"), "info")
        rows.append(
            {
                **alert,
                "severity_label": severity_label,
                "severity_class": "severity-{}".format(severity_label),
                "sessions_label": ", ".join(
                    _safe_label(item) for item in _as_list(alert.get("affected_sessions")) if _safe_label(item)
                ) or "none",
                "clients_label": ", ".join(
                    _safe_label(item) for item in _as_list(alert.get("affected_clients")) if _safe_label(item)
                ) or "none",
                "cooldown_label": _safe_label(alert.get("cooldown_key"), "n/a"),
            }
        )
    return rows


def _display_dashboard_view(state: Dict[str, Any]) -> Dict[str, Any]:
    manager_summary = _as_dict(state.get("manager_summary"))
    diagnosis_presentation = _as_dict(state.get("diagnosis_presentation"))
    issue_metadata = _as_dict(state.get("issue_metadata"))
    playback_quality = _as_dict(manager_summary.get("playback_quality"))
    plex = _as_dict(state.get("plex"))
    system = _as_dict(state.get("system"))
    facts = _as_dict(state.get("facts"))
    alert_rows = _display_alerts(_as_list(state.get("alerts")))[:3]
    session_rows = _display_sessions(_as_list(plex.get("sessions")))
    severity_raw = _safe_label(diagnosis_presentation.get("severity"), "info")
    upload_limit = 41.0
    try:
        from config import UPLOAD_LIMIT_MBPS  # local import to avoid widening module surface

        upload_limit = float(UPLOAD_LIMIT_MBPS)
    except Exception:
        upload_limit = 41.0

    total_upload = float(system.get("total_upload_mbps") or 0)
    upload_saturation_percent = round((total_upload / upload_limit) * 100, 1) if upload_limit > 0 else 0
    return {
        "service_health_label": _safe_label(manager_summary.get("service_health"), "unknown"),
        "service_health_tone": _safe_label(manager_summary.get("service_health_tone"), "healthy"),
        "severity_label": _safe_label(diagnosis_presentation.get("severity_display_label"), severity_display_label(severity_raw)),
        "severity_class": "severity-{}".format(severity_raw),
        "scope_label": _safe_label(diagnosis_presentation.get("scope"), "unknown"),
        "confidence_label": _safe_label(diagnosis_presentation.get("confidence"), "low"),
        "impact_score_label": "{}/100".format(_safe_label(playback_quality.get("quality_score"), issue_metadata.get("impact_score") or "0")),
        "impact_level_label": _safe_label(playback_quality.get("quality_label"), issue_metadata.get("impact_label") or "Minimal"),
        "capacity_headroom_label": _safe_label(playback_quality.get("headroom_label"), issue_metadata.get("capacity_headroom") or "Comfortable"),
        "impact_driver_summary": _safe_label(issue_metadata.get("impact_driver_summary"), "No meaningful playback strain is currently confirmed."),
        "capacity_headroom_summary": _safe_label(
            playback_quality.get("headroom_summary"),
            issue_metadata.get("capacity_headroom_summary") or "Current telemetry suggests the system is comfortably handling playback.",
        ),
        "diagnosis_label": _safe_label(playback_quality.get("home_diagnosis_label"), _safe_label(diagnosis_presentation.get("primary_diagnosis_label"), "unknown")),
        "supporting_text": _safe_label(playback_quality.get("note"), _safe_label(diagnosis_presentation.get("supporting_text"), "")),
        "contributing_factors": [
            _safe_label(item)
            for item in _as_list(diagnosis_presentation.get("dashboard_contributing_factors"))[:2]
            if _safe_label(item)
        ],
        "primary_action": _safe_label(diagnosis_presentation.get("primary_action"), "No immediate action needed."),
        "active_sessions_label": _safe_label(plex.get("active_sessions"), "0"),
        "playback_summary": "{} transcode(s), {} direct play(s)".format(
            _safe_label(plex.get("transcodes"), "0"),
            _safe_label(plex.get("direct_plays"), "0"),
        ),
        "manager_impact_summary": _safe_label(manager_summary.get("impact_summary"), "No summary available."),
        "manager_diagnosis_label": _safe_label(manager_summary.get("current_diagnosis_label"), "unknown"),
        "manager_contributing_summary": _safe_label(manager_summary.get("contributing_summary"), ""),
        "manager_escalation_label": "Yes" if manager_summary.get("escalation_needed") else "No",
        "manager_trend_summary": _safe_label(manager_summary.get("trend_summary"), "No trend summary available."),
        "recent_playback_note": _safe_label(manager_summary.get("recent_playback_note"), ""),
        "recent_issue_active": bool(manager_summary.get("recent_issue_active")),
        "playback_quality_note": _safe_label(playback_quality.get("note"), ""),
        "playback_quality_window_active": bool(playback_quality.get("recent_window_active")),
        "playback_quality_recent_count": _safe_label(playback_quality.get("recent_buffer_count"), "0"),
        "delivery_confidence_label": _safe_label(playback_quality.get("delivery_confidence_label"), "High"),
        "delivery_confidence_summary": _safe_label(playback_quality.get("delivery_confidence_summary"), ""),
        "recurrence_risk_label": _safe_label(playback_quality.get("recurrence_risk_label"), "Low"),
        "recurrence_summary": _safe_label(playback_quality.get("recurrence_summary"), ""),
        "score_driver_summary": _safe_label(playback_quality.get("score_driver_summary"), ""),
        "score_driver_items": [
            _safe_label(item)
            for item in _as_list(playback_quality.get("score_driver_items"))
            if _safe_label(item)
        ],
        "quick_stats": [
            {"label": "Host CPU", "value": "{}%".format(_safe_label(system.get("host_cpu_percent"), "0"))},
            {"label": "Host RAM", "value": "{}%".format(_safe_label(system.get("host_ram_percent"), "0"))},
            {"label": "Plex CPU", "value": "{}%".format(_safe_label(system.get("plex_cpu_host_percent"), "0"))},
            {"label": "Upload Saturation", "value": "{}%".format(_safe_label(upload_saturation_percent, "0"))},
            {"label": "Plex Upload", "value": "{} Mbps".format(_safe_label(system.get("plex_upload_mbps"), "0"))},
            {"label": "Total Upload", "value": "{} Mbps".format(_safe_label(system.get("total_upload_mbps"), "0"))},
            {"label": "Active Sessions", "value": _safe_label(plex.get("active_sessions"), "0")},
            {"label": "Transcodes", "value": _safe_label(plex.get("transcodes"), "0")},
        ],
        "quick_stats_note": (
            "Current Plex delivery is stable, but recent WAN behavior still matters."
            if playback_quality.get("recent_window_active") or playback_quality.get("recurrence_risk_label") in {"Guarded", "High"}
            else "System is comfortably handling current playback activity."
            if severity_raw == "info" and not facts.get("buffering_detected")
            else "Watch these live numbers alongside the current diagnosis."
        ),
        "alert_rows": alert_rows,
        "session_rows": session_rows,
    }


def _display_manager_view(state: Dict[str, Any]) -> Dict[str, Any]:
    manager_summary = _as_dict(state.get("manager_summary"))
    diagnosis_presentation = _as_dict(state.get("diagnosis_presentation"))
    executive_summary = _as_dict(manager_summary.get("executive_decision_summary"))
    affected_scope = _as_dict(manager_summary.get("affected_scope_summary"))
    recommendation_ladder = _as_dict(manager_summary.get("recommendation_ladder"))
    impact_breakdown = _as_dict(manager_summary.get("impact_breakdown"))
    playback_quality = _as_dict(manager_summary.get("playback_quality"))
    diagnosis_label = _safe_label(manager_summary.get("current_diagnosis_label"), "")
    diagnosis_code = _safe_label(manager_summary.get("current_diagnosis"), diagnosis_presentation.get("primary_diagnosis", ""))
    if not diagnosis_label and diagnosis_code in {"", "none_detected", "unknown"}:
        diagnosis_label = "No active issue"
    elif diagnosis_label.lower() == "unknown" and diagnosis_code in {"", "none_detected", "unknown"}:
        diagnosis_label = "No active issue"
    return {
        "service_health": _safe_label(manager_summary.get("service_health"), "unknown"),
        "service_health_tone": _safe_label(manager_summary.get("service_health_tone"), "healthy"),
        "severity_label": _safe_label(manager_summary.get("severity_display_label"), severity_display_label(_safe_label(manager_summary.get("severity"), "info"))),
        "scope_label": _safe_label(manager_summary.get("issue_scope"), "unknown"),
        "confidence_label": _safe_label(manager_summary.get("confidence"), "low"),
        "impact_summary": _safe_label(manager_summary.get("impact_summary"), "No summary available."),
        "diagnosis_label": diagnosis_label or "No active issue",
        "contributing_summary": _safe_label(manager_summary.get("contributing_summary"), ""),
        "insight": _safe_label(manager_summary.get("insight"), ""),
        "recommended_action": _safe_label(manager_summary.get("recommended_action"), "No immediate action needed."),
        "escalation_label": "Yes" if manager_summary.get("escalation_needed") else "No",
        "trend_summary": _safe_label(manager_summary.get("trend_summary"), "No trend summary available."),
        "executive_user_impact": _safe_label(executive_summary.get("user_impact"), "none"),
        "executive_action_urgency": _safe_label(executive_summary.get("action_urgency"), "none"),
        "executive_confidence": _safe_label(executive_summary.get("confidence"), "low"),
        "executive_conclusion": _safe_label(executive_summary.get("conclusion"), "No meaningful operational issue is confirmed."),
        "why_not_worse": [_safe_label(item) for item in _as_list(manager_summary.get("why_this_is_not_worse")) if _safe_label(item)],
        "escalation_triggers": [_safe_label(item) for item in _as_list(manager_summary.get("escalation_triggers")) if _safe_label(item)],
        "affected_users_label": "{} / {}".format(
            _safe_label(affected_scope.get("users_affected"), "0"),
            _safe_label(affected_scope.get("active_sessions"), "0"),
        ),
        "top_affected_client_label": _safe_label(affected_scope.get("top_affected_client"), "none"),
        "other_sessions_healthy_label": "Yes" if affected_scope.get("other_sessions_healthy") else "No",
        "playback_progressing_label": "Yes" if affected_scope.get("playback_progressing") else "No",
        "trend_judgment_label": _safe_label(manager_summary.get("trend_judgment"), "stable"),
        "delivery_confidence_label": _safe_label(playback_quality.get("delivery_confidence_label"), "High"),
        "delivery_confidence_summary": _safe_label(playback_quality.get("delivery_confidence_summary"), ""),
        "recurrence_risk_label": _safe_label(manager_summary.get("recurrence_risk_label"), "Low"),
        "recurrence_summary": _safe_label(manager_summary.get("recurrence_summary"), ""),
        "playback_quality_label": _safe_label(playback_quality.get("quality_label"), "Stable"),
        "recommendation_bucket": _safe_label(recommendation_ladder.get("bucket"), "do_nothing"),
        "recommendation_reason": _safe_label(recommendation_ladder.get("reason"), "No urgent operational action is currently needed."),
        "recommendation_primary_action": _safe_label(
            recommendation_ladder.get("primary_action"),
            _safe_label(manager_summary.get("recommended_action"), "No immediate action needed."),
        ),
        "impact_breakdown_rows": [
            {"label": "User experience impact", "value": _safe_label(impact_breakdown.get("user_experience_impact"), "none")},
            {"label": "Server health impact", "value": _safe_label(impact_breakdown.get("server_health_impact"), "low")},
            {"label": "Network risk", "value": _safe_label(impact_breakdown.get("network_risk"), "low")},
            {"label": "Compatibility friction", "value": _safe_label(impact_breakdown.get("compatibility_friction"), "low")},
            {"label": "Service-wide risk", "value": _safe_label(impact_breakdown.get("service_wide_risk"), "low")},
        ],
    }


def _upload_saturation_percent(system: Dict[str, Any]) -> float:
    upload_limit = 41.0
    try:
        from config import UPLOAD_LIMIT_MBPS

        upload_limit = float(UPLOAD_LIMIT_MBPS)
    except Exception:
        upload_limit = 41.0

    total_upload = float(system.get("total_upload_mbps") or 0)
    return round((total_upload / upload_limit) * 100, 1) if upload_limit > 0 else 0.0


def _avg_upload_saturation_percent(facts: Dict[str, Any]) -> float:
    upload_limit = 41.0
    try:
        from config import UPLOAD_LIMIT_MBPS

        upload_limit = float(UPLOAD_LIMIT_MBPS)
    except Exception:
        upload_limit = 41.0

    avg_upload = float(facts.get("recent_upload_avg_mbps") or 0)
    return round((avg_upload / upload_limit) * 100, 1) if upload_limit > 0 else 0.0


def _operator_transcode_mechanics(state: Dict[str, Any]) -> str:
    facts = _as_dict(state.get("facts"))
    system = _as_dict(state.get("system"))
    issue_metadata = _as_dict(state.get("issue_metadata"))
    playback_quality = _as_dict(_as_dict(state.get("manager_summary")).get("playback_quality"))
    sessions = _as_list(facts.get("session_facts"))
    transcodes = [sf for sf in sessions if _as_dict(sf).get("is_transcode")]
    if not transcodes:
        if playback_quality.get("recent_window_active"):
            return "No active transcoding is currently occurring, but recent playback disruption suggests the delivery path has only recently stabilized after tighter WAN conditions."
        return "No active transcoding is currently occurring. Playback is staying on direct play or direct stream paths."

    primary = _as_dict(transcodes[0])
    title = _safe_label(primary.get("title"), "the active session")
    client = _safe_label(primary.get("client_name") or primary.get("tautulli_product_name"), "the current client")
    audio_transcode = primary.get("audio_decision") == "transcode"
    video_transcode = primary.get("video_decision") == "transcode" or primary.get("container_decision") == "transcode"
    subtitle_transcode = primary.get("subtitle_decision") == "transcode" or primary.get("is_image_subtitle")
    container_transcode = primary.get("container_decision") == "transcode"

    if audio_transcode and not video_transcode and not subtitle_transcode and not container_transcode:
        transcode_type = "an audio-only compatibility transcode"
        why = "Session facts show audio conversion while the video path remains intact, which points to client audio capability mismatch."
    elif subtitle_transcode and not video_transcode and not container_transcode:
        transcode_type = "a subtitle-driven compatibility transcode"
        why = "Subtitle handling is the strongest explicit transcode trigger in the current session facts."
    elif video_transcode and not container_transcode:
        transcode_type = "a video compatibility transcode"
        why = "Session facts show the video path is being converted for the client."
    elif container_transcode and not video_transcode:
        transcode_type = "a container-driven compatibility transcode"
        why = "Session facts show container adaptation without clear video re-encoding."
    elif audio_transcode or video_transcode or subtitle_transcode or container_transcode:
        transcode_type = "a compatibility transcode"
        why = "The playback path is being adapted for client compatibility, but the exact dominant subtype is mixed."
    else:
        transcode_type = "a client-driven transcode"
        why = "The session is transcoding, but the subtype is not explicit enough to claim a narrower cause."

    dominant = _safe_label(issue_metadata.get("dominant_impact_factor"), "none")
    if dominant == "upload":
        tail = "Upload is the leading live constraint, so delivery headroom matters more than compute right now and similar spikes could renew buffering."
    elif dominant == "host_pressure":
        tail = "Host pressure is the leading live constraint, so compute headroom matters more than delivery right now."
    elif dominant == "buffering_confirmed":
        tail = "Confirmed buffering is the strongest live signal, so playback quality is the main concern right now."
    elif playback_quality.get("recurrence_risk_label") in {"Guarded", "High"}:
        tail = "Playback is functioning now, but recent WAN tightness suggests this delivery path is still fragile under bursty upload."
    else:
        tail = "No buffering is confirmed, Plex CPU is {}%, and host CPU is {}%, so the current workload remains mechanically light.".format(
            _safe_label(system.get("plex_cpu_host_percent"), "0"),
            _safe_label(system.get("host_cpu_percent"), "0"),
        )

    return "{} on {} via {}. {} {}".format(
        transcode_type.capitalize(),
        title,
        client,
        why,
        tail,
    )


def _operator_resource_analysis(state: Dict[str, Any]) -> List[str]:
    facts = _as_dict(state.get("facts"))
    system = _as_dict(state.get("system"))
    playback_quality = _as_dict(_as_dict(state.get("manager_summary")).get("playback_quality"))
    upload_percent = _avg_upload_saturation_percent(facts)
    instant_upload_percent = _upload_saturation_percent(system)
    analysis = []
    analysis.append(
        "Plex CPU is {}%, so the active playback work is currently cheap for the media engine.".format(
            _safe_label(system.get("plex_cpu_host_percent"), "0")
        )
    )
    analysis.append(
        "Host CPU is {}%, leaving substantial processing headroom before transcoding would become compute-bound.".format(
            _safe_label(system.get("host_cpu_percent"), "0")
        )
    )
    if playback_quality.get("recurrence_risk_label") in {"Guarded", "High"} or upload_percent >= 70:
        analysis.append(
            "Average upload is at about {}% of the configured ceiling, and recent delivery behavior suggests WAN headroom is fragile even though playback is currently functioning.".format(
                _safe_label(upload_percent, "0")
            )
        )
    elif upload_percent >= 50:
        analysis.append(
            "Average upload is at about {}% of the configured ceiling: meaningful WAN load, but not yet a hard saturation event.".format(
                _safe_label(upload_percent, "0")
            )
        )
    elif facts.get("burst_upload_saturation"):
        analysis.append(
            "Upload shows brief spikes up to about {}% of the configured ceiling, but average WAN load remains serviceable and the spikes do not look sustained.".format(
                _safe_label(instant_upload_percent, "0")
            )
        )
    else:
        analysis.append(
            "Average upload is at about {}% of the configured ceiling: real background load, but still below the range where delivery should become fragile.".format(
                _safe_label(upload_percent, "0")
            )
        )

    if float(system.get("host_ram_percent") or 0) < 85:
        analysis.append("RAM is at {}%, which is well below pressure territory for this workload.".format(_safe_label(system.get("host_ram_percent"), "0")))
    if float(system.get("iowait_percent") or 0) < 8:
        analysis.append("Disk I/O wait is {}%, so storage does not look like a contributing constraint.".format(_safe_label(system.get("iowait_percent"), "0")))
    if playback_quality.get("recurrence_risk_label") in {"Guarded", "High"}:
        analysis.append("CPU, RAM, and disk do not look like the limiting subsystems; recent delivery fragility is better explained by WAN headroom than by compute pressure.")
    elif not facts.get("sustained_upload_saturation") and not facts.get("sustained_upload_high") and upload_percent < 80:
        analysis.append("Because upload is not near saturation, the network path does not currently look like the limiting subsystem.")
    return analysis[:5]


def _operator_is_observational_mode(state: Dict[str, Any]) -> bool:
    facts = _as_dict(state.get("facts"))
    structured_diagnosis = _as_dict(state.get("structured_diagnosis"))
    diagnosis_presentation = _as_dict(state.get("diagnosis_presentation"))
    playback_quality = _as_dict(_as_dict(state.get("manager_summary")).get("playback_quality"))
    sessions = _as_list(facts.get("session_facts"))
    transcodes = [_as_dict(sf) for sf in sessions if _as_dict(sf).get("is_transcode")]
    buffering_confirmed = bool(structured_diagnosis.get("buffering_confirmed"))
    buffering_detected = bool(facts.get("buffering_detected"))
    diagnosis = _safe_label(diagnosis_presentation.get("primary_diagnosis"), "none_detected")
    recurrence_risk = _safe_label(playback_quality.get("recurrence_risk_label"), "Low")
    quality_label = _safe_label(playback_quality.get("quality_label"), "")

    # Healthy mode should stay observational unless telemetry shows a real issue,
    # meaningful instability, or a playback path divergence that needs investigation.
    if buffering_confirmed or buffering_detected:
        return False
    if diagnosis not in {"none_detected", ""}:
        return False
    if recurrence_risk in {"Guarded", "High"}:
        return False
    if any(token in quality_label.lower() for token in ["degraded", "unstable", "recovered", "fragile"]):
        return False
    if transcodes:
        return False
    return True


def _operator_system_behavior(state: Dict[str, Any]) -> List[str]:
    facts = _as_dict(state.get("facts"))
    plex = _as_dict(state.get("plex"))
    sessions = _as_list(facts.get("session_facts"))
    healthy_sessions = [_as_dict(sf) for sf in sessions if _as_dict(sf).get("is_healthy_playing_session")]
    lines: List[str] = []

    if healthy_sessions:
        lines.append(
            "{} active session(s) are playing without confirmed buffering or playback-path disruption.".format(
                len(healthy_sessions)
            )
        )
    else:
        lines.append(
            "{} active session(s) are present and no abnormal playback behavior is currently confirmed.".format(
                _safe_label(plex.get("active_sessions"), "0")
            )
        )

    if int(plex.get("transcodes") or 0) == 0:
        lines.append("Playback is staying on direct delivery paths with no active transcode investigation needed.")
    else:
        lines.append("Active playback is present, but no transcode is currently affecting delivery quality.")

    lines.append("No active issue is currently confirmed by telemetry.")
    return lines[:3]


def _operator_constraints_ruled_out(state: Dict[str, Any]) -> List[str]:
    facts = _as_dict(state.get("facts"))
    system = _as_dict(state.get("system"))
    ruled_out = []

    if float(system.get("host_cpu_percent") or 0) < 60 and float(system.get("plex_cpu_host_percent") or 0) < 25:
        ruled_out.append("CPU pressure is not currently limiting playback.")
    if float(system.get("host_ram_percent") or 0) < 85:
        ruled_out.append("RAM pressure is not currently limiting playback.")
    if float(system.get("iowait_percent") or 0) < 8:
        ruled_out.append("Disk I/O is not currently limiting playback.")
    if not facts.get("sustained_upload_saturation") and not facts.get("sustained_upload_high"):
        ruled_out.append("Upload capacity is not currently showing sustained pressure.")
    if int(_as_dict(state.get("plex")).get("transcodes") or 0) == 0:
        ruled_out.append("Transcoding is not currently contributing to playback quality.")
    return ruled_out[:5]


def _operator_session_reasoning(state: Dict[str, Any]) -> List[str]:
    facts = _as_dict(state.get("facts"))
    sessions = _as_list(facts.get("session_facts"))
    lines: List[str] = []
    transcodes = [_as_dict(sf) for sf in sessions if _as_dict(sf).get("is_transcode")]
    healthy = [_as_dict(sf) for sf in sessions if _as_dict(sf).get("is_healthy_playing_session")]
    if _operator_is_observational_mode(state):
        if len(sessions) > 1 and any(_as_dict(sf).get("tautulli_state") == "buffering" for sf in sessions):
            return ["Cross-session behavior differs, but no active issue is currently confirmed."]
        return []

    for sf in transcodes[:2]:
        title = _safe_label(sf.get("title"), "unknown session")
        client = _safe_label(sf.get("client_name") or sf.get("tautulli_product_name"), "unknown client")
        if sf.get("audio_decision") == "transcode" and sf.get("video_decision") != "transcode" and sf.get("container_decision") != "transcode":
            reason = "audio compatibility is the clearest explicit trigger"
        elif (sf.get("subtitle_decision") == "transcode" or sf.get("is_image_subtitle")) and sf.get("video_decision") != "transcode":
            reason = "subtitle handling is the clearest explicit trigger"
        elif sf.get("video_decision") == "transcode":
            reason = "video conversion is explicitly active"
        elif sf.get("container_decision") == "transcode":
            reason = "container adaptation is explicitly active"
        else:
            reason = "the session is transcoding for client compatibility, but subtype evidence is mixed"
        lines.append("{} on {} is transcoding because {}.".format(title, client, reason))

    if healthy:
        lines.append(
            "Because {} other session(s) are direct playing normally at the same time, the issue is more consistent with client compatibility than server-wide resource strain.".format(
                len(healthy)
            )
        )
    elif not transcodes:
        lines.append("No session is currently transcoding, so playback is staying on direct delivery paths.")

    if facts.get("buffering_session_count", 0) == 0:
        lines.append("No session is currently confirmed buffering, so playback is still progressing despite the compatibility workaround.")
    return lines[:4]


def _operator_failure_paths(state: Dict[str, Any]) -> List[str]:
    facts = _as_dict(state.get("facts"))
    system = _as_dict(state.get("system"))
    issue_metadata = _as_dict(state.get("issue_metadata"))
    playback_quality = _as_dict(_as_dict(state.get("manager_summary")).get("playback_quality"))
    sessions = _as_list(facts.get("session_facts"))
    transcode_count = sum(1 for sf in sessions if _as_dict(sf).get("is_transcode"))
    upload_percent = _avg_upload_saturation_percent(facts)
    dominant = _safe_label(issue_metadata.get("dominant_impact_factor"), "none")
    if _operator_is_observational_mode(state):
        return []

    candidates = []

    if facts.get("sustained_upload_saturation") or upload_percent >= 35 or playback_quality.get("recurrence_risk_label") in {"Guarded", "High"}:
        candidates.append(
            (
                6 if dominant == "upload" or playback_quality.get("recurrence_risk_label") in {"Guarded", "High"} else (4 if upload_percent >= 85 else 3 if upload_percent >= 65 else 2),
                "If upload rises materially above the current {}% level or the same burst pattern returns, delivery throughput would likely fail before CPU does and buffering would appear first.".format(
                    _safe_label(upload_percent, "0")
                ),
            )
        )
    if transcode_count > 0:
        candidates.append(
            (
                5 if dominant == "host_pressure" else (4 if transcode_count > 1 or float(system.get("plex_cpu_host_percent") or 0) >= 35 else 2),
                "If more sessions shift from direct play to transcode, Plex CPU would be the next subsystem to tighten and transcode delivery would degrade before RAM becomes the limiter.",
            )
        )
    if facts.get("buffering_session_count", 0) > 0:
        candidates.append(
            (
                5 if dominant == "buffering_confirmed" else 3,
                "If buffering persists or spreads to additional sessions, the issue would stop being a localized compatibility problem and become a broader playback degradation event.",
            )
        )
    if facts.get("healthy_playing_session_count", 0) > 0:
        candidates.append(
            (
                2,
                "If the issue broadens beyond the current client while healthy direct-play sessions disappear, the scope would be widening from compatibility friction into a broader service problem.",
            )
        )
    if float(system.get("iowait_percent") or 0) >= 8:
        candidates.append(
            (
                1,
                "Disk I/O would become a plausible next failure path if wait time keeps rising from current levels.",
            )
        )
    if not candidates:
        candidates.append((1, "No immediate failure path stands out; the next meaningful change would be loss of headroom rather than an active bottleneck."))

    return [item for _, item in sorted(candidates, key=lambda pair: pair[0], reverse=True)[:3]]


def _operator_contextual_checks(state: Dict[str, Any]) -> List[Dict[str, str]]:
    facts = _as_dict(state.get("facts"))
    system = _as_dict(state.get("system"))
    sessions = _as_list(facts.get("session_facts"))
    checks: List[Dict[str, str]] = []
    upload_percent = _avg_upload_saturation_percent(facts)
    transcode_count = sum(1 for sf in sessions if _as_dict(sf).get("is_transcode"))
    if _operator_is_observational_mode(state):
        return []

    if upload_percent >= 70 or facts.get("sustained_upload_saturation") or facts.get("sustained_upload_high"):
        checks.append(
            {
                "hypothesis": "Delivery capacity is becoming the next failure mode.",
                "signal": "Inspect upload saturation, remaining upload headroom, and Plex share of total upload.",
                "result": "Sustained high utilization with shrinking headroom confirms network risk; stable headroom weakens it.",
            }
        )
    if float(system.get("plex_cpu_host_percent") or 0) >= 25 or transcode_count > 1:
        checks.append(
            {
                "hypothesis": "Transcode scaling is the next likely systems constraint.",
                "signal": "Inspect Plex CPU while additional transcodes start or bitrate demand rises.",
                "result": "A clear CPU rise with new transcodes confirms compute sensitivity; flat CPU weakens that hypothesis.",
            }
        )
    if any(_as_dict(sf).get("audio_decision") == "transcode" for sf in sessions):
        checks.append(
            {
                "hypothesis": "Client audio compatibility is driving the current transcode.",
                "signal": "Inspect the affected session’s audio decision and compare playback on another client path.",
                "result": "If audio transcode clears on another client, compatibility is confirmed; if it persists everywhere, the cause is broader.",
            }
        )
    if any(_as_dict(sf).get("subtitle_decision") == "transcode" or _as_dict(sf).get("is_image_subtitle") for sf in sessions):
        checks.append(
            {
                "hypothesis": "Subtitle handling is altering the playback path.",
                "signal": "Inspect subtitle decision and test playback with subtitles disabled or switched.",
                "result": "If the transcode path relaxes when subtitles change, subtitle compatibility is the stronger explanation.",
            }
        )
    if not checks:
        checks.append(
            {
                "hypothesis": "No active bottleneck is currently present.",
                "signal": "Re-check upload and Plex CPU only if playback quality degrades or more sessions begin transcoding.",
                "result": "Stable telemetry would keep this in low-impact compatibility territory.",
            }
        )
    return checks[:4]


def _operator_confidence_note(state: Dict[str, Any]) -> str:
    diagnosis_presentation = _as_dict(state.get("diagnosis_presentation"))
    facts = _as_dict(state.get("facts"))
    confidence = _safe_label(diagnosis_presentation.get("confidence"), "low")
    if confidence == "high":
        return ""

    sessions = [_as_dict(sf) for sf in _as_list(facts.get("session_facts")) if _as_dict(sf).get("is_transcode")]
    if any(
        sf.get("audio_decision") == "transcode"
        and (sf.get("subtitle_decision") == "transcode" or sf.get("is_image_subtitle") or sf.get("container_decision") == "transcode")
        for sf in sessions
    ):
        return "Evidence is mixed across audio, subtitle, or container signals."
    if diagnosis_presentation.get("primary_diagnosis") == "transcoding" and sessions:
        return "Subtype is inferred from playback behavior."
    if facts.get("buffering_risk_detected") and not _as_dict(state.get("structured_diagnosis")).get("buffering_confirmed"):
        return "The pattern suggests risk, but active failure is not directly confirmed."
    return "Evidence is suggestive but not definitive."


def _operator_recurrence_hint(state: Dict[str, Any]) -> str:
    diagnosis_presentation = _as_dict(state.get("diagnosis_presentation"))
    current_diagnosis = _safe_label(diagnosis_presentation.get("primary_diagnosis"), "unknown")
    if current_diagnosis == "unknown":
        return ""

    facts = _as_dict(state.get("facts"))
    current_clients = {
        _safe_label(item)
        for item in (_as_list(facts.get("affected_session_client_names")) or [sf.get("client_name") for sf in _as_list(facts.get("session_facts")) if _as_dict(sf).get("is_transcode")])
        if _safe_label(item)
    }
    if not current_clients:
        return ""

    matches = 0
    for event in _as_list(state.get("recent_history_events")):
        raw_event = _as_dict(event)
        if _safe_label(raw_event.get("diagnosis")) != current_diagnosis:
            continue
        event_clients = {_safe_label(item) for item in _as_list(raw_event.get("affected_clients")) if _safe_label(item)}
        if current_clients & event_clients:
            matches += 1

    if matches >= 2:
        client_name = sorted(current_clients)[0]
        if current_diagnosis == "transcoding":
            return "Recent history suggests {} has triggered similar low-impact transcodes multiple times.".format(client_name)
        return "Recent history suggests {} has shown a similar localized issue pattern multiple times.".format(client_name)
    return ""


def _operator_takeaway(state: Dict[str, Any]) -> str:
    diagnosis_presentation = _as_dict(state.get("diagnosis_presentation"))
    issue_metadata = _as_dict(state.get("issue_metadata"))
    system = _as_dict(state.get("system"))
    playback_quality = _as_dict(_as_dict(state.get("manager_summary")).get("playback_quality"))
    diagnosis_label = _safe_label(
        playback_quality.get("manager_diagnosis_label"),
        _safe_label(diagnosis_presentation.get("primary_diagnosis_label"), "No confirmed issue"),
    )
    impact_label = _safe_label(issue_metadata.get("impact_label"), "Minimal").lower()
    dominant = _safe_label(issue_metadata.get("dominant_impact_factor"), "none")

    if playback_quality.get("recent_window_active") and dominant == "upload":
        return "Playback has recovered, but WAN delivery remains the primary live constraint and similar spikes could trigger renewed buffering."
    if dominant == "upload":
        return "Delivery load is the main live constraint, and usable headroom remains guarded rather than comfortable."
    if dominant == "host_pressure" and (
        float(system.get("host_cpu_percent") or 0) >= 60
        or float(system.get("plex_cpu_host_percent") or 0) >= 35
        or float(system.get("host_ram_percent") or 0) >= 85
        or float(system.get("iowait_percent") or 0) >= 8
    ):
        return "Compute load is the main live constraint, but current CPU telemetry still indicates healthy processing headroom."
    if dominant == "buffering_confirmed":
        return "Confirmed playback degradation is the strongest live signal and should drive troubleshooting priority."
    if playback_quality.get("recent_window_active"):
        return "Playback is stable now, but recent buffering suggests delivery confidence remains reduced under current WAN conditions."
    if diagnosis_presentation.get("primary_diagnosis") == "transcoding":
        return "{} is active with {} system impact and no evidence of broader service strain.".format(
            diagnosis_label,
            impact_label,
        )
    if diagnosis_presentation.get("primary_diagnosis") == "none_detected":
        return "No active failure is confirmed right now, but delivery confidence depends on maintaining current WAN margin."
    return "{} is currently the best fit, with {} system impact and localized scope.".format(
        diagnosis_label,
        impact_label,
    )


def _display_operator_view(state: Dict[str, Any]) -> Dict[str, Any]:
    diagnosis_presentation = _as_dict(state.get("diagnosis_presentation"))
    structured_diagnosis = _as_dict(state.get("structured_diagnosis"))
    action_plan = _as_dict(state.get("action_plan"))
    history_summary = _as_dict(state.get("history_summary"))
    state_change = _as_dict(state.get("state_change"))
    issue_metadata = _as_dict(state.get("issue_metadata"))
    playback_quality = _as_dict(_as_dict(state.get("manager_summary")).get("playback_quality"))
    severity_raw = _safe_label(diagnosis_presentation.get("severity"), "info")
    confidence_label = _safe_label(diagnosis_presentation.get("confidence"), "low")
    observational_mode = _operator_is_observational_mode(state)
    session_reasoning = _operator_session_reasoning(state)
    failure_paths = _operator_failure_paths(state)
    verification_steps = _operator_contextual_checks(state)

    return {
        "severity_label": _safe_label(diagnosis_presentation.get("severity_display_label"), severity_display_label(severity_raw)),
        "severity_class": "severity-{}".format(severity_raw),
        "scope_label": _safe_label(diagnosis_presentation.get("scope"), "unknown"),
        "confidence_label": confidence_label,
        "confidence_note": _operator_confidence_note(state),
        "impact_score_label": "{}/100".format(_safe_label(issue_metadata.get("impact_score"), "0")),
        "impact_level_label": _safe_label(issue_metadata.get("impact_label"), "Minimal"),
        "capacity_headroom_label": _safe_label(playback_quality.get("headroom_label"), issue_metadata.get("capacity_headroom") or "Comfortable"),
        "dominant_impact_factor": _safe_label(issue_metadata.get("dominant_impact_factor"), "none"),
        "impact_driver_summary": _safe_label(issue_metadata.get("impact_driver_summary"), "No meaningful playback strain is currently confirmed."),
        "recurrence_hint": _operator_recurrence_hint(state),
        "takeaway": _operator_takeaway(state),
        "capacity_headroom_summary": _safe_label(
            playback_quality.get("headroom_summary"),
            issue_metadata.get("capacity_headroom_summary") or "Current telemetry suggests the system is comfortably handling playback.",
        ),
        "diagnosis_label": _safe_label(
            playback_quality.get("manager_diagnosis_label"),
            _safe_label(diagnosis_presentation.get("primary_diagnosis_label"), "unknown"),
        ),
        "delivery_confidence_label": _safe_label(playback_quality.get("delivery_confidence_label"), "High"),
        "delivery_confidence_summary": _safe_label(playback_quality.get("delivery_confidence_summary"), ""),
        "recurrence_risk_label": _safe_label(playback_quality.get("recurrence_risk_label"), "Low"),
        "recurrence_summary": _safe_label(playback_quality.get("recurrence_summary"), ""),
        "contributing_label": ", ".join(
            _safe_label(item)
            for item in _as_list(diagnosis_presentation.get("operator_contributing_factors"))
            if _safe_label(item)
        ),
        "confirmed_text": (
            "Buffering is confirmed for: {}".format(
                ", ".join(
                    _safe_label(item)
                    for item in _as_list(structured_diagnosis.get("buffering_sessions"))
                    if _safe_label(item)
                )
            )
            if structured_diagnosis.get("buffering_confirmed")
            else "No active buffering is currently confirmed by telemetry."
        ),
        "state_change_label": _safe_label(state_change.get("change_type"), "unknown"),
        "ruled_out_labels": [
            _safe_label(item).replace("_", " ")
            for item in _as_list(structured_diagnosis.get("ruled_out"))
            if _safe_label(item)
        ],
        "next_checks": [_safe_label(item) for item in _as_list(action_plan.get("next_checks")) if _safe_label(item)],
        "recommended_actions": [
            _safe_label(item) for item in _as_list(action_plan.get("recommended_actions")) if _safe_label(item)
        ],
        "events_last_24h": _safe_label(history_summary.get("events_last_24h"), "0"),
        "warning_or_higher_last_24h": _safe_label(history_summary.get("warning_or_higher_last_24h"), "0"),
        "top_diagnosis_last_24h": _safe_label(history_summary.get("top_diagnosis_last_24h"), "none"),
        "top_affected_client_last_24h": _safe_label(history_summary.get("top_affected_client_last_24h"), "none"),
        "observational_mode": observational_mode,
        "system_mechanics_summary": _operator_transcode_mechanics(state),
        "resource_pressure_analysis": _operator_resource_analysis(state),
        "system_behavior": _operator_system_behavior(state),
        "constraints_ruled_out_summary": _operator_constraints_ruled_out(state),
        "session_level_reasoning": session_reasoning,
        "session_level_reasoning_empty_label": "No cross-session divergence observed.",
        "failure_path_analysis": failure_paths,
        "failure_path_empty_label": "No immediate failure risks under current load.",
        "contextual_verification_steps": verification_steps,
    }


def _snapshot_age_seconds(snapshot: Optional[Dict[str, Any]]) -> Optional[float]:
    if not snapshot:
        return None
    generated_at = snapshot.get("generated_at")
    if not generated_at:
        return None
    try:
        generated_time = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except Exception:
        return None
    return (datetime.now(timezone.utc) - generated_time).total_seconds()


def _cache_snapshot(snapshot: Dict[str, Any]) -> None:
    if snapshot.get("ok"):
        _STATE_SNAPSHOT_CACHE["generated_at"] = snapshot.get("generated_at")
        _STATE_SNAPSHOT_CACHE["snapshot"] = snapshot


def _get_fresh_or_cached_snapshot(prefer_cache: bool = False) -> Dict[str, Any]:
    cached_snapshot = _STATE_SNAPSHOT_CACHE.get("snapshot")
    cached_age = _snapshot_age_seconds(cached_snapshot)

    if prefer_cache and cached_snapshot and cached_snapshot.get("ok") and cached_age is not None and cached_age <= ASK_STATE_CACHE_SECONDS:
        cached_copy = dict(cached_snapshot)
        cached_copy["snapshot_source"] = "cached"
        cached_copy["snapshot_age_seconds"] = round(cached_age, 1)
        return cached_copy

    snapshot = _safe_build_state()
    snapshot["snapshot_source"] = "fresh"
    snapshot["snapshot_age_seconds"] = 0.0
    if snapshot.get("ok"):
        _cache_snapshot(snapshot)
    return snapshot


def _selected_trend_range(range_key: str) -> Dict[str, str]:
    for option in TREND_TIME_RANGES:
        if option["key"] == range_key:
            return option
    for option in TREND_TIME_RANGES:
        if option["key"] == GRAFANA_DEFAULT_RANGE:
            return option
    return TREND_TIME_RANGES[1]


def _build_grafana_panel_url(panel_id: int, from_expr: str) -> str:
    if not (GRAFANA_BASE_URL and GRAFANA_DASHBOARD_UID and GRAFANA_DASHBOARD_SLUG):
        return ""

    return "{}/d-solo/{}/{}?{}".format(
        GRAFANA_BASE_URL,
        GRAFANA_DASHBOARD_UID,
        GRAFANA_DASHBOARD_SLUG,
        urlencode(
            {
                "panelId": panel_id,
                "from": from_expr,
                "to": "now",
                "theme": "light",
            }
        ),
    )


def _build_public_dashboard_url(from_expr: str) -> str:
    if not GRAFANA_PUBLIC_DASHBOARD_URL:
        return ""

    separator = "&" if "?" in GRAFANA_PUBLIC_DASHBOARD_URL else "?"
    return "{}{}{}".format(
        GRAFANA_PUBLIC_DASHBOARD_URL,
        separator,
        urlencode(
            {
                "from": from_expr,
                "to": "now",
            }
        ),
    )


def _build_trends_context(request: Request, range_key: str) -> Dict[str, Any]:
    selected_range = _selected_trend_range(range_key)

    return {
        **_base_page_context(request, "Trends"),
        "trend_description": "Observability charts from your public Grafana dashboard.",
        "trend_dashboard_url": _build_public_dashboard_url(selected_range["from"]),
        "grafana_configured": bool(GRAFANA_PUBLIC_DASHBOARD_URL),
        "grafana_base_url": GRAFANA_PUBLIC_DASHBOARD_URL,
    }


def _get_or_create_ask_session_id(request: Request) -> str:
    existing = request.cookies.get(ASK_SESSION_COOKIE)
    if existing:
        return existing
    return uuid4().hex


def _get_ask_history(session_id: str) -> List[Dict[str, str]]:
    return list(_ASK_CONVERSATIONS.get(session_id, []))


def _append_ask_turn(session_id: str, question: str, answer: str, response_mode: str, intent: str) -> None:
    history = _ASK_CONVERSATIONS.get(session_id, [])
    history.append(
        {
            "user_question": question,
            "assistant_answer": answer,
            "response_mode": response_mode,
            "intent": intent,
        }
    )
    _ASK_CONVERSATIONS[session_id] = history[-MAX_ASK_MEMORY_TURNS:]


def _clear_ask_history(session_id: str) -> None:
    _ASK_CONVERSATIONS.pop(session_id, None)


def _normalize_page_context(page_context: str) -> str:
    normalized = (page_context or "").strip().lower()
    if normalized in PAGE_CONFIG:
        return normalized
    return "home"


def _page_context_from_path(path: str) -> str:
    for key, config in PAGE_CONFIG.items():
        if config["path"] == path:
            return key
    return "home"


def _page_config(page_context: str) -> Dict[str, str]:
    return PAGE_CONFIG[_normalize_page_context(page_context)]


def _resolve_ask_mode(page_context: str, requested_mode: str) -> str:
    normalized_page_context = _normalize_page_context(page_context)
    page_config = _page_config(normalized_page_context)
    locked_mode = str(page_config.get("locked_ask_mode", "")).strip()
    if locked_mode in {"operator", "manager"}:
        return locked_mode
    normalized_requested_mode = (requested_mode or "").strip().lower()
    if normalized_requested_mode in {"operator", "manager"}:
        return normalized_requested_mode
    return str(page_config.get("response_mode", "operator"))


def build_web_context(
    response_mode: str = "operator",
    snapshot: Optional[Dict[str, Any]] = None,
    page_context: str = "home",
    ask_question: str = "",
    ask_answer: Optional[str] = None,
    ask_error: Optional[str] = None,
    ask_mode: str = "operator",
    ask_intent: Optional[str] = None,
    ask_follow_ups: Optional[List[str]] = None,
    ask_history: Optional[List[Dict[str, str]]] = None,
    ask_source: Optional[str] = None,
    ask_section: Optional[str] = None,
    ask_prompt_key: Optional[str] = None,
    scroll_target: Optional[str] = None,
) -> Dict[str, Any]:
    page_context = _normalize_page_context(page_context)
    page_config = _page_config(page_context)
    effective_ask_mode = _resolve_ask_mode(page_context, ask_mode)
    quick_questions = page_config.get("quick_questions_by_mode", {}).get(effective_ask_mode, page_config["quick_questions"])
    snapshot = snapshot or _get_fresh_or_cached_snapshot(prefer_cache=False)
    state = snapshot["state"] or {}
    current_alerts = state.get("alerts", [])
    recent_history = load_recent_history(limit=50)
    recent_alert_history = load_recent_alert_history(limit=50)
    display_history = [build_history_display_event(_as_dict(event)) for event in reversed(recent_history)]
    display_sessions = _display_sessions(_as_list(_as_dict(state.get("plex")).get("sessions")))
    display_current_alerts = _display_alerts(_as_list(current_alerts))
    display_recent_alerts = _display_alerts(list(reversed(recent_alert_history)))
    dashboard_view = _display_dashboard_view(state)
    manager_view = _display_manager_view(state)
    operator_view = _display_operator_view(state)

    return {
        "ok": snapshot["ok"],
        "generated_at": snapshot["generated_at"],
        "generated_at_label": _format_timestamp(snapshot["generated_at"]),
        "error": snapshot["error"],
        "snapshot_source": snapshot.get("snapshot_source", "fresh"),
        "snapshot_age_seconds": snapshot.get("snapshot_age_seconds"),
        "response_mode": response_mode,
        "page_context": page_context,
        "state": state,
        "plex": state.get("plex", {}),
        "system": state.get("system", {}),
        "structured_diagnosis": state.get("structured_diagnosis", {}),
        "diagnosis_presentation": state.get("diagnosis_presentation", {}),
        "issue_metadata": state.get("issue_metadata", {}),
        "action_plan": state.get("action_plan", {}),
        "history_summary": state.get("history_summary", {}),
        "state_change": state.get("state_change", {}),
        "alerts": current_alerts,
        "manager_summary": state.get("manager_summary", {}),
        "dashboard_view": dashboard_view,
        "manager_view": manager_view,
        "operator_view": operator_view,
        "recent_history": display_history,
        "recent_alert_history": display_recent_alerts,
        "alerts_display": display_current_alerts,
        "sessions_display": display_sessions,
        "session_rows": display_sessions,
        "sessions": state.get("plex", {}).get("sessions", []),
        "ask_question": ask_question,
        "ask_answer": ask_answer,
        "ask_error": ask_error,
        "ask_mode": effective_ask_mode,
        "ask_mode_locked": not bool(page_config["allow_mode_toggle"]),
        "ask_mode_label": page_config["locked_mode_label"],
        "ask_helper_text": page_config["ask_helper_text"],
        "ask_placeholder": page_config["ask_placeholder"],
        "ask_intent": ask_intent,
        "ask_follow_ups": ask_follow_ups or [],
        "ask_source": ask_source or "",
        "ask_section": ask_section or "",
        "ask_prompt_key": ask_prompt_key or "",
        "scroll_target": scroll_target or "",
        "ask_history": ask_history or [],
        "ask_memory_turns": len(ask_history or []),
        "ask_answered_at": _format_timestamp(_now_iso()) if ask_answer or ask_error else None,
        "ask_answer_source": snapshot.get("snapshot_source") if ask_answer else None,
        "quick_questions": quick_questions,
    }


def render_page(
    request: Request,
    template_name: str,
    page_title: str,
    response_mode: str = "operator",
    snapshot: Optional[Dict[str, Any]] = None,
    page_context: Optional[str] = None,
    ask_question: str = "",
    ask_answer: Optional[str] = None,
    ask_error: Optional[str] = None,
    ask_mode: str = "operator",
    ask_intent: Optional[str] = None,
    ask_follow_ups: Optional[List[str]] = None,
    ask_history: Optional[List[Dict[str, str]]] = None,
    ask_source: Optional[str] = None,
    ask_section: Optional[str] = None,
    ask_prompt_key: Optional[str] = None,
    scroll_target: Optional[str] = None,
) -> HTMLResponse:
    session_id = _get_or_create_ask_session_id(request)
    normalized_page_context = _normalize_page_context(page_context or _page_context_from_path(request.url.path))
    page_config = _page_config(normalized_page_context)
    context = build_web_context(
        response_mode=response_mode,
        snapshot=snapshot,
        page_context=normalized_page_context,
        ask_question=ask_question,
        ask_answer=ask_answer,
        ask_error=ask_error,
        ask_mode=ask_mode,
        ask_intent=ask_intent,
        ask_follow_ups=ask_follow_ups,
        ask_history=ask_history if ask_history is not None else _get_ask_history(session_id),
        ask_source=ask_source,
        ask_section=ask_section,
        ask_prompt_key=ask_prompt_key,
        scroll_target=scroll_target,
    )
    context.update(
        {
            "request": request,
            "page_title": page_title,
            "active_path": page_config["path"],
            "static_asset_version": STATIC_ASSET_VERSION,
            "page_context": normalized_page_context,
            "scroll_target": scroll_target or "",
        }
    )
    response = templates.TemplateResponse(
        request=request,
        name=template_name,
        context=context,
    )
    if request.cookies.get(ASK_SESSION_COOKIE) != session_id:
        response.set_cookie(ASK_SESSION_COOKIE, session_id, max_age=60 * 60 * 24 * 30, samesite="lax")
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    snapshot = _get_fresh_or_cached_snapshot(prefer_cache=False)
    return render_page(request, "dashboard.html", "Dashboard", response_mode="operator", snapshot=snapshot, page_context="home")


@app.post("/ask", response_class=HTMLResponse)
async def ask(request: Request) -> HTMLResponse:
    form = await request.form()
    question = str(form.get("question", "")).strip()
    page_context = _normalize_page_context(str(form.get("page_context", "home")))
    page_config = _page_config(page_context)
    ask_source = str(form.get("ask_source", "")).strip()
    ask_section = str(form.get("ask_section", "")).strip()
    ask_prompt_key = str(form.get("ask_prompt_key", "")).strip()
    ask_mode = _resolve_ask_mode(page_context, str(form.get("ask_mode", "operator")).strip() or "operator")
    session_id = _get_or_create_ask_session_id(request)
    ask_history = _get_ask_history(session_id)

    snapshot = _get_fresh_or_cached_snapshot(prefer_cache=True)
    ask_answer = None
    ask_error = None
    ask_intent = None
    ask_follow_ups: List[str] = []

    if not question:
        ask_error = "Enter a question about the current system state."
    elif not snapshot["ok"]:
        ask_error = "The current system state could not be loaded, so no grounded answer is available right now."
    else:
        try:
            ask_result = answer_question_result_from_state(
                question,
                snapshot["state"],
                response_mode=ask_mode,
                context_mode="web_ask",
                page_context=page_context,
                conversation_history=ask_history,
                ask_source=ask_source,
                ask_section=ask_section,
                ask_prompt_key=ask_prompt_key,
            )
            ask_answer = ask_result["answer"]
            ask_intent = ask_result.get("intent")
            ask_follow_ups = ask_result.get("follow_up_questions", [])
            if ask_answer:
                _append_ask_turn(session_id, question, ask_answer, ask_mode, ask_intent or "status")
                ask_history = _get_ask_history(session_id)
        except Exception:
            ask_error = "Plex Assistant could not answer that question from the current snapshot right now."

    return render_page(
        request,
        page_config["template"],
        page_config["title"],
        response_mode=page_config["response_mode"],
        snapshot=snapshot,
        page_context=page_context,
        ask_question=question,
        ask_answer=ask_answer,
        ask_error=ask_error,
        ask_mode=ask_mode,
        ask_intent=ask_intent,
        ask_follow_ups=ask_follow_ups,
        ask_history=ask_history,
        ask_source=ask_source,
        ask_section=ask_section,
        ask_prompt_key=ask_prompt_key,
        scroll_target="ask-plex",
    )


@app.post("/ask/clear", response_class=HTMLResponse)
async def clear_ask_history(request: Request) -> HTMLResponse:
    form = await request.form()
    page_context = _normalize_page_context(str(form.get("page_context", "home")))
    page_config = _page_config(page_context)
    session_id = _get_or_create_ask_session_id(request)
    _clear_ask_history(session_id)
    snapshot = _get_fresh_or_cached_snapshot(prefer_cache=True)
    return render_page(
        request,
        page_config["template"],
        page_config["title"],
        response_mode=page_config["response_mode"],
        snapshot=snapshot,
        page_context=page_context,
        ask_error="Conversation cleared. New questions will start fresh from the current state.",
        ask_mode=_resolve_ask_mode(page_context, page_config["response_mode"]),
        ask_history=[],
        scroll_target="ask-plex",
    )


@app.get("/operator", response_class=HTMLResponse)
def operator_view(request: Request) -> HTMLResponse:
    return render_page(request, "operator.html", "Operator View", response_mode="operator", page_context="operator")


@app.get("/manager", response_class=HTMLResponse)
def manager_view(request: Request) -> HTMLResponse:
    return render_page(request, "manager.html", "Manager View", response_mode="manager", page_context="manager")


@app.get("/history", response_class=HTMLResponse)
def history_view(request: Request) -> HTMLResponse:
    return render_page(request, "history.html", "History", response_mode="operator", page_context="history")


@app.get("/alerts", response_class=HTMLResponse)
def alerts_view(request: Request) -> HTMLResponse:
    return render_page(request, "alerts.html", "Alerts", response_mode="operator", page_context="alerts")


@app.get("/trends", response_class=HTMLResponse)
def trends_view(request: Request, range: str = GRAFANA_DEFAULT_RANGE) -> HTMLResponse:
    context = _build_trends_context(request, range)
    return templates.TemplateResponse(
        request=request,
        name="trends.html",
        context=context,
    )


@app.get("/api/state")
def api_state() -> JSONResponse:
    snapshot = _get_fresh_or_cached_snapshot(prefer_cache=False)
    if not snapshot["ok"]:
        return JSONResponse(
            {
                "status": "error",
                "generated_at": snapshot["generated_at"],
                "error": snapshot["error"],
            },
            status_code=500,
        )

    return JSONResponse(
        {
            "status": "ok",
            "generated_at": snapshot["generated_at"],
            "state": snapshot["state"],
        }
    )


@app.get("/api/health")
def api_health() -> JSONResponse:
    snapshot = _get_fresh_or_cached_snapshot(prefer_cache=False)
    if not snapshot["ok"]:
        return JSONResponse(
            {
                "status": "error",
                "generated_at": snapshot["generated_at"],
                "service_health": "degraded",
                "error": snapshot["error"],
            },
            status_code=500,
        )

    manager_summary = snapshot["state"].get("manager_summary", {})
    return JSONResponse(
        {
            "status": "ok",
            "generated_at": snapshot["generated_at"],
            "service_health": manager_summary.get("service_health", "unknown"),
            "severity": manager_summary.get("severity", "info"),
            "diagnosis": manager_summary.get("current_diagnosis", "unknown"),
        }
    )


@app.get("/api/history")
def api_history() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "generated_at": _now_iso(),
            "history": list(reversed(load_recent_history(limit=100))),
        }
    )


@app.get("/api/alerts")
def api_alerts() -> JSONResponse:
    snapshot = _get_fresh_or_cached_snapshot(prefer_cache=True)
    return JSONResponse(
        {
            "status": "ok" if snapshot["ok"] else "error",
            "generated_at": snapshot["generated_at"],
            "current_alerts": (snapshot["state"] or {}).get("alerts", []),
            "recent_alerts": list(reversed(load_recent_alert_history(limit=100))),
            "error": snapshot["error"],
        }
    )


@app.post("/api/ask")
async def api_ask(request: Request) -> JSONResponse:
    payload = await request.json()
    question = str(payload.get("question", "")).strip()
    page_context = _normalize_page_context(str(payload.get("page_context", "home")))
    response_mode = _resolve_ask_mode(page_context, str(payload.get("response_mode", "operator")).strip() or "operator")
    ask_source = str(payload.get("ask_source", "")).strip()
    ask_section = str(payload.get("ask_section", "")).strip()
    ask_prompt_key = str(payload.get("ask_prompt_key", "")).strip()

    snapshot = _safe_build_state()
    if not question:
        return JSONResponse({"status": "error", "error": "Question is required."}, status_code=400)
    if not snapshot["ok"]:
        return JSONResponse(
            {
                "status": "error",
                "generated_at": snapshot["generated_at"],
                "error": "Current state is unavailable.",
                "details": snapshot["error"],
            },
            status_code=500,
        )

    try:
        ask_result = answer_question_result_from_state(
            question,
            snapshot["state"],
            response_mode=response_mode,
            context_mode="web_ask",
            page_context=page_context,
            ask_source=ask_source,
            ask_section=ask_section,
            ask_prompt_key=ask_prompt_key,
        )
    except Exception:
        return JSONResponse(
            {
                "status": "error",
                "generated_at": snapshot["generated_at"],
                "error": "Plex Assistant could not answer that question from the current snapshot.",
            },
            status_code=500,
        )

    return JSONResponse(
        {
            "status": "ok",
            "generated_at": snapshot["generated_at"],
            "response_mode": response_mode,
            "page_context": page_context,
            "ask_source": ask_source,
            "ask_section": ask_section,
            "ask_prompt_key": ask_prompt_key,
            "snapshot_source": snapshot.get("snapshot_source", "fresh"),
            "question": question,
            "answer": ask_result["answer"],
            "intent": ask_result.get("intent"),
            "follow_up_questions": ask_result.get("follow_up_questions", []),
        }
    )
