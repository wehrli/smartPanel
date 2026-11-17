from __future__ import annotations

from smartpanel.sessions.doing import MAX_DETAIL_CHARS, describe_running
from tests.panes import pane


def test_bash_tool_header():
    assert describe_running(pane("running_bash")) == "Bash · npm test -- filters"


def test_read_tool_uses_the_latest_header_not_an_earlier_one():
    assert describe_running(pane("running_read")) == "Read · web/app/advisors/[id]/page.tsx"


def test_described_bash_reads_the_dollar_line():
    assert describe_running(pane("running_described_bash")) == (
        "Bash · tmux capture-pane -p -t '=claude-smartPanel:'")


def test_background_agents():
    assert describe_running(pane("running_agents")) == "Waiting for 2 background agents"


def test_one_background_agent_is_singular():
    text = pane("running_agents").replace("2 background agents", "1 background agent")
    assert describe_running(text) == "Waiting for 1 background agent"


def test_prose_after_a_tool_is_none_not_the_stale_tool():
    # The last bullet is Claude writing; "Ran 3 shell commands" above it is done.
    assert describe_running(pane("running_thinking")) is None


def test_idle_and_empty_are_none():
    assert describe_running(pane("idle_prose_question")) is None
    assert describe_running("") is None
    assert describe_running("   \n") is None


def test_agents_list_bullets_under_the_input_box_are_ignored():
    # "  ● main" in the status rows is chrome, never a tool call.
    text = pane("running_agents").replace("✻ Waiting for 2 background agents to finish", "")
    assert describe_running(text) is None


def test_long_commands_are_cut():
    long = "x" * 200
    text = pane("running_bash").replace("npm test -- filters", long)
    out = describe_running(text)
    assert out.startswith("Bash · xxx") and out.endswith("…")
    assert len(out) == len("Bash · ") + MAX_DETAIL_CHARS


def test_mcp_tool_header():
    text = pane("running_bash").replace(
        "● Bash(npm test -- filters)", "● claude_ai_Gmail - search_threads (MCP)(query: \"invoice\")")
    assert describe_running(text) == 'claude_ai_Gmail - search_threads · query: "invoice"'
    text = pane("running_bash").replace(
        "● Bash(npm test -- filters)", "● Redmine - list_issues (MCP)(project: \"ledgerly\")")
    assert describe_running(text) == 'Redmine - list_issues · project: "ledgerly"'


def test_other_background_work_is_named_as_the_tui_names_it():
    assert describe_running(pane("running_workflow")) == "Waiting for 1 dynamic workflow"
