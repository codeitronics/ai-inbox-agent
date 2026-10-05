import pytest

from inbox_agent.config import Settings
from inbox_agent.demo.seed import seed
from inbox_agent.llm import RecordedLLM
from inbox_agent.mailbox import DemoMailbox
from inbox_agent.pipeline import InboxAgent
from inbox_agent.retrieval import similar
from inbox_agent.store import Store


@pytest.fixture
def agent():
    s = Settings(_env_file=None, demo_mode=True, ai_provider="recorded", database_path=":memory:")
    a = InboxAgent(s, store=Store(":memory:"), llm=RecordedLLM(), mailbox=DemoMailbox())
    seed(a)
    return a


def test_seed_builds_history_inbox_and_new_mail(agent):
    st = agent.store
    assert {e["id"] for e in st.emails(status="new")} == {"e109", "e110", "e111"}
    assert {e["id"] for e in st.emails(status="awaiting_approval")} == {"e101", "e102", "e103", "e104", "e108"}
    assert st.email("e107")["category"] == "spam" and st.email("e107")["status"] == "fyi"
    assert len(st.sent()) == 3  # h1, h2, h5
    assert len(st.memory()) == 6  # 3 seeded + 3 approved
    assert [f["email_id"] for f in agent.followups()["due"]] == ["h2"]


def test_triage_classifies_summarises_and_drafts(agent):
    assert agent.triage_all() == 3
    urgent = agent.store.email("e109")
    assert (urgent["category"], urgent["priority"], urgent["status"]) == ("urgent", "high", "awaiting_approval")
    assert urgent["draft_body"].startswith("Hi Priya")
    assert urgent["draft_sources"], "drafts should cite past replies"
    assert agent.store.email("e111")["status"] == "fyi"  # newsletter: no draft


def test_approve_sends_to_mock_box_and_remembers(agent):
    before = len(agent.store.memory())
    assert agent.approve("e104", "Re: dock review", "Friday works.")
    e = agent.store.email("e104")
    assert e["status"] == "sent" and e["draft_body"] == "Friday works."
    assert agent.store.sent()[0]["mock"] == 1
    assert len(agent.store.memory()) == before + 1
    assert not agent.approve("e104"), "cannot send twice"


def test_refine_and_skip(agent):
    before = agent.store.email("e102")["draft_body"]
    after = agent.refine("e102", "Confirm it's 12 pallets")["draft_body"]
    assert after != before and "12 pallets" in after
    agent.skip("e103")
    assert agent.store.email("e103")["status"] == "skipped"


def test_rules_turn_off_drafting_and_approval(agent):
    agent.store.save_rules({"draft_for_categories": ["urgent"], "approval_required": False})
    agent.triage("e110")  # general → no draft under the new rules
    assert agent.store.email("e110")["status"] == "fyi"
    agent.triage("e109")  # urgent → drafted and, with approval off, sent
    assert agent.store.email("e109")["status"] == "sent"


def test_followups_and_stats(agent):
    agent.complete_followup("h2")
    assert agent.followups()["due"] == []
    s = agent.stats()
    assert s["triaged"] == 14 and s["sent"] == 3 and s["approval_rate"] == 75
    assert agent.digest()["high"]


def test_retrieval_prefers_matching_reply():
    memory = [
        {"id": 1, "subject": "Invoice INV-1", "incoming": "invoice pallets billed wrong credit", "reply": "a"},
        {"id": 2, "subject": "Dock schedule", "incoming": "move the dock meeting to friday", "reply": "b"},
    ]
    assert similar("our invoice shows the wrong pallets", memory)[0]["id"] == 1
