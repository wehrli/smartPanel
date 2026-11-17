"""SP-1215: every pane fixture through the activity classifier."""
from __future__ import annotations

import pytest

from smartpanel.sessions.activity import IDLE, RUNNING, WAITING, classify_pane
from tests.panes import all_panes, pane

EXPECTED = {
    "bash_permission": WAITING,
    "edit_permission": WAITING,
    "question_3": WAITING,
    "question_4": WAITING,
    "trust": WAITING,
    "trust_unnumbered": WAITING,
    "running_agents": RUNNING,
    "running_bash": RUNNING,
    "running_read": RUNNING,
    "running_described_bash": RUNNING,
    "running_thinking": RUNNING,
    "idle_prose_question": IDLE,
    "user_numbered": IDLE,
    "user_numbered_draft": IDLE,
}


def test_every_fixture_has_an_expectation():
    assert set(all_panes()) == set(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture_classifies(name):
    assert classify_pane(pane(name)) == EXPECTED[name]
