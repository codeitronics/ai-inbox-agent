"""Builds the demo inbox by running the real pipeline over the fictional mailbox with recorded AI answers.

History emails are triaged and decided (so stats and reply memory have something in them), current emails are
triaged and waiting for a decision, and a few arrive untriaged so you can watch the agent work.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from ..llm import RecordedLLM
from ..mailbox import DemoMailbox
from ..pipeline import InboxAgent
from ..store import iso, now

DATA = json.loads((Path(__file__).parent / "emails.json").read_text())


def seed(agent: InboxAgent) -> None:
    store = agent.store
    store.reset()
    t0 = now()

    for m in DATA["memory"]:
        store.remember(m["incoming"], m["reply"], m["category"], m["subject"], at=iso(t0 - timedelta(days=m["days_ago"])))

    # The seed always uses recorded answers, even when a live provider is configured.
    seeding = InboxAgent(agent.settings, store=store, llm=RecordedLLM(), mailbox=DemoMailbox())
    for e in DATA["emails"]:
        received = t0 - timedelta(minutes=e["minutes_ago"])
        store.add_email({**e, "received_at": iso(received)})
        if e["phase"] == "new":
            continue

        seeding.triage(e["id"])
        triaged = received + timedelta(minutes=2)
        store.update_email(e["id"], triaged_at=iso(triaged))

        if e["phase"] == "history":
            decided = received + timedelta(minutes=40)
            if e["outcome"] == "sent":
                seeding.approve(e["id"])
            elif e["outcome"] == "skipped":
                seeding.skip(e["id"])
            store.update_email(e["id"], decided_at=iso(decided))
            with store.tx() as c:
                c.execute("UPDATE sent SET sent_at = ? WHERE email_id = ?", (iso(decided), e["id"]))
                c.execute("UPDATE memory SET created_at = ? WHERE subject = ? AND created_at > ?", (iso(decided), e["subject"], iso(decided)))
                c.execute("UPDATE followups SET status = 'done', completed_at = ? WHERE email_id = ?", (iso(decided), e["id"]))

        with store.tx() as c:
            c.execute("UPDATE activity SET at = ? WHERE email_id = ? AND kind = 'triaged'", (iso(triaged), e["id"]))
            c.execute("UPDATE activity SET at = ? WHERE email_id = ? AND kind IN ('sent','skipped')", (iso(received + timedelta(minutes=40)), e["id"]))

    # One follow-up already due: check that Alder & Finch released payment after the POD was sent.
    store.schedule_followup("h2", -0.1)
    store.log("seeded", detail="Demo inbox reset: fictional Northwind Logistics mailbox")
