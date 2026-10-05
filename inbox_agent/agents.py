"""The three model-backed agents: classifier, summariser and reply drafter."""

from __future__ import annotations

from typing import Any

from .llm import LLM

CATEGORIES = ["urgent", "important", "general", "newsletter", "promotional", "spam"]
PRIORITIES = ["high", "medium", "low"]


def sender_name(sender: str) -> str:
    name = sender.split("<")[0].strip().strip('"') if "<" in sender else ""
    return name.split()[0] if name else ""


def sender_address(sender: str) -> str:
    return sender.split("<")[1].split(">")[0].strip() if "<" in sender else sender.strip()


class Classifier:
    system = "You classify incoming email for a busy operations lead. Be decisive and conservative about urgency."

    def __init__(self, llm: LLM):
        self.llm = llm

    def run(self, e: dict[str, Any]) -> dict[str, Any]:
        prompt = f"""Classify this email.

From: {e['sender']}
Subject: {e['subject']}
Body: {e['body'][:2000]}

Return JSON: {{"category": one of {CATEGORIES}, "priority": one of {PRIORITIES}, "confidence": 0.0-1.0, "reasoning": "one sentence"}}

Categories: urgent = time-sensitive, needs action today; important = key contact or business-critical;
general = everything else that a person wrote; newsletter = subscriptions and digests;
promotional = marketing and offers; spam = unwanted or suspicious (including phishing).
Priority: high = act within 24h; medium = within a week; low = FYI."""
        r = self.llm.json("classify", self.system, prompt, key=e["id"])
        r["category"] = r.get("category") if r.get("category") in CATEGORIES else "general"
        r["priority"] = r.get("priority") if r.get("priority") in PRIORITIES else "medium"
        r["confidence"] = float(r.get("confidence") or 0.5)
        r.setdefault("reasoning", "")
        return r


class Summarizer:
    system = "You summarise email so it can be acted on in ten seconds. Never invent facts that are not in the email."

    def __init__(self, llm: LLM):
        self.llm = llm

    def run(self, e: dict[str, Any], c: dict[str, Any]) -> dict[str, Any]:
        prompt = f"""Summarise this email.

From: {e['sender']}
Subject: {e['subject']}
Body: {e['body'][:6000]}

Category: {c['category']} / Priority: {c['priority']}

Return JSON: {{"summary": "1-2 sentences", "key_points": ["..."], "action_items": ["what the reader must do, with any deadline"], "sentiment": "positive | neutral | negative | urgent"}}"""
        r = self.llm.json("summarize", self.system, prompt, key=e["id"])
        return {
            "summary": r.get("summary") or e["body"][:200],
            "key_points": list(r.get("key_points") or []),
            "action_items": list(r.get("action_items") or []),
            "sentiment": r.get("sentiment") or "neutral",
        }


class Drafter:
    system = (
        "You draft email replies in the owner's voice: short, specific and polite. Use the past replies as a guide to "
        "tone and to how the owner usually answers. Never promise dates, prices or actions the email doesn't support; "
        "ask instead."
    )

    def __init__(self, llm: LLM, owner_name: str, signature: str):
        self.llm, self.owner_name, self.signature = llm, owner_name, signature

    def run(self, e: dict[str, Any], c: dict[str, Any], s: dict[str, Any], examples: list[dict[str, Any]]) -> dict[str, Any]:
        past = "\n\n".join(f"Example {i}:\nThey wrote: {x['incoming'][:400]}\nOwner replied: {x['reply'][:600]}" for i, x in enumerate(examples, 1))
        prompt = f"""Draft a reply from {self.owner_name}.

INCOMING
From: {e['sender']}
Subject: {e['subject']}
Body: {e['body'][:6000]}

ANALYSIS
Category: {c['category']} / Priority: {c['priority']}
Summary: {s['summary']}
Action items: {'; '.join(s['action_items']) or 'none'}

PAST REPLIES (for tone and typical answers)
{past or 'none yet'}

Greet {sender_name(e['sender']) or 'them'} by first name and sign off with:
{self.signature}

Return JSON: {{"subject": "Re: ...", "body": "the full reply", "tone": "formal | professional | friendly", "confidence": 0.0-1.0}}"""
        return self._clean(self.llm.json("draft", self.system, prompt, key=e["id"]), e)

    def refine(self, e: dict[str, Any], draft: str, feedback: str) -> dict[str, Any]:
        prompt = f"""Rewrite this reply following the owner's feedback. Keep everything else that was right.

ORIGINAL EMAIL
From: {e['sender']}
Subject: {e['subject']}
Body: {e['body'][:4000]}

CURRENT DRAFT
{draft}

FEEDBACK
{feedback}

Return JSON: {{"subject": "Re: ...", "body": "the full revised reply", "tone": "formal | professional | friendly", "confidence": 0.0-1.0}}"""
        return self._clean(self.llm.json("refine", self.system, prompt, key=e["id"]), e)

    @staticmethod
    def _clean(r: dict[str, Any], e: dict[str, Any]) -> dict[str, Any]:
        subject = r.get("subject") or ""
        if not subject.lower().startswith("re:"):
            subject = f"Re: {e['subject']}"
        return {
            "subject": subject,
            "body": (r.get("body") or "").strip(),
            "tone": r.get("tone") or "professional",
            "confidence": float(r.get("confidence") or 0.5),
        }
