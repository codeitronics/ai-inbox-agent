"""Web UI (FastAPI + Jinja + HTMX boost): triage board, email view with approval, follow-ups, digest, sent, stats, settings."""

from __future__ import annotations

import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from ..agents import CATEGORIES
from ..config import get_settings
from ..pipeline import InboxAgent

HERE = Path(__file__).parent
settings = get_settings()
if not settings.demo_mode and not settings.web_password:
    raise SystemExit("WEB_PASSWORD must be set when DEMO_MODE=false: the UI shows your real inbox.")

agent = InboxAgent(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.demo_mode and not agent.store.emails():
        from ..demo.seed import seed

        seed(agent)
    if settings.demo_mode and settings.demo_reset_minutes > 0:
        def loop() -> None:
            from ..demo.seed import seed

            while True:
                time.sleep(settings.demo_reset_minutes * 60)
                logger.info("Scheduled demo reset")
                seed(agent)

        threading.Thread(target=loop, daemon=True).start()
    yield


app = FastAPI(title="AI Inbox Agent", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
basic = HTTPBasic(auto_error=False)


def guard(creds: Annotated[HTTPBasicCredentials | None, Depends(basic)]) -> None:
    if settings.demo_mode:
        return
    if not creds or not secrets.compare_digest(creds.password.encode(), settings.web_password.encode()):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})


Guard = Depends(guard)


def ago(value: str | None) -> str:
    if not value:
        return ""
    s = (datetime.now(timezone.utc) - datetime.fromisoformat(value)).total_seconds()
    if s < 0:
        s = -s
        unit = "m" if s < 3600 else "h" if s < 86400 else "d"
        n = s / 60 if unit == "m" else s / 3600 if unit == "h" else s / 86400
        return f"in {max(1, round(n))}{unit}"
    if s < 3600:
        return f"{max(1, round(s / 60))}m ago"
    if s < 86400:
        return f"{round(s / 3600)}h ago"
    return f"{round(s / 86400)}d ago"


def name(sender: str) -> str:
    return sender.split("<")[0].strip().strip('"') or sender


templates.env.filters["ago"] = ago
templates.env.filters["name"] = name


def page(request: Request, template: str, **ctx) -> HTMLResponse:
    counts = {
        "awaiting": len(agent.store.emails(status="awaiting_approval")),
        "new": len(agent.store.emails(status="new")),
        "due": len(agent.followups()["due"]),
    }
    return templates.TemplateResponse(
        request,
        template,
        {"demo": settings.demo_mode, "provider": agent.llm.provider, "model": agent.llm.model, "counts": counts, "path": request.url.path, **ctx},
    )


def back(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "demo": settings.demo_mode, "provider": agent.llm.provider}


@app.get("/", response_class=HTMLResponse, dependencies=[Guard])
def inbox(request: Request, view: str = "open"):
    views = {
        "open": lambda e: e["status"] in ("new", "awaiting_approval", "fyi") and e["category"] not in ("promotional", "spam", "newsletter"),
        "approval": lambda e: e["status"] == "awaiting_approval",
        "filtered": lambda e: e["category"] in ("promotional", "spam", "newsletter"),
        "done": lambda e: e["status"] in ("sent", "skipped"),
        "all": lambda e: True,
    }
    rows = [e for e in agent.store.emails() if views.get(view, views["open"])(e)]
    return page(request, "inbox.html", emails=rows, view=view, activity=agent.store.activity(8))


@app.post("/triage", dependencies=[Guard])
def triage_all():
    agent.triage_all()
    return back("/")


@app.get("/email/{email_id}", response_class=HTMLResponse, dependencies=[Guard])
def email(request: Request, email_id: str):
    e = agent.store.email(email_id)
    if not e:
        raise HTTPException(404)
    sources = {m["id"]: m for m in agent.store.memory()}
    examples = [sources[s["id"]] | {"score": s["score"]} for s in (e.get("draft_sources") or []) if s["id"] in sources]
    followup = next((f for f in agent.store.followups() if f["email_id"] == email_id), None)
    sent = next((s for s in agent.store.sent() if s["email_id"] == email_id), None)
    return page(request, "email.html", e=e, examples=examples, followup=followup, sent=sent)


@app.post("/email/{email_id}/triage", dependencies=[Guard])
def triage_one(email_id: str):
    agent.triage(email_id)
    return back(f"/email/{email_id}")


@app.post("/email/{email_id}/approve", dependencies=[Guard])
def approve(email_id: str, subject: Annotated[str, Form()], body: Annotated[str, Form()]):
    agent.approve(email_id, subject, body)
    return back(f"/email/{email_id}")


@app.post("/email/{email_id}/refine", dependencies=[Guard])
def refine(email_id: str, feedback: Annotated[str, Form()], body: Annotated[str, Form()] = ""):
    if feedback.strip():
        agent.refine(email_id, feedback.strip(), body or None)
    return back(f"/email/{email_id}#draft")


@app.post("/email/{email_id}/skip", dependencies=[Guard])
def skip(email_id: str):
    agent.skip(email_id)
    return back("/")


@app.get("/followups", response_class=HTMLResponse, dependencies=[Guard])
def followups(request: Request):
    return page(request, "followups.html", f=agent.followups())


@app.post("/followups/{email_id}/done", dependencies=[Guard])
def followup_done(email_id: str):
    agent.complete_followup(email_id)
    return back("/followups")


@app.post("/followups/{email_id}/snooze", dependencies=[Guard])
def followup_snooze(email_id: str):
    agent.snooze_followup(email_id, 2)
    return back("/followups")


@app.get("/digest", response_class=HTMLResponse, dependencies=[Guard])
def digest(request: Request):
    return page(request, "digest.html", d=agent.digest())


@app.get("/sent", response_class=HTMLResponse, dependencies=[Guard])
def sent(request: Request):
    return page(request, "sent.html", sent=agent.store.sent())


@app.get("/stats", response_class=HTMLResponse, dependencies=[Guard])
def stats(request: Request):
    s = agent.stats()
    top = max([n for _, n in s["by_category"]] or [1])
    return page(request, "stats.html", s=s, top=top, settings=settings)


@app.get("/settings", response_class=HTMLResponse, dependencies=[Guard])
def settings_page(request: Request, saved: int = 0):
    return page(request, "settings.html", rules=agent.store.rules(), categories=CATEGORIES, saved=saved, settings=settings)


@app.post("/settings", dependencies=[Guard])
async def save_settings(request: Request):
    form = await request.form()
    agent.store.save_rules(
        {
            "approval_required": form.get("approval_required") == "on",
            "draft_for_categories": [c for c in form.getlist("draft_for_categories") if c in CATEGORIES],
            "draft_min_priority": form.get("draft_min_priority") if form.get("draft_min_priority") in ("high", "medium", "low") else "medium",
            "follow_up_days": max(1, min(30, int(form.get("follow_up_days") or 3))),
        }
    )
    return back("/settings?saved=1")


@app.post("/demo/reset", dependencies=[Guard])
def demo_reset():
    if not settings.demo_mode:
        raise HTTPException(404)
    from ..demo.seed import seed

    seed(agent)
    return back("/")
