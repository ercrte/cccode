from __future__ import annotations

import asyncio
import time
from importlib import resources
from pathlib import Path

import pytest

from mewcode.commands import CommandDispatcher, create_builtin_command_registry
from mewcode.prompting.base import RuntimePromptContext
from mewcode.prompting.builder import PromptBuilder
from mewcode.skills import LOAD_SKILL_TOOL_NAME, LoadSkillTool, SkillConfigurationError, SkillLoader, SkillManager
from mewcode.skills.models import SkillRoots
from mewcode.tools.base import ToolSpec
from mewcode.tools.base import ToolCall, ToolContext
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import create_default_registry
from mewcode.tools.scheduler import ToolPolicy


async def _collect_ticks(duration: float = 0.18, interval: float = 0.02) -> list[float]:
    started = time.monotonic()
    ticks: list[float] = []
    while time.monotonic() - started < duration:
        ticks.append(time.monotonic() - started)
        await asyncio.sleep(interval)
    return ticks


def roots(tmp_path: Path) -> SkillRoots:
    return SkillRoots(
        project=tmp_path / "project" / ".mewcode" / "skills",
        user=tmp_path / "user" / ".mewcode" / "skills",
        builtin=resources.files("mewcode.skills.builtin"),
    )


def write_skill(root: Path, name: str, description: str, tools: list[str] | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: {description}",
                "tools:",
                *[f"  - {tool}" for tool in (tools or ["read_file"])],
                "mode: shared",
                "history: 0",
                "---",
                "执行 {{input}}。",
            ]
        ),
        encoding="utf-8",
    )


def test_loader_discovers_builtin_and_priority_override(tmp_path: Path) -> None:
    skill_roots = roots(tmp_path)
    write_skill(skill_roots.user, "review", "用户级审查")
    write_skill(skill_roots.project, "review", "项目级审查")

    catalog = SkillLoader(skill_roots).discover()

    assert {"commit", "review", "test"}.issubset(catalog.definitions)
    assert catalog.definitions["review"].description == "项目级审查"
    assert catalog.definitions["review"].source_scope == "project"


def test_loader_skips_bad_single_file(tmp_path: Path) -> None:
    skill_roots = roots(tmp_path)
    skill_roots.project.mkdir(parents=True)
    (skill_roots.project / "bad.md").write_text("没有 frontmatter", encoding="utf-8")

    catalog = SkillLoader(skill_roots).discover()

    assert "bad" not in catalog.definitions
    assert catalog.warnings
    assert "frontmatter" in catalog.warnings[0].message


def test_manager_validates_missing_whitelist_tool(tmp_path: Path) -> None:
    skill_roots = roots(tmp_path)
    write_skill(skill_roots.project, "badtool", "坏工具", ["missing_tool"])
    registry = create_default_registry()
    manager = SkillManager(skill_roots, registry)
    registry.register(LoadSkillTool(manager))

    with pytest.raises(SkillConfigurationError, match="missing_tool"):
        manager.refresh_if_changed()


def test_manager_loads_and_whitelist_filters_tools(tmp_path: Path) -> None:
    skill_roots = roots(tmp_path)
    registry = create_default_registry()
    manager = SkillManager(skill_roots, registry)
    registry.register(LoadSkillTool(manager))
    manager.refresh_if_changed()

    activation = manager.load("review", "README.md")
    policy = ToolPolicy("normal", manager.active_tool_whitelist())
    allowed = {spec.name for spec in policy.allowed_specs(registry)}

    assert activation.name == "review"
    assert "README.md" in activation.rendered_body
    assert LOAD_SKILL_TOOL_NAME in allowed
    assert "read_file" in allowed
    assert "write_file" not in allowed


def test_manager_deactivate_clears_single_active_skill(tmp_path: Path) -> None:
    skill_roots = roots(tmp_path)
    write_skill(skill_roots.project, "alpha", "A")
    write_skill(skill_roots.project, "beta", "B")
    registry = create_default_registry()
    manager = SkillManager(skill_roots, registry)
    registry.register(LoadSkillTool(manager))
    manager.refresh_if_changed()
    manager.load("alpha", "1")
    manager.load("beta", "2")

    manager.deactivate("alpha")

    assert tuple(activation.name for activation in manager.prompt_context().active) == ("beta",)


@pytest.mark.asyncio
async def test_skill_commands_register_unregister_and_dispatch(tmp_path: Path) -> None:
    skill_roots = roots(tmp_path)
    registry = create_default_registry()
    manager = SkillManager(skill_roots, registry)
    registry.register(LoadSkillTool(manager))
    command_registry = create_builtin_command_registry()
    manager.refresh_if_changed(command_registry)

    assert command_registry.get("review") is not None

    dispatcher = CommandDispatcher(command_registry)
    invoked: list[dict[str, str]] = []

    class Context:
        async def invoke_skill(self, *, name: str, arguments: str, visible_text: str) -> None:
            invoked.append({"name": name, "arguments": arguments, "visible_text": visible_text})

        async def show_error(self, content: str) -> None:
            raise AssertionError(content)

    await dispatcher.dispatch("/review README.md", Context())
    assert invoked[-1]["name"] == "review"

    command_registry.unregister_origin("skills")
    assert command_registry.get("review") is None


def test_prompt_builder_includes_skill_summary_and_active_sop(tmp_path: Path) -> None:
    skill_roots = roots(tmp_path)
    registry = create_default_registry()
    manager = SkillManager(skill_roots, registry)
    registry.register(LoadSkillTool(manager))
    manager.refresh_if_changed()
    manager.load("review", "README.md")

    block = PromptBuilder().build_runtime_prompt(
        RuntimePromptContext(
            cwd=tmp_path,
            mode="normal",
            iteration=1,
            max_iterations=8,
            allowed_tools=(
                ToolSpec("read_file", "读", {"type": "object"}),
                ToolSpec(LOAD_SKILL_TOOL_NAME, "加载", {"type": "object"}, visibility="system"),
            ),
            source_request="/review README.md",
            skill_context=manager.prompt_context(),
        )
    )[-1]

    assert "<mewcode_skills>" in block.text
    assert "- review:" in block.text
    assert 'name="review"' in block.text
    assert "你正在执行内置 review Skill" in block.text
    assert block.text.index("<mewcode_skills>") < block.text.index("本轮约束")


@pytest.mark.asyncio
async def test_directory_skill_registers_local_script_tool(tmp_path: Path) -> None:
    skill_roots = roots(tmp_path)
    package = skill_roots.project / "pack"
    tools_dir = package / "tools"
    tools_dir.mkdir(parents=True)
    (package / "skill.md").write_text(
        "\n".join(
            [
                "---",
                "name: pack",
                "description: 目录包",
                "tools:",
                "  - echo",
                "mode: shared",
                "history: 0",
                "---",
                "执行目录包 {{input}}。",
            ]
        ),
        encoding="utf-8",
    )
    (tools_dir / "echo.yaml").write_text(
        "\n".join(
            [
                "name: echo",
                "description: 回显文本",
                "safety: read_only",
                "script: tools/echo.py",
                "parameters:",
                "  type: object",
                "  properties:",
                "    text:",
                "      type: string",
                "  required: [text]",
                "  additionalProperties: false",
            ]
        ),
        encoding="utf-8",
    )
    (tools_dir / "echo.py").write_text(
        "import json, sys\npayload = json.loads(sys.stdin.read() or '{}')\n"
        "print(json.dumps({'echo': payload.get('text', '')}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    registry = create_default_registry()
    manager = SkillManager(skill_roots, registry)
    registry.register(LoadSkillTool(manager))
    manager.refresh_if_changed()

    activation = manager.load("pack", "hello")
    assert activation.tool_whitelist == ("skill_pack__echo",)

    result = await ToolExecutor(registry, ToolContext(cwd=tmp_path)).execute(
        ToolCall("call-1", "skill_pack__echo", {"text": "mew"})
    )

    assert result.success is True
    assert result.data == {"echo": "mew"}


@pytest.mark.asyncio
async def test_directory_skill_script_tool_does_not_block_event_loop(tmp_path: Path) -> None:
    skill_roots = roots(tmp_path)
    package = skill_roots.project / "sleepy"
    tools_dir = package / "tools"
    tools_dir.mkdir(parents=True)
    (package / "skill.md").write_text(
        "\n".join(
            [
                "---",
                "name: sleepy",
                "description: 慢脚本",
                "tools:",
                "  - echo",
                "mode: shared",
                "history: 0",
                "---",
                "执行慢脚本 {{input}}。",
            ]
        ),
        encoding="utf-8",
    )
    (tools_dir / "echo.yaml").write_text(
        "\n".join(
            [
                "name: echo",
                "description: 慢回显文本",
                "safety: read_only",
                "script: tools/echo.py",
                "parameters:",
                "  type: object",
                "  properties:",
                "    text:",
                "      type: string",
                "  required: [text]",
                "  additionalProperties: false",
            ]
        ),
        encoding="utf-8",
    )
    (tools_dir / "echo.py").write_text(
        "import json, sys, time\npayload = json.loads(sys.stdin.read() or '{}')\n"
        "time.sleep(0.2)\n"
        "print(json.dumps({'echo': payload.get('text', '')}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    registry = create_default_registry()
    manager = SkillManager(skill_roots, registry)
    registry.register(LoadSkillTool(manager))
    manager.refresh_if_changed()
    manager.load("sleepy", "hello")

    ticker = asyncio.create_task(_collect_ticks())
    await asyncio.sleep(0)
    result = await ToolExecutor(registry, ToolContext(cwd=tmp_path)).execute(
        ToolCall("call-1", "skill_sleepy__echo", {"text": "mew"})
    )

    ticks = await ticker
    assert result.success is True
    assert result.data == {"echo": "mew"}
    assert len(ticks) >= 4
