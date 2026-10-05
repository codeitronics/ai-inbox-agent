import os

os.environ.update(DEMO_MODE="true", AI_PROVIDER="recorded", DATABASE_PATH=":memory:")

from fastapi.testclient import TestClient  # noqa: E402

from inbox_agent.web.app import app  # noqa: E402


def test_pages_render_and_approve_flow():
    with TestClient(app) as c:
        for path in ["/", "/?view=filtered", "/email/e101", "/followups", "/digest", "/sent", "/stats", "/settings", "/healthz"]:
            assert c.get(path).status_code == 200, path
        assert "Reply ready" in c.get("/").text
        r = c.post("/email/e104/approve", data={"subject": "Re: dock", "body": "Friday works."}, follow_redirects=False)
        assert r.status_code == 303
        assert "Friday works." in c.get("/sent").text
        c.post("/triage")
        assert "Not triaged" not in c.get("/?view=all").text
        c.post("/demo/reset")
        assert "Not triaged" in c.get("/?view=all").text
