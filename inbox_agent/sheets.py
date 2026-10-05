"""Optional Google Sheets activity log (set GOOGLE_SHEETS_ID and install the `gmail` extra)."""

from __future__ import annotations

from typing import Any

from loguru import logger

from .config import Settings

HEADERS = ["Triaged at", "Email ID", "From", "Subject", "Category", "Priority", "Summary", "Status", "Processing (ms)"]


class SheetsLog:
    def __init__(self, settings: Settings):
        self.ws = None
        if not settings.google_sheets_id or settings.demo_mode:
            return
        try:
            import gspread

            client = gspread.oauth(credentials_filename=settings.gmail_credentials_path, authorized_user_filename=settings.gmail_token_path)
            self.ws = client.open_by_key(settings.google_sheets_id).sheet1
            if self.ws.row_values(1) != HEADERS:
                self.ws.update("A1:I1", [HEADERS])
        except Exception as err:  # noqa: BLE001 - logging must never break triage
            logger.warning(f"Google Sheets log disabled: {err}")
            self.ws = None

    def append(self, e: dict[str, Any] | None) -> None:
        if not self.ws or not e:
            return
        try:
            self.ws.append_row([e["triaged_at"], e["id"], e["sender"], e["subject"], e["category"], e["priority"], e["summary"], e["status"], e["processing_ms"]])
        except Exception as err:  # noqa: BLE001
            logger.warning(f"Sheets append failed: {err}")
