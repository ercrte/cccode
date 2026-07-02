from __future__ import annotations

import json
import os
import asyncio
import queue
import subprocess
import sys
from collections.abc import Mapping
import threading
from typing import Any

from mewcode.skills.models import SkillToolDefinition
from mewcode.tools.base import ToolContext, ToolExecutionError, ToolSpec

LOAD_SKILL_TOOL_NAME = "load_skill"


async def _run_blocking(function: Any, *args: Any, **kwargs: Any) -> Any:
    results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def runner() -> None:
        try:
            result = function(*args, **kwargs)
        except BaseException as exc:
            results.put((False, exc))
            return
        results.put((True, result))

    threading.Thread(target=runner, name="mewcode-skill-tool", daemon=True).start()
    while True:
        try:
            ok, value = results.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.001)
            continue
        if ok:
            return value
        raise value


class LoadSkillTool:
    def __init__(self, manager: Any) -> None:
        self.manager = manager
        self.spec = ToolSpec(
            name=LOAD_SKILL_TOOL_NAME,
            description=(
                "按名称加载一个 Skill 的完整 SOP 指令和专属工具。"
                "当用户目标匹配可用 Skill 摘要时，先调用本工具再继续执行。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "要加载的 Skill 名称"},
                    "input": {"type": "string", "description": "替换到 Skill SOP 的用户输入"},
                    "args": {
                        "type": "object",
                        "description": "替换到 Skill SOP 的结构化参数，可选。",
                        "additionalProperties": True,
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            timeout_seconds=5.0,
            safety="side_effect",
            visibility="system",
            origin="system",
        )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        _ = context
        name = str(arguments["name"]).strip()
        args = arguments.get("input")
        if args is None:
            args = arguments.get("args", "")
        activation = self.manager.load(name, args)
        return {
            "name": activation.name,
            "mode": activation.mode,
            "model": activation.model,
            "tools": list(activation.tool_whitelist),
            "source_path": activation.source_path,
            "rendered_body": activation.rendered_body,
        }


class SkillScriptTool:
    def __init__(self, skill_name: str, definition: SkillToolDefinition) -> None:
        self.skill_name = skill_name
        self.definition = definition
        self.spec = ToolSpec(
            name=definition.global_name,
            description=definition.description,
            parameters_schema=definition.parameters_schema,
            timeout_seconds=definition.timeout_seconds,
            safety=definition.safety,
            origin=f"skill:{skill_name}",
        )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        payload = json.dumps(dict(arguments), ensure_ascii=False)
        env = dict(os.environ)
        env["MEWCODE_SKILL_NAME"] = self.skill_name
        env["MEWCODE_SKILL_TOOL"] = self.definition.local_name
        env["MEWCODE_SKILL_DIR"] = str(self.definition.script_path.parent.parent)
        try:
            completed = await _run_blocking(
                subprocess.run,
                [sys.executable, str(self.definition.script_path)],
                input=payload.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(context.cwd),
                env=env,
                timeout=self.definition.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout_text = (exc.stdout or b"").decode("utf-8", errors="replace").strip()
            stderr_text = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
            raise ToolExecutionError(
                f"Skill 工具脚本执行超时，超过 {self.definition.timeout_seconds:g} 秒",
                error_type="skill_tool_timeout",
                data={"stderr": stderr_text, "stdout": stdout_text},
            ) from exc

        stdout_text = completed.stdout.decode("utf-8", errors="replace").strip()
        stderr_text = completed.stderr.decode("utf-8", errors="replace").strip()
        if completed.returncode != 0:
            raise ToolExecutionError(
                f"Skill 工具脚本退出码 {completed.returncode}: {stderr_text or stdout_text}",
                error_type="skill_tool_failed",
                data={"stderr": stderr_text, "stdout": stdout_text},
            )
        if not stdout_text:
            return {}
        try:
            parsed = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            raise ToolExecutionError(
                f"Skill 工具脚本 stdout 不是合法 JSON 对象: {exc.msg}",
                error_type="skill_tool_invalid_json",
                data={"stdout": stdout_text},
            ) from exc
        if not isinstance(parsed, dict):
            raise ToolExecutionError("Skill 工具脚本 stdout 必须是 JSON 对象", error_type="skill_tool_invalid_json")
        return parsed
