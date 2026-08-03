from __future__ import annotations

from pathlib import Path

import pytest

from julycode.errors import ConfigError
from julycode.permissions import PermissionConfig, PermissionRule, PermissionSubject
from julycode.permissions.blacklist import DangerousCommandGuard
from julycode.permissions.controller import create_permission_controller
from julycode.permissions.engine import PermissionEngine
from julycode.permissions.rules import (
    PermissionRuleParser,
    PermissionRuleSet,
    PermissionRuleStore,
    SessionPermissionRules,
)
from julycode.permissions.sandbox import ProjectSandbox
from julycode.tools.base import ToolCall, ToolSpec


def write_yaml(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def spec(name: str, safety: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=name,
        parameters_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        safety=safety,  # type: ignore[arg-type]
    )


class ChoicePrompter:
    def __init__(self, choice: str) -> None:
        self.choice = choice
        self.prompts = []

    async def request_permission(self, prompt):
        self.prompts.append(prompt)
        return self.choice


def make_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str = "default",
    user_rules: str | None = None,
    project_rules: str | None = None,
    local_rules: str | None = None,
    session_rules: list[PermissionRule] | None = None,
) -> PermissionEngine:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    if user_rules is not None:
        write_yaml(home / ".julycode" / "permissions.yaml", user_rules)
    if project_rules is not None:
        write_yaml(project / ".julycode.permissions.yaml", project_rules)
    if local_rules is not None:
        write_yaml(project / ".julycode.permissions.local.yaml", local_rules)
    session = SessionPermissionRules()
    for rule in session_rules or []:
        session.add(rule)
    store = PermissionRuleStore.load(project)
    return PermissionEngine(
        PermissionConfig(mode=mode),  # type: ignore[arg-type]
        ProjectSandbox(project),
        DangerousCommandGuard(),
        store,
        session,
    )


def test_permission_models_export_defaults() -> None:
    assert PermissionConfig().mode == "default"
    rule = PermissionRule("user", "run_command", "git *", "allow", "glob", "Bash(git *)")
    subject = PermissionSubject("run_command", ("git status",), "git status")

    assert rule.source == "user"
    assert rule.tool_name == "run_command"
    assert subject.targets == ("git status",)


def test_dangerous_command_guard_blocks_high_risk_commands() -> None:
    guard = DangerousCommandGuard()
    commands = [
        "rm -rf /",
        "rm -rf ~",
        "sudo rm -rf /tmp/x",
        "mkfs.ext4 /dev/sda1",
        "dd if=/tmp/x of=/dev/sda",
        "shutdown now",
        ":(){ :|:& };:",
        "chmod -R 777 /",
        "kill -9 -1",
        "git clean -fdx",
    ]

    for command in commands:
        decision = guard.check(command)
        assert decision is not None, command
        assert decision.kind == "deny"
        assert decision.error_type == "permission_dangerous_command"


def test_dangerous_command_guard_allows_safe_commands() -> None:
    guard = DangerousCommandGuard()

    assert guard.check("git status") is None
    assert guard.check("python -m pytest -q") is None


def test_project_sandbox_allows_inside_path(tmp_path: Path) -> None:
    sandbox = ProjectSandbox(tmp_path)

    resolved = sandbox.resolve_inside("src/app.py")

    assert resolved == (tmp_path / "src/app.py").resolve(strict=False)
    assert sandbox.relative_display(resolved) == "src/app.py"


def test_project_sandbox_rejects_parent_escape(tmp_path: Path) -> None:
    sandbox = ProjectSandbox(tmp_path)

    with pytest.raises(PermissionError):
        sandbox.resolve_inside("../outside.txt")


def test_project_sandbox_rejects_absolute_escape(tmp_path: Path) -> None:
    sandbox = ProjectSandbox(tmp_path)

    with pytest.raises(PermissionError):
        sandbox.resolve_inside("/etc/passwd")


def test_project_sandbox_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(outside)
    sandbox = ProjectSandbox(tmp_path)

    with pytest.raises(PermissionError):
        sandbox.resolve_inside("link.txt")


def test_project_sandbox_builds_subject_for_core_tools(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print('ok')", encoding="utf-8")
    sandbox = ProjectSandbox(tmp_path)

    assert sandbox.subject_for(ToolCall("c1", "read_file", {"path": "src/app.py"})).targets == ("src/app.py",)
    assert sandbox.subject_for(ToolCall("c2", "write_file", {"path": "src/new.py"})).targets == ("src/new.py",)
    assert sandbox.subject_for(ToolCall("c3", "edit_file", {"path": "src/app.py"})).targets == ("src/app.py",)
    assert sandbox.subject_for(ToolCall("c4", "run_command", {"command": " git   status "})).targets == ("git status",)
    assert sandbox.subject_for(ToolCall("c5", "find_files", {"pattern": "src/*.py"})).targets == ("src/*.py",)
    assert sandbox.subject_for(ToolCall("c6", "search_code", {"path": "src", "glob": "*.py"})).targets == (
        "src",
        "src *.py",
        "*.py",
    )


def test_project_sandbox_read_file_range_subject_for_keeps_path_only(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("one\ntwo\n", encoding="utf-8")
    sandbox = ProjectSandbox(tmp_path)
    call = ToolCall(
        "c1",
        "read_file",
        {"path": "src/app.py", "offset": 2, "limit": 1},
    )

    assert sandbox.check_tool_call(call) is None
    assert sandbox.subject_for(call).targets == ("src/app.py",)
    assert sandbox.subject_for(call).summary == "src/app.py"


def test_project_sandbox_allows_explicit_search_code_file_and_directory(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("", encoding="utf-8")
    sandbox = ProjectSandbox(tmp_path)

    assert sandbox.check_tool_call(
        ToolCall("c1", "search_code", {"pattern": "x", "path": "src"})
    ) is None
    assert sandbox.check_tool_call(
        ToolCall("c2", "search_code", {"pattern": "x", "path": "src/app.py"})
    ) is None


def test_project_sandbox_rejects_find_files_escape(tmp_path: Path) -> None:
    sandbox = ProjectSandbox(tmp_path)

    decision = sandbox.check_tool_call(ToolCall("c1", "find_files", {"pattern": "../*.py"}))

    assert decision is not None
    assert decision.error_type == "permission_sandbox_violation"


def test_project_sandbox_find_files_check_does_not_use_unbounded_path_glob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = ProjectSandbox(tmp_path)

    def fail_glob(self: Path, pattern: str):
        _ = self, pattern
        raise AssertionError("权限预检查不应扫描整个项目")

    monkeypatch.setattr(Path, "glob", fail_glob)

    assert sandbox.check_tool_call(
        ToolCall("c1", "find_files", {"pattern": "**/*.py"})
    ) is None


def test_project_sandbox_rejects_find_files_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("", encoding="utf-8")
    try:
        (tmp_path / "linked.py").symlink_to(outside)
    except OSError:
        pytest.skip("当前平台不允许创建符号链接")
    sandbox = ProjectSandbox(tmp_path)

    decision = sandbox.check_tool_call(
        ToolCall("c1", "find_files", {"pattern": "**/*.py"})
    )

    assert decision is not None
    assert decision.error_type == "permission_sandbox_violation"


def test_project_sandbox_rejects_search_path_escape(tmp_path: Path) -> None:
    sandbox = ProjectSandbox(tmp_path)

    decision = sandbox.check_tool_call(ToolCall("c1", "search_code", {"pattern": "x", "path": "../"}))

    assert decision is not None
    assert decision.error_type == "permission_sandbox_violation"


def test_file_tool_cannot_escape_project_sandbox(tmp_path: Path) -> None:
    sandbox = ProjectSandbox(tmp_path)

    decision = sandbox.check_tool_call(ToolCall("c1", "read_file", {"path": "../secret.txt"}))

    assert decision is not None
    assert decision.error_type == "permission_sandbox_violation"


def test_permission_rule_parser_parses_exact_and_glob() -> None:
    parser = PermissionRuleParser()

    exact = parser.parse_rule_key("read_file(README.md)", "project", "allow")
    glob = parser.parse_rule_key("write_file(src/**/*.py)", "project", "deny")

    assert exact.match_kind == "exact"
    assert exact.effect == "allow"
    assert glob.match_kind == "glob"
    assert glob.effect == "deny"


def test_permission_rule_parser_normalizes_bash_alias() -> None:
    rule = PermissionRuleParser().parse_rule_key("Bash(git *)", "user", "allow")

    assert rule.tool_name == "run_command"
    assert rule.pattern == "git *"


def test_permission_rule_parser_rejects_invalid_rules() -> None:
    parser = PermissionRuleParser()

    with pytest.raises(ConfigError):
        parser.parse_rule_key("Bash git *", "user", "allow")
    with pytest.raises(ConfigError):
        parser.parse_rule_key("Bash()", "user", "allow")
    with pytest.raises(ConfigError):
        parser.parse_rule_key("Bash(git *)", "user", "maybe")


def test_permission_rule_set_prefers_exact_match() -> None:
    rules = PermissionRuleSet(
        "project",
        (
            PermissionRule("project", "run_command", "git *", "deny", "glob", "Bash(git *)"),
            PermissionRule("project", "run_command", "git status", "allow", "exact", "Bash(git status)"),
        ),
    )

    match = rules.match(PermissionSubject("run_command", ("git status",), "git status"))

    assert match is not None
    assert match.rule.effect == "allow"


def test_permission_rule_set_prefers_deny_on_equal_match() -> None:
    rules = PermissionRuleSet(
        "project",
        (
            PermissionRule("project", "run_command", "git *", "allow", "glob", "Bash(git *)"),
            PermissionRule("project", "run_command", "git *", "deny", "glob", "Bash(git *)"),
        ),
    )

    match = rules.match(PermissionSubject("run_command", ("git status",), "git status"))

    assert match is not None
    assert match.rule.effect == "deny"


def test_permission_rule_set_matches_any_subject_target() -> None:
    rules = PermissionRuleSet(
        "project",
        (PermissionRule("project", "search_code", "*.py", "allow", "glob", "search_code(*.py)"),),
    )

    match = rules.match(PermissionSubject("search_code", ("src", "src *.py", "*.py"), "src *.py"))

    assert match is not None
    assert match.target in {"src *.py", "*.py"}


def test_permission_rules_support_regex_and_negation() -> None:
    rules = PermissionRuleSet(
        "project",
        (
            PermissionRule(
                "project",
                "run_command",
                "regex:^git\\s+status$",
                "allow",
                "regex",
                "Bash(regex:^git\\s+status$)",
            ),
            PermissionRule("project", "run_command", "!regex:^rm\\b", "allow", "regex", "Bash(!regex:^rm\\b)"),
        ),
    )

    regex_match = rules.match(PermissionSubject("run_command", ("git status",), "git status"))
    negated_match = rules.match(PermissionSubject("run_command", ("python -V",), "python -V"))
    denied_by_negation = rules.match(PermissionSubject("run_command", ("rm file",), "rm file"))

    assert regex_match is not None
    assert regex_match.rule.pattern == "regex:^git\\s+status$"
    assert negated_match is not None
    assert negated_match.rule.pattern == "!regex:^rm\\b"
    assert denied_by_negation is None


def test_permission_rule_store_loads_missing_files_as_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()

    store = PermissionRuleStore.load(project)

    assert all(not rule_set.rules for rule_set in store.ordered_rule_sets(SessionPermissionRules()))


def test_permission_rule_store_orders_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    write_yaml(home / ".julycode" / "permissions.yaml", 'rules:\n  "Bash(git *)": allow\n')
    write_yaml(project / ".julycode.permissions.yaml", 'rules:\n  "Bash(git *)": deny\n')
    write_yaml(project / ".julycode.permissions.local.yaml", 'rules:\n  "Bash(git status)": allow\n')
    session = SessionPermissionRules()
    session.add(PermissionRule("session", "run_command", "git status", "deny", "exact", "Bash(git status)"))

    sources = [rule_set.source for rule_set in PermissionRuleStore.load(project).ordered_rule_sets(session)]

    assert sources == ["session", "local", "project", "user"]


def test_permission_rule_store_rejects_invalid_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(project / ".julycode.permissions.yaml", "[]\n")

    with pytest.raises(ConfigError, match="顶层"):
        PermissionRuleStore.load(project)


def test_permission_rule_store_writes_local_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    store = PermissionRuleStore.load(project)

    store.add_local_rule(PermissionRule("local", "run_command", "git status", "allow", "exact", "Bash(git status)"))

    assert "Bash(git status): allow" in (project / ".julycode.permissions.local.yaml").read_text(encoding="utf-8")


def test_permission_engine_dangerous_command_overrides_allow_rule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = make_engine(
        tmp_path,
        monkeypatch,
        mode="permissive",
        local_rules='rules:\n  "Bash(rm -rf /)": allow\n',
    )

    decision = engine.evaluate(ToolCall("c1", "run_command", {"command": "rm -rf /"}), spec("run_command", "side_effect"))

    assert decision.kind == "deny"
    assert decision.error_type == "permission_dangerous_command"


def test_permission_engine_sandbox_overrides_allow_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(
        tmp_path,
        monkeypatch,
        mode="permissive",
        local_rules='rules:\n  "read_file(*)": allow\n',
    )

    decision = engine.evaluate(ToolCall("c1", "read_file", {"path": "../outside.txt"}), spec("read_file", "read_only"))

    assert decision.kind == "deny"
    assert decision.error_type == "permission_sandbox_violation"


def test_permission_engine_uses_highest_priority_rule_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parser = PermissionRuleParser()
    engine = make_engine(
        tmp_path,
        monkeypatch,
        user_rules='rules:\n  "Bash(git status)": allow\n',
        project_rules='rules:\n  "Bash(git status)": deny\n',
    )
    call = ToolCall("c1", "run_command", {"command": "git status"})

    assert engine.evaluate(call, spec("run_command", "side_effect")).matched_rule.source == "project"
    engine = make_engine(
        tmp_path,
        monkeypatch,
        user_rules='rules:\n  "Bash(git status)": allow\n',
        project_rules='rules:\n  "Bash(git status)": deny\n',
        local_rules='rules:\n  "Bash(git status)": allow\n',
    )
    assert engine.evaluate(call, spec("run_command", "side_effect")).matched_rule.source == "local"
    engine.session_rules.add(parser.parse_rule_key("Bash(git status)", "session", "deny"))
    decision = engine.evaluate(call, spec("run_command", "side_effect"))
    assert decision.kind == "deny"
    assert decision.matched_rule.source == "session"


def test_permission_engine_default_mode_decisions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(
        tmp_path,
        monkeypatch,
        project_rules='rules:\n  "Bash(git status)": allow\n  "Bash(git push)": deny\n',
    )

    assert engine.evaluate(ToolCall("c1", "read_file", {"path": "README.md"}), spec("read_file", "read_only")).kind == "allow"
    assert engine.evaluate(ToolCall("c2", "run_command", {"command": "git status"}), spec("run_command", "side_effect")).kind == "allow"
    assert engine.evaluate(ToolCall("c3", "run_command", {"command": "git push"}), spec("run_command", "side_effect")).kind == "deny"
    assert engine.evaluate(ToolCall("c4", "run_command", {"command": "python -V"}), spec("run_command", "side_effect")).kind == "prompt"


def test_permission_engine_strict_mode_prompts_side_effect_even_when_allowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = make_engine(
        tmp_path,
        monkeypatch,
        mode="strict",
        project_rules='rules:\n  "Bash(git status)": allow\n',
    )

    decision = engine.evaluate(ToolCall("c1", "run_command", {"command": "git status"}), spec("run_command", "side_effect"))

    assert decision.kind == "prompt"


def test_permission_engine_permissive_mode_allows_unmatched_but_respects_deny(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = make_engine(
        tmp_path,
        monkeypatch,
        mode="permissive",
        project_rules='rules:\n  "Bash(git push)": deny\n',
    )

    assert engine.evaluate(ToolCall("c1", "run_command", {"command": "python -V"}), spec("run_command", "side_effect")).kind == "allow"
    assert engine.evaluate(ToolCall("c2", "run_command", {"command": "git push"}), spec("run_command", "side_effect")).kind == "deny"


def test_permission_controller_turns_denial_into_tool_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()
    controller = create_permission_controller(project, PermissionConfig(mode="permissive"))
    decision = controller.evaluate(ToolCall("c1", "run_command", {"command": "rm -rf /"}), spec("run_command", "side_effect"))

    result = controller.denial_result(ToolCall("c1", "run_command", {"command": "rm -rf /"}), decision)

    assert result.success is False
    assert result.error_type == "permission_dangerous_command"
    assert "命令命中高危黑名单" in (result.error or "")


@pytest.mark.asyncio
async def test_permission_controller_resolves_allow_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()
    prompter = ChoicePrompter("allow_once")
    controller = create_permission_controller(project, PermissionConfig(), prompter)
    prompt = controller.evaluate(ToolCall("c1", "run_command", {"command": "python -V"}), spec("run_command", "side_effect")).prompt

    decision = await controller.resolve_prompt(prompt)

    assert decision.kind == "allow"
    assert not (project / ".julycode.permissions.local.yaml").exists()


@pytest.mark.asyncio
async def test_permission_controller_adds_session_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()
    controller = create_permission_controller(project, PermissionConfig(), ChoicePrompter("allow_session"))
    call = ToolCall("c1", "run_command", {"command": "python -V"})
    prompt = controller.evaluate(call, spec("run_command", "side_effect")).prompt

    assert (await controller.resolve_prompt(prompt)).kind == "allow"
    assert controller.evaluate(call, spec("run_command", "side_effect")).kind == "allow"


@pytest.mark.asyncio
async def test_permission_controller_persists_local_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()
    controller = create_permission_controller(project, PermissionConfig(), ChoicePrompter("allow_permanent"))
    prompt = controller.evaluate(ToolCall("c1", "run_command", {"command": "python -V"}), spec("run_command", "side_effect")).prompt

    decision = await controller.resolve_prompt(prompt)

    assert decision.kind == "allow"
    assert "Bash(python -V): allow" in (project / ".julycode.permissions.local.yaml").read_text(encoding="utf-8")


def test_permission_controller_denies_when_prompt_has_no_prompter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()
    controller = create_permission_controller(project, PermissionConfig())

    decision = controller.evaluate(ToolCall("c1", "run_command", {"command": "python -V"}), spec("run_command", "side_effect"))

    assert decision.kind == "deny"
    assert decision.error_type == "permission_confirmation_required"
