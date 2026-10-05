"""The inbox agent: fetch → classify → summarise → draft (with past replies) → wait for approval → send → remember."""

from __future__ import annotations

import re
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from .agents import Classifier, Drafter, Summarizer, sender_address
from .config import Settings, get_settings
from .llm import LLM, get_llm
from .mailbox import Mailbox, get_mailbox
from .retrieval import similar
from .sheets import SheetsLog
from .store import Store, iso, now

PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


class InboxAgent:
    def __init__(self, settings: Settings | None = None, store: Store | None = None, llm: LLM | None = None, mailbox: Mailbox | None = None):
        self.settings = settings or get_settings()
        self.store = store or Store(self.settings.database_path)
        self.llm = llm or get_llm(self.settings)
        self.mailbox = mailbox or get_mailbox(self.settings)
        self.sheets = SheetsLog(self.settings)
        self.classifier = Classifier(self.llm)
        self.summarizer = Summarizer(self.llm)
        self.drafter = Drafter(self.llm, self.settings.owner_name, self.settings.owner_signature)

    # ---- intake -------------------------------------------------------------------------------

    def fetch(self) -> int:
        """Pull unread mail into the store (no-op in demo mode, where the seed provides it)."""
        added = 0
        for e in self.mailbox.fetch_unread(self.settings.max_emails_per_run):
            if self.store.add_email(e):
                added += 1
                self.mailbox.mark_read(e["id"])
        if added:
            self.store.log("fetched", detail=f"{added} new email{'s' if added != 1 else ''}")
        return added

    def triage_all(self) -> int:
        self.fetch()
        pending = self.store.emails(status="new")
        for e in pending:
            try:
                self.triage(e["id"])
            except Exception as err:  # noqa: BLE001 - one bad email must not stop the run
                logger.exception(f"Triage failed for {e['id']}: {err}")
                self.store.log("error", e["id"], str(err)[:300])
        return len(pending)

    def triage(self, email_id: str) -> dict[str, Any]:
        e = self.store.email(email_id)
        if not e:
            raise KeyError(email_id)
        started = time.perf_counter()
        rules = self.store.rules()

        c = self.classifier.run(e)
        s = self.summarizer.run(e, c)
        fields: dict[str, Any] = {
            "category": c["category"], "priority": c["priority"], "confidence": c["confidence"], "reasoning": c["reasoning"],
            "summary": s["summary"], "key_points": s["key_points"], "action_items": s["action_items"], "sentiment": s["sentiment"],
            "status": "fyi", "triaged_at": iso(now()),
        }

        if self._should_draft(c, rules):
            names = {w.lower() for w in re.findall(r"[A-Za-z]+", f"{e['sender'].split('<')[0]} {self.settings.owner_signature}")}
            examples = similar(f"{e['subject']} {e['body']}", self.store.memory(), k=3, category=c["category"], ignore=names)
            d = self.drafter.run(e, c, s, examples)
            fields.update(
                draft_subject=d["subject"], draft_body=d["body"], draft_tone=d["tone"], draft_confidence=d["confidence"],
                draft_sources=[{"id": x["id"], "subject": x.get("subject"), "score": x["score"]} for x in examples],
                status="awaiting_approval",
            )

        fields["processing_ms"] = int((time.perf_counter() - started) * 1000)
        self.store.update_email(email_id, **fields)
        self.store.log("triaged", email_id, f"{c['category']} · {c['priority']}" + (" · reply drafted" if fields["status"] == "awaiting_approval" else ""))

        if c["priority"] in ("high", "medium") and c["category"] not in ("spam", "promotional", "newsletter"):
            days = 1 if c["priority"] == "high" else float(rules["follow_up_days"])
            self.store.schedule_followup(email_id, days)

        if fields["status"] == "awaiting_approval" and not rules["approval_required"]:
            self.approve(email_id)

        self.sheets.append(self.store.email(email_id))
        return self.store.email(email_id) or {}

    @staticmethod
    def _should_draft(c: dict[str, Any], rules: dict[str, Any]) -> bool:
        return c["category"] in rules["draft_for_categories"] and PRIORITY_RANK[c["priority"]] <= PRIORITY_RANK[rules["draft_min_priority"]]

    # ---- decisions ----------------------------------------------------------------------------

    def approve(self, email_id: str, subject: str | None = None, body: str | None = None) -> bool:
        e = self.store.email(email_id)
        if not e or e["status"] in ("sent", "skipped"):
            return False
        subject = (subject or e["draft_subject"] or f"Re: {e['subject']}").strip()
        body = (body if body is not None else e["draft_body"] or "").strip()
        if not body:
            return False
        if not self.mailbox.send(sender_address(e["sender"]), subject, body, e.get("thread_id")):
            self.store.log("error", email_id, "Send failed")
            return False
        self.store.record_sent(email_id, sender_address(e["sender"]), subject, body, mock=self.mailbox.mock)
        self.store.update_email(email_id, status="sent", draft_subject=subject, draft_body=body, decided_at=iso(now()))
        # Approved replies become examples for future drafts.
        self.store.remember(e["body"], body, e["category"], e["subject"])
        self.store.log("sent", email_id, f"Reply to {sender_address(e['sender'])}" + (" (demo — not actually sent)" if self.mailbox.mock else ""))
        return True

    def refine(self, email_id: str, feedback: str, current: str | None = None) -> dict[str, Any]:
        e = self.store.email(email_id)
        if not e:
            raise KeyError(email_id)
        d = self.drafter.refine(e, current or e["draft_body"] or "", feedback)
        self.store.update_email(email_id, draft_subject=d["subject"], draft_body=d["body"], draft_tone=d["tone"], draft_confidence=d["confidence"], status="awaiting_approval")
        self.store.log("refined", email_id, feedback[:200])
        return self.store.email(email_id) or {}

    def skip(self, email_id: str) -> None:
        self.store.update_email(email_id, status="skipped", decided_at=iso(now()))
        self.store.log("skipped", email_id)

    def complete_followup(self, email_id: str) -> None:
        self.store.complete_followup(email_id)
        self.store.log("followup_done", email_id)

    def snooze_followup(self, email_id: str, days: float = 2) -> None:
        self.store.schedule_followup(email_id, days)
        self.store.log("followup_snoozed", email_id, f"{days:g} days")

    # ---- views --------------------------------------------------------------------------------

    def followups(self) -> dict[str, list[dict[str, Any]]]:
        t = iso(now())
        rows = self.store.followups()
        return {
            "due": [f for f in rows if f["status"] == "pending" and f["due_at"] <= t],
            "upcoming": [f for f in rows if f["status"] == "pending" and f["due_at"] > t],
            "done": [f for f in rows if f["status"] == "done"][:10],
        }

    def digest(self) -> dict[str, Any]:
        emails = [e for e in self.store.emails() if e["status"] != "new"]
        open_ = [e for e in emails if e["status"] in ("awaiting_approval", "fyi")]
        return {
            "date": datetime.now(timezone.utc).strftime("%A, %d %B %Y"),
            "high": [e for e in open_ if e["priority"] == "high"],
            "awaiting": [e for e in emails if e["status"] == "awaiting_approval"],
            "fyi": [e for e in open_ if e["status"] == "fyi" and e["category"] in ("general", "important", "newsletter")],
            "filtered": Counter(e["category"] for e in emails if e["category"] in ("promotional", "spam")),
            "followups_due": self.followups()["due"],
            "untriaged": len(self.store.emails(status="new")),
        }

    def stats(self) -> dict[str, Any]:
        emails = [e for e in self.store.emails() if e["status"] != "new"]
        drafted = [e for e in emails if e["draft_body"]]
        decided = [e for e in drafted if e["status"] in ("sent", "skipped")]
        sent = [e for e in drafted if e["status"] == "sent"]
        times = [e["processing_ms"] for e in emails if e["processing_ms"]]
        minutes = len(emails) * self.settings.minutes_saved_triage + len(sent) * self.settings.minutes_saved_reply
        return {
            "triaged": len(emails),
            "by_category": Counter(e["category"] for e in emails).most_common(),
            "by_priority": Counter(e["priority"] for e in emails),
            "drafted": len(drafted),
            "sent": len(sent),
            "approval_rate": round(100 * len(sent) / len(decided)) if decided else None,
            "avg_ms": round(sum(times) / len(times)) if times else None,
            "minutes_saved": round(minutes),
            "memory": len(self.store.memory()),
            "provider": self.llm.provider,
            "model": self.llm.model,
        }
