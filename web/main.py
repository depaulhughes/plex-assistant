from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from alerts import load_recent_alert_history
from app import answer_question_from_state, build_state
from config import ASK_STATE_CACHE_SECONDS
from history import load_recent_history
from summaries import build_history_display_event


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Plex Assistant UI")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

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
    display_history = [build_history_display_event(event) for event in reversed(recent_history)]

    return {
        "ok": snapshot["ok"],
        "generated_at": snapshot["generated_at"],
        "generated_at_label": _format_timestamp(snapshot["generated_at"]),
        "error": snapshot["error"],
        "snapshot_source": snapshot.get("snapshot_source", "fresh"),
        "snapshot_age_seconds": snapshot.get("snapshot_age_seconds"),
        "response_mode": response_mode,
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
        "recent_history": display_history,
        "recent_alert_history": list(reversed(recent_alert_history)),
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
    return templates.TemplateResponse(template_name, context)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    snapshot = _get_fresh_or_cached_snapshot(prefer_cache=False)
    return render_page(request, "dashboard.html", "Dashboard", response_mode="operator", snapshot=snapshot)


@app.post("/ask", response_class=HTMLResponse)
async def ask(request: Request) -> HTMLResponse:
    form = await request.form()
    question = str(form.get("question", "")).strip()
    ask_mode = str(form.get("ask_mode", "operator")).strip() or "operator"
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
