print("🔥 NEW NEW BUILD DEPLOYED 🔥")

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from alerts import load_recent_alert_history
from app import answer_question_from_state, build_state
from config import ASK_STATE_CACHE_SECONDS, DOTENV_LOADED, PLEX_BASE_URL, PLEX_TOKEN, TAUTULLI_BASE_URL
from history import load_recent_history
from summaries import build_history_display_event


BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

app = FastAPI(title="Plex Assistant UI")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
logger = logging.getLogger("plex_assistant.web")

_STATE_SNAPSHOT_CACHE: Dict[str, Any] = {
    "generated_at": None,
    "snapshot": None,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_timestamp(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return value


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


def _classify_request_exception(exc: Exception) -> str:
    if isinstance(exc, requests.ConnectTimeout):
        return "connect timeout"
    if isinstance(exc, requests.ReadTimeout):
        return "read timeout"
    if isinstance(exc, requests.HTTPError):
        status_code = getattr(exc.response, "status_code", "unknown")
        return "http {}".format(status_code)
    if isinstance(exc, requests.ConnectionError):
        detail = str(exc).lower()
        if (
            "name or service not known" in detail
            or "temporary failure in name resolution" in detail
            or "nodename nor servname provided" in detail
            or "failed to resolve" in detail
        ):
            return "dns failure"
        if "connection refused" in detail:
            return "connection refused"
        return "connection error"
    return exc.__class__.__name__.lower()


def _probe_url(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 5) -> Dict[str, Any]:
    try:
        response = requests.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return {
            "ok": True,
            "target": url,
            "status_code": response.status_code,
            "error_type": None,
            "detail": None,
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "target": url,
            "status_code": getattr(getattr(exc, "response", None), "status_code", None),
            "error_type": _classify_request_exception(exc),
            "detail": str(exc),
        }


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
                "alert_type": _safe_label(alert.get("alert_type"), "unknown"),
                "title": _safe_label(alert.get("title"), "Alert"),
                "message": _safe_label(alert.get("message"), ""),
                "triggered_at": _safe_label(alert.get("triggered_at"), ""),
                "severity_label": severity_label,
                "severity_class": "severity-{}".format(severity_label),
                "sessions_label": ", ".join(_safe_label(item) for item in _as_list(alert.get("affected_sessions")) if _safe_label(item)) or "none",
                "clients_label": ", ".join(_safe_label(item) for item in _as_list(alert.get("affected_clients")) if _safe_label(item)) or "none",
                "cooldown_label": _safe_label(alert.get("cooldown_key"), "n/a"),
            }
        )
    return rows


def _display_dashboard_view(state: Dict[str, Any]) -> Dict[str, Any]:
    manager_summary = _as_dict(state.get("manager_summary"))
    diagnosis_presentation = _as_dict(state.get("diagnosis_presentation"))
    plex = _as_dict(state.get("plex"))
    alert_rows = _display_alerts(_as_list(state.get("alerts")))[:3]
    session_rows = _display_sessions(_as_list(plex.get("sessions")))

    severity_label = _safe_label(diagnosis_presentation.get("severity"), "info")
    return {
        "service_health_label": _safe_label(manager_summary.get("service_health"), "unknown"),
        "service_health_tone": _safe_label(manager_summary.get("service_health_tone"), "healthy"),
        "severity_label": severity_label,
        "severity_class": "severity-{}".format(severity_label),
        "scope_label": _safe_label(diagnosis_presentation.get("scope"), "unknown"),
        "confidence_label": _safe_label(diagnosis_presentation.get("confidence"), "low"),
        "diagnosis_label": _safe_label(diagnosis_presentation.get("primary_diagnosis_label"), "unknown"),
        "supporting_text": _safe_label(diagnosis_presentation.get("supporting_text"), ""),
        "contributing_factors": [
            _safe_label(item) for item in _as_list(diagnosis_presentation.get("dashboard_contributing_factors"))[:2] if _safe_label(item)
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
        "alert_rows": alert_rows,
        "session_rows": session_rows,
    }


def _display_manager_view(state: Dict[str, Any]) -> Dict[str, Any]:
    manager_summary = _as_dict(state.get("manager_summary"))
    return {
        "service_health": _safe_label(manager_summary.get("service_health"), "unknown"),
        "service_health_tone": _safe_label(manager_summary.get("service_health_tone"), "healthy"),
        "severity_label": _safe_label(manager_summary.get("severity"), "info"),
        "scope_label": _safe_label(manager_summary.get("issue_scope"), "unknown"),
        "impact_summary": _safe_label(manager_summary.get("impact_summary"), "No summary available."),
        "diagnosis_label": _safe_label(manager_summary.get("current_diagnosis_label"), "unknown"),
        "contributing_summary": _safe_label(manager_summary.get("contributing_summary"), ""),
        "insight": _safe_label(manager_summary.get("insight"), ""),
        "recommended_action": _safe_label(manager_summary.get("recommended_action"), "No immediate action needed."),
        "escalation_label": "Yes" if manager_summary.get("escalation_needed") else "No",
        "trend_summary": _safe_label(manager_summary.get("trend_summary"), "No trend summary available."),
    }


def _display_operator_view(state: Dict[str, Any]) -> Dict[str, Any]:
    diagnosis_presentation = _as_dict(state.get("diagnosis_presentation"))
    structured_diagnosis = _as_dict(state.get("structured_diagnosis"))
    action_plan = _as_dict(state.get("action_plan"))
    history_summary = _as_dict(state.get("history_summary"))
    state_change = _as_dict(state.get("state_change"))

    severity_label = _safe_label(diagnosis_presentation.get("severity"), "info")
    return {
        "severity_label": severity_label,
        "severity_class": "severity-{}".format(severity_label),
        "scope_label": _safe_label(diagnosis_presentation.get("scope"), "unknown"),
        "confidence_label": _safe_label(diagnosis_presentation.get("confidence"), "low"),
        "diagnosis_label": _safe_label(diagnosis_presentation.get("primary_diagnosis_label"), "unknown"),
        "contributing_label": ", ".join(
            _safe_label(item) for item in _as_list(diagnosis_presentation.get("operator_contributing_factors")) if _safe_label(item)
        ),
        "confirmed_text": (
            "Buffering is confirmed for: {}".format(
                ", ".join(_safe_label(item) for item in _as_list(structured_diagnosis.get("buffering_sessions")) if _safe_label(item))
            )
            if structured_diagnosis.get("buffering_confirmed")
            else "No active buffering is currently confirmed by telemetry."
        ),
        "state_change_label": _safe_label(state_change.get("change_type"), "unknown"),
        "ruled_out_labels": [_safe_label(item).replace("_", " ") for item in _as_list(structured_diagnosis.get("ruled_out")) if _safe_label(item)],
        "next_checks": [_safe_label(item) for item in _as_list(action_plan.get("next_checks")) if _safe_label(item)],
        "recommended_actions": [_safe_label(item) for item in _as_list(action_plan.get("recommended_actions")) if _safe_label(item)],
        "events_last_24h": _safe_label(history_summary.get("events_last_24h"), "0"),
        "warning_or_higher_last_24h": _safe_label(history_summary.get("warning_or_higher_last_24h"), "0"),
        "top_diagnosis_last_24h": _safe_label(history_summary.get("top_diagnosis_last_24h"), "none"),
        "top_affected_client_last_24h": _safe_label(history_summary.get("top_affected_client_last_24h"), "none"),
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


def build_web_context(
    response_mode: str = "operator",
    snapshot: Optional[Dict[str, Any]] = None,
    ask_question: str = "",
    ask_answer: Optional[str] = None,
    ask_error: Optional[str] = None,
    ask_mode: str = "operator",
) -> Dict[str, Any]:
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
        "ask_mode": ask_mode,
        "ask_answered_at": _format_timestamp(_now_iso()) if ask_answer or ask_error else None,
        "ask_answer_source": snapshot.get("snapshot_source") if ask_answer else None,
        "quick_questions": [
            "What's happening right now?",
            "Why is this buffering?",
            "What should I do next?",
            "Is this a server issue?",
            "Should I worry about this?",
        ],
    }


def _log_dashboard_context(context: Dict[str, Any]) -> None:
    dashboard_view = _as_dict(context.get("dashboard_view"))
    session_rows = _as_list(context.get("session_rows"))
    alert_rows = _as_list(dashboard_view.get("alert_rows"))
    recent_history = _as_list(context.get("recent_history"))
    manager_view = context.get("manager_view")
    operator_view = context.get("operator_view")
    first_session = _as_dict(session_rows[0]) if session_rows else {}
    first_alert = _as_dict(alert_rows[0]) if alert_rows else {}

    logger.info(
        "DASHBOARD DEBUG template=%s dashboard_view=%s session_rows=%s alert_rows=%s manager_view=%s operator_view=%s",
        "dashboard.html",
        type(dashboard_view).__name__,
        type(session_rows).__name__,
        type(alert_rows).__name__,
        type(manager_view).__name__,
        type(operator_view).__name__,
    )
    logger.info(
        "DASHBOARD DEBUG lengths session_rows=%s alert_rows=%s recent_history=%s quick_questions=%s",
        len(session_rows),
        len(alert_rows),
        len(recent_history),
        len(_as_list(context.get("quick_questions"))),
    )
    logger.info(
        "DASHBOARD DEBUG first_session type=%s keys=%s",
        type(first_session).__name__,
        sorted(first_session.keys()),
    )
    logger.info(
        "DASHBOARD DEBUG first_alert type=%s keys=%s",
        type(first_alert).__name__,
        sorted(first_alert.keys()),
    )
    logger.info(
        "DASHBOARD DEBUG context field types ask_answer=%s ask_error=%s ask_question=%s generated_at_label=%s active_path=%s",
        type(context.get("ask_answer")).__name__,
        type(context.get("ask_error")).__name__,
        type(context.get("ask_question")).__name__,
        type(context.get("generated_at_label")).__name__,
        type(context.get("active_path")).__name__,
    )
    logger.info(
        "Dashboard context types: dashboard_view=%s alert_rows=%s session_rows=%s quick_questions=%s ask_answer=%s ask_error=%s",
        type(dashboard_view).__name__,
        type(dashboard_view.get("alert_rows")).__name__,
        type(dashboard_view.get("session_rows")).__name__,
        type(context.get("quick_questions")).__name__,
        type(context.get("ask_answer")).__name__,
        type(context.get("ask_error")).__name__,
    )
    logger.info(
        "Dashboard context lengths: alert_rows=%s session_rows=%s quick_questions=%s",
        len(_as_list(dashboard_view.get("alert_rows"))),
        len(_as_list(dashboard_view.get("session_rows"))),
        len(_as_list(context.get("quick_questions"))),
    )


def render_page(
    request: Request,
    template_name: str,
    page_title: str,
    response_mode: str = "operator",
    snapshot: Optional[Dict[str, Any]] = None,
    ask_question: str = "",
    ask_answer: Optional[str] = None,
    ask_error: Optional[str] = None,
    ask_mode: str = "operator",
) -> HTMLResponse:
    context = build_web_context(
        response_mode=response_mode,
        snapshot=snapshot,
        ask_question=ask_question,
        ask_answer=ask_answer,
        ask_error=ask_error,
        ask_mode=ask_mode,
    )
    context.update(
        {
            "request": request,
            "page_title": page_title,
            "active_path": request.url.path,
        }
    )
    if template_name == "dashboard.html":
        _log_dashboard_context(context)
    logger.info(
        "Rendering template=%s ok=%s snapshot_source=%s context_keys=%s",
        template_name,
        context.get("ok"),
        context.get("snapshot_source"),
        sorted(context.keys()),
    )
    try:
        return templates.TemplateResponse(
            request=request,
            name=template_name,
            context=context,
        )
    except Exception:
        if template_name == "dashboard.html":
            logger.error("DASHBOARD RENDER FAILED")
            logger.error("DASHBOARD RENDER FAILED context_keys=%s", sorted(context.keys()))
            logger.error(
                "DASHBOARD RENDER FAILED field_types dashboard_view=%s session_rows=%s alert_rows=%s manager_view=%s operator_view=%s quick_questions=%s ask_question=%s ask_answer=%s ask_error=%s generated_at_label=%s active_path=%s",
                type(context.get("dashboard_view")).__name__,
                type(context.get("session_rows")).__name__,
                type(_as_dict(context.get("dashboard_view")).get("alert_rows")).__name__,
                type(context.get("manager_view")).__name__,
                type(context.get("operator_view")).__name__,
                type(context.get("quick_questions")).__name__,
                type(context.get("ask_question")).__name__,
                type(context.get("ask_answer")).__name__,
                type(context.get("ask_error")).__name__,
                type(context.get("generated_at_label")).__name__,
                type(context.get("active_path")).__name__,
            )
            logger.error(
                "DASHBOARD RENDER FAILED lengths session_rows=%s alert_rows=%s recent_history=%s",
                len(_as_list(context.get("session_rows"))),
                len(_as_list(_as_dict(context.get("dashboard_view")).get("alert_rows"))),
                len(_as_list(context.get("recent_history"))),
            )
            first_session = _as_dict(_as_list(context.get("session_rows"))[0]) if _as_list(context.get("session_rows")) else {}
            first_alert = _as_dict(_as_list(_as_dict(context.get("dashboard_view")).get("alert_rows"))[0]) if _as_list(_as_dict(context.get("dashboard_view")).get("alert_rows")) else {}
            logger.error(
                "DASHBOARD RENDER FAILED first_session type=%s keys=%s",
                type(first_session).__name__,
                sorted(first_session.keys()),
            )
            logger.error(
                "DASHBOARD RENDER FAILED first_alert type=%s keys=%s",
                type(first_alert).__name__,
                sorted(first_alert.keys()),
            )
        logger.exception(
            "Template render failed for %s with dashboard_view=%s manager_view=%s operator_view=%s recent_history=%s alerts_display=%s sessions_display=%s",
            template_name,
            type(context.get("dashboard_view")).__name__,
            type(context.get("manager_view")).__name__,
            type(context.get("operator_view")).__name__,
            type(context.get("recent_history")).__name__,
            type(context.get("alerts_display")).__name__,
            type(context.get("sessions_display")).__name__,
        )
        raise


@app.on_event("startup")
def log_startup_paths() -> None:
    logger.warning(
        "Plex Assistant startup cwd=%s template_dir=%s static_dir=%s",
        Path.cwd(),
        TEMPLATE_DIR,
        STATIC_DIR,
    )
    logger.warning(
        "Plex Assistant upstreams dotenv_loaded=%s PLEX_BASE_URL=%s TAUTULLI_BASE_URL=%s",
        DOTENV_LOADED,
        PLEX_BASE_URL,
        TAUTULLI_BASE_URL,
    )


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    snapshot = _get_fresh_or_cached_snapshot(prefer_cache=False)
    return render_page(request, "dashboard.html", "Dashboard", response_mode="operator", snapshot=snapshot)


@app.get("/debug-dashboard-min", response_class=HTMLResponse)
def debug_dashboard_min(request: Request) -> HTMLResponse:
    snapshot = _get_fresh_or_cached_snapshot(prefer_cache=False)
    return render_page(request, "dashboard_min.html", "Dashboard Debug Min", response_mode="operator", snapshot=snapshot)


@app.get("/debug-dashboard-cards", response_class=HTMLResponse)
def debug_dashboard_cards(request: Request) -> HTMLResponse:
    snapshot = _get_fresh_or_cached_snapshot(prefer_cache=False)
    return render_page(request, "dashboard_cards.html", "Dashboard Debug Cards", response_mode="operator", snapshot=snapshot)


@app.get("/debug-dashboard-ask", response_class=HTMLResponse)
def debug_dashboard_ask(request: Request) -> HTMLResponse:
    snapshot = _get_fresh_or_cached_snapshot(prefer_cache=False)
    return render_page(request, "dashboard_ask.html", "Dashboard Debug Ask", response_mode="operator", snapshot=snapshot)


@app.get("/debug-dashboard-sessions", response_class=HTMLResponse)
def debug_dashboard_sessions(request: Request) -> HTMLResponse:
    snapshot = _get_fresh_or_cached_snapshot(prefer_cache=False)
    return render_page(request, "dashboard_sessions.html", "Dashboard Debug Sessions", response_mode="operator", snapshot=snapshot)


@app.get("/debug-dashboard-alerts", response_class=HTMLResponse)
def debug_dashboard_alerts(request: Request) -> HTMLResponse:
    snapshot = _get_fresh_or_cached_snapshot(prefer_cache=False)
    return render_page(request, "dashboard_alerts.html", "Dashboard Debug Alerts", response_mode="operator", snapshot=snapshot)


@app.post("/ask", response_class=HTMLResponse)
async def ask(request: Request) -> HTMLResponse:
    question = ""
    ask_mode = "operator"
    try:
        form = await request.form()
        question = str(form.get("question", "")).strip()
        ask_mode = str(form.get("ask_mode", "operator")).strip() or "operator"
    except AssertionError:
        snapshot = _get_fresh_or_cached_snapshot(prefer_cache=True)
        return render_page(
            request,
            "dashboard.html",
            "Dashboard",
            response_mode="operator",
            snapshot=snapshot,
            ask_question=question,
            ask_answer=None,
            ask_error="Ask Plex Assistant needs form parsing support. Install the python-multipart dependency in the container and redeploy.",
            ask_mode=ask_mode,
        )

    if ask_mode not in {"operator", "manager"}:
        ask_mode = "operator"

    snapshot = _get_fresh_or_cached_snapshot(prefer_cache=True)
    ask_answer = None
    ask_error = None

    if not question:
        ask_error = "Enter a question about the current system state."
    elif not snapshot["ok"]:
        ask_error = "The current system state could not be loaded, so no grounded answer is available right now."
    else:
        try:
            ask_answer = answer_question_from_state(
                question,
                snapshot["state"],
                response_mode=ask_mode,
                context_mode="web_ask",
            )
        except Exception:
            ask_error = "Plex Assistant could not answer that question from the current snapshot right now."

    return render_page(
        request,
        "dashboard.html",
        "Dashboard",
        response_mode="operator",
        snapshot=snapshot,
        ask_question=question,
        ask_answer=ask_answer,
        ask_error=ask_error,
        ask_mode=ask_mode,
    )


@app.get("/operator", response_class=HTMLResponse)
def operator_view(request: Request) -> HTMLResponse:
    return render_page(request, "operator.html", "Operator View", response_mode="operator")


@app.get("/manager", response_class=HTMLResponse)
def manager_view(request: Request) -> HTMLResponse:
    return render_page(request, "manager.html", "Manager View", response_mode="manager")


@app.get("/history", response_class=HTMLResponse)
def history_view(request: Request) -> HTMLResponse:
    return render_page(request, "history.html", "History", response_mode="operator")


@app.get("/alerts", response_class=HTMLResponse)
def alerts_view(request: Request) -> HTMLResponse:
    return render_page(request, "alerts.html", "Alerts", response_mode="operator")


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


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"ok": True})


@app.get("/debug/connectivity")
def debug_connectivity() -> JSONResponse:
    results = [
        {
            "name": "plex_base",
            **_probe_url(PLEX_BASE_URL, params={"X-Plex-Token": PLEX_TOKEN}),
        },
        {
            "name": "plex_identity",
            **_probe_url(f"{PLEX_BASE_URL}/identity", params={"X-Plex-Token": PLEX_TOKEN}),
        },
        {
            "name": "plex_status_sessions",
            **_probe_url(f"{PLEX_BASE_URL}/status/sessions", params={"X-Plex-Token": PLEX_TOKEN}),
        },
    ]
    return JSONResponse(
        {
            "ok": all(item["ok"] for item in results),
            "plex_base_url": PLEX_BASE_URL,
            "results": results,
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
    response_mode = str(payload.get("response_mode", "operator")).strip() or "operator"
    if response_mode not in {"operator", "manager"}:
        response_mode = "operator"

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
        answer = answer_question_from_state(
            question,
            snapshot["state"],
            response_mode=response_mode,
            context_mode="web_ask",
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
            "snapshot_source": snapshot.get("snapshot_source", "fresh"),
            "question": question,
            "answer": answer,
        }
    )
