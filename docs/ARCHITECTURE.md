# Architecture

## Flow

```
Gmail / demo mailbox
        │  fetch unread → store as status "new"
        ▼
   Classifier ── category, priority, confidence, reason
        ▼
   Summarizer ── summary, key points, action items, sentiment
        ▼
   Drafting rules (Settings page): category allowed? priority high enough?
        │ no → status "fyi"                     │ yes
        │                                        ▼
        │                 Retrieval: BM25 over approved replies (same category boosted)
        │                                        ▼
        │                 Drafter ── reply in the owner's voice, citing the examples used
        │                                        ▼
        │                 status "awaiting_approval"
        │                     │ approve (edited or not) │ rewrite with feedback │ skip
        │                     ▼                                                  ▼
        │                 send → status "sent" → reply saved to memory      status "skipped"
        ▼
   Follow-ups: high = 1 day, medium = N days (Done / Snooze)
```

If approval is turned off, drafted replies are sent straight away. It's on by default.

## Modules

| File | Responsibility |
|---|---|
| `pipeline.py` | `InboxAgent`: the flow above, plus the follow-up, digest and stats views. No I/O except through the store, mailbox and LLM it's given. |
| `agents.py` | The three prompts, with output validation (unknown categories fall back to `general`, and so on). |
| `llm.py` | `json(task, system, prompt, key)` over Anthropic, OpenAI, Gemini and DeepSeek. `RecordedLLM` replays `demo/recordings.json` by email id, with a keyword fallback for unknown mail. |
| `retrieval.py` | BM25 with stopwords and light stemming. People's names are ignored, and only matches within 40% of the best score are kept. |
| `mailbox.py` | `GmailMailbox` (OAuth desktop flow, plain-text body extraction, threaded replies) and `DemoMailbox` (sending only records to the Sent table). |
| `store.py` | SQLite tables: `emails`, `followups`, `sent`, `memory`, `activity`, `settings`. Thread-safe with a single connection. |
| `sheets.py` | Optional: appends each triaged email to a Google Sheet. |
| `web/app.py` | FastAPI routes. Forms post, then redirect (303); `hx-boost` makes navigation feel instant. HTTP Basic auth is required outside demo mode. |
| `demo/seed.py` | Runs the real pipeline over the fictional mailbox with recorded answers: history (decided), current (awaiting decision) and three untriaged emails. |

## Design decisions

- **A human approves by default.** An agent that emails customers on its own is a liability until you trust it; the Settings page lets you relax that per your comfort.
- **BM25 instead of an embedding store.** A personal inbox has hundreds to low thousands of approved replies. BM25 is instant, needs no model download, and its matches are easy to explain in the UI ("match 14.8"). Embeddings slot into `retrieval.similar` if needed.
- **Recorded answers for the demo.** The demo and its screenshots are repeatable, cost nothing to host, and can't be abused to spend someone's API credit.
- **SQLite.** One file and no server; the agent is single-user by design.
