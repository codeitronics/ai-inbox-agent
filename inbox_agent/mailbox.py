"""Where email comes from and where replies go: a fictional demo mailbox, or Gmail."""

from __future__ import annotations

import base64
import os
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from loguru import logger

from .config import Settings


class Mailbox(Protocol):
    mock: bool

    def fetch_unread(self, limit: int) -> list[dict[str, Any]]: ...
    def mark_read(self, email_id: str) -> None: ...
    def send(self, to: str, subject: str, body: str, thread_id: str | None) -> bool: ...


class DemoMailbox:
    """Emails are seeded into the database by `demo.seed`; sending only records to the mock Sent box."""

    mock = True

    def fetch_unread(self, limit: int) -> list[dict[str, Any]]:
        return []

    def mark_read(self, email_id: str) -> None:
        pass

    def send(self, to: str, subject: str, body: str, thread_id: str | None) -> bool:
        logger.info(f"[demo] would send '{subject}' to {to}")
        return True


class GmailMailbox:
    mock = False
    SCOPES = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.modify",
    ]

    def __init__(self, settings: Settings):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as err:  # pragma: no cover
            raise SystemExit("Gmail support is optional: install it with `uv sync --extra gmail`.") from err

        creds = None
        if os.path.exists(settings.gmail_token_path):
            creds = Credentials.from_authorized_user_file(settings.gmail_token_path, self.SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(settings.gmail_credentials_path, self.SCOPES)
                creds = flow.run_local_server(port=0)
            with open(settings.gmail_token_path, "w") as f:
                f.write(creds.to_json())
        self.service = build("gmail", "v1", credentials=creds)

    def fetch_unread(self, limit: int) -> list[dict[str, Any]]:
        res = self.service.users().messages().list(userId="me", labelIds=["INBOX", "UNREAD"], maxResults=limit).execute()
        return [self._message(m["id"]) for m in res.get("messages", [])]

    def _message(self, mid: str) -> dict[str, Any]:
        m = self.service.users().messages().get(userId="me", id=mid, format="full").execute()
        headers = {h["name"].lower(): h["value"] for h in m["payload"].get("headers", [])}
        try:
            received = parsedate_to_datetime(headers.get("date", "")).isoformat()
        except (TypeError, ValueError):
            received = ""
        return {
            "id": mid,
            "thread_id": m.get("threadId"),
            "sender": headers.get("from", ""),
            "subject": headers.get("subject", "(no subject)"),
            "body": self._body(m["payload"]) or m.get("snippet", ""),
            "received_at": received,
        }

    def _body(self, payload: dict[str, Any]) -> str:
        if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
        for part in payload.get("parts", []) or []:
            text = self._body(part)
            if text:
                return text
        return ""

    def mark_read(self, email_id: str) -> None:
        self.service.users().messages().modify(userId="me", id=email_id, body={"removeLabelIds": ["UNREAD"]}).execute()

    def send(self, to: str, subject: str, body: str, thread_id: str | None) -> bool:
        msg = MIMEText(body)
        msg["to"], msg["subject"] = to, subject
        payload: dict[str, Any] = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
        if thread_id:
            payload["threadId"] = thread_id
        try:
            self.service.users().messages().send(userId="me", body=payload).execute()
            return True
        except Exception as err:  # noqa: BLE001
            logger.error(f"Gmail send failed: {err}")
            return False


def get_mailbox(settings: Settings) -> Mailbox:
    return DemoMailbox() if settings.demo_mode else GmailMailbox(settings)
