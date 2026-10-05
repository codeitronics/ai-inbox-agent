import os
import subprocess
import sys


def test_app_works_under_a_path_prefix():
    # Run in a subprocess: settings are read once at import time.
    code = """
from fastapi.testclient import TestClient
from inbox_agent.web.app import app
with TestClient(app, root_path="/inbox") as c:
    html = c.get("/").text
    assert 'href="/inbox/email/e101"' in html and 'src="/inbox/static/htmx.min.js"' in html, html[:500]
    r = c.post("/email/e104/skip", follow_redirects=False)
    assert r.headers["location"] == "/inbox/", r.headers
"""
    env = {**os.environ, "DEMO_MODE": "true", "AI_PROVIDER": "recorded", "DATABASE_PATH": ":memory:", "ROOT_PATH": "/inbox"}
    res = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr[-2000:]
