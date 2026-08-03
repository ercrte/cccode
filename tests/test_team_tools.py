from __future__ import annotations

from pathlib import Path

import pytest

from julycode.teams.manager import TeamManager
from julycode.teams.models import MessageDraft, TeamActor, TeamDataError, TeamMemberRecord
from julycode.teams.policy import ApprovalGate, TeamAudienceGate, TeamMemberRoleGate
from julycode.teams.store import TeamStore
from julycode.teams.tools import create_team_tools
from julycode.tools.base import RuntimePrincipal, ToolCall, ToolContext, ToolSpec
from julycode.tools.executor import ToolExecutor
from julycode.tools.registry import ToolRegistry
from julycode.tools.scheduler import ToolPolicy
from tests.test_worktrees import init_repository


def setup_manager(tmp_path: Path):
    repo = init_repository(tmp_path / "repo")
    store = TeamStore(repo, root=tmp_path / "teams")
    manager = TeamManager(repo, store=store)
    registry = ToolRegistry()
    for tool in create_team_tools(manager):
        registry.register(tool)
    return repo, manager, registry


@pytest.mark.asyncio
async def test_manage_team_tool_create_list_and_status(tmp_path: Path) -> None:
    repo, _manager, registry = setup_manager(tmp_path)
    executor = ToolExecutor(registry, ToolContext(repo))
    created = await executor.execute(ToolCall("1", "manage_team", {"action": "create", "name": "demo"}))
    assert created.success
    status = await executor.execute(ToolCall("2", "manage_team", {"action": "status"}))
    assert status.success and status.data["team"]["name"] == "demo"


@pytest.mark.asyncio
async def test_team_task_tool_crud(tmp_path: Path) -> None:
    repo, manager, registry = setup_manager(tmp_path)
    await manager.create_team("demo")
    executor = ToolExecutor(registry, ToolContext(repo))
    created = await executor.execute(ToolCall("1", "team_task", {"action": "create", "title": "work", "kind": "research"}))
    assert created.success
    listed = await executor.execute(ToolCall("2", "team_task", {"action": "list"}))
    assert listed.success and listed.data["tasks"][0]["title"] == "work"


def test_audience_gate_filters_team_tools(tmp_path: Path) -> None:
    _repo, manager, registry = setup_manager(tmp_path)
    lifecycle = ToolPolicy("normal", gates=(TeamAudienceGate(RuntimePrincipal(), lambda: None),))
    assert {spec.name for spec in lifecycle.allowed_specs(registry)} == {"manage_team"}
    lead = ToolPolicy("normal", gates=(TeamAudienceGate(RuntimePrincipal(), lambda: "demo"),))
    assert {spec.name for spec in lead.allowed_specs(registry)} == {
        "manage_team", "manage_team_member", "team_task", "team_message", "team_wait"
    }
    member = ToolPolicy(
        "normal",
        gates=(TeamAudienceGate(RuntimePrincipal("team_member", "demo", "worker"), lambda: "demo"),),
    )
    assert {spec.name for spec in member.allowed_specs(registry)} == {"team_task", "team_message"}
    sub_agent = ToolPolicy("normal", gates=(TeamAudienceGate(RuntimePrincipal("sub_agent"), lambda: "demo"),))
    assert sub_agent.allowed_specs(registry) == ()
    disabled = ToolPolicy(
        "normal",
        gates=(TeamAudienceGate(RuntimePrincipal(), lambda: None, lambda: False),),
    )
    assert disabled.allowed_specs(registry) == ()


def test_team_tool_set_has_stable_names(tmp_path: Path) -> None:
    _repo, manager, _registry = setup_manager(tmp_path)
    tools = create_team_tools(manager)
    assert [tool.spec.name for tool in tools] == [
        "manage_team", "manage_team_member", "team_task", "team_message", "team_wait"
    ]
    assert all(tool.spec.origin == "teams" for tool in tools)


def test_member_role_and_approval_gates_enforce_runtime_policy() -> None:
    read = ToolSpec("read_file", "read", {}, safety="read_only")
    write = ToolSpec("write_file", "write", {}, safety="side_effect")
    task = ToolSpec("team_task", "task", {}, safety="side_effect", origin="teams")
    delegate = ToolSpec("delegate_agent", "delegate", {}, safety="side_effect")
    role = TeamMemberRoleGate(frozenset({"read_file", "write_file"}), frozenset({"write_file"}))

    assert role.allows(read)
    assert role.allows(task)
    assert not role.allows(write)
    assert not role.allows(delegate)
    approval = ApprovalGate(lambda: False)
    assert approval.allows(read)
    assert approval.allows(task)
    assert not approval.allows(write)
    assert ApprovalGate(lambda: True).allows(write)


@pytest.mark.asyncio
async def test_actor_resolution_uses_principal_and_roster(tmp_path: Path) -> None:
    repo, manager, _registry = setup_manager(tmp_path)
    await manager.create_team("demo")
    now = "2026-01-01T00:00:00+00:00"
    member = TeamMemberRecord(
        "worker", "reviewer", "coroutine", False, "idle", str(repo), str(repo), "branch",
        "owner", str(manager.store.root / "demo/sessions/worker.jsonl"), None, None, now, now, now,
    )
    await manager.store.add_member("demo", member)

    actor = await manager.actor_for(RuntimePrincipal("team_member", "demo", "worker"))
    assert actor == TeamActor("demo", "worker", "member", repo)
    with pytest.raises(TeamDataError, match="未知团队成员"):
        await manager.actor_for(RuntimePrincipal("team_member", "demo", "forged"))


def test_message_tool_schema_does_not_accept_sender_or_team(tmp_path: Path) -> None:
    _repo, manager, _registry = setup_manager(tmp_path)
    message_tool = next(tool for tool in create_team_tools(manager) if tool.spec.name == "team_message")
    properties = message_tool.spec.parameters_schema["properties"]
    assert "sender" not in properties
    assert "team" not in properties


@pytest.mark.asyncio
async def test_message_tool_read_acknowledges_mailbox(tmp_path: Path) -> None:
    repo, manager, registry = setup_manager(tmp_path)
    await manager.create_team("demo")
    lead = TeamActor("demo", "lead", "lead", repo)
    await manager._service("demo").mailbox.send(lead, MessageDraft("lead", "message", "event"))
    executor = ToolExecutor(registry, ToolContext(repo))

    read = await executor.execute(ToolCall("1", "team_message", {"action": "read"}))

    assert read.success and len(read.data["messages"]) == 1
    assert await manager._service("demo").mailbox.unread(lead) == ()
