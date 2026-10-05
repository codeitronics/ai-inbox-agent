"""Command line: `inbox-agent serve | run | triage | demo-reset | digest`."""

from __future__ import annotations

import argparse
import time

from loguru import logger

from .config import get_settings


def main() -> None:
    p = argparse.ArgumentParser(prog="inbox-agent", description="AI inbox triage with human approval.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="Run the web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    sub.add_parser("triage", help="Fetch and triage new mail once")
    sub.add_parser("run", help="Fetch and triage on a loop (POLL_INTERVAL_SECONDS)")
    sub.add_parser("demo-reset", help="Wipe the database and reseed the demo mailbox")
    sub.add_parser("digest", help="Print today's digest")
    args = p.parse_args()

    if args.cmd == "serve":
        import uvicorn

        # Trust X-Forwarded-Proto/For from the reverse proxy so redirects keep https. The port is only published
        # on 127.0.0.1 (compose.yaml) or reached through the proxy network, so nothing else can spoof these headers.
        uvicorn.run("inbox_agent.web.app:app", host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="*")
        return

    from .pipeline import InboxAgent

    agent = InboxAgent()
    if args.cmd == "triage":
        print(f"Triaged {agent.triage_all()} email(s).")
    elif args.cmd == "run":
        interval = get_settings().poll_interval_seconds
        logger.info(f"Polling every {interval}s with {agent.llm.provider}/{agent.llm.model}. Ctrl+C to stop.")
        while True:
            agent.triage_all()
            time.sleep(interval)
    elif args.cmd == "demo-reset":
        from .demo.seed import seed

        seed(agent)
        print("Demo inbox reset.")
    elif args.cmd == "digest":
        d = agent.digest()
        print(f"Digest for {d['date']}")
        print(f"  High priority open: {len(d['high'])}")
        for e in d["high"]:
            print(f"    - {e['subject']} ({e['sender']})")
        print(f"  Replies awaiting approval: {len(d['awaiting'])}")
        print(f"  Follow-ups due: {len(d['followups_due'])}")
        print(f"  Filtered out: {dict(d['filtered'])}")


if __name__ == "__main__":
    main()
