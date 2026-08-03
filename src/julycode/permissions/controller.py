from __future__ import annotations

import time
from pathlib import Path

from julycode.errors import redact_secret
from julycode.permissions.blacklist import DangerousCommandGuard
from julycode.permissions.engine import PermissionEngine
from julycode.permissions.models import PermissionConfig, PermissionDecision, PermissionPrompter, PermissionPrompt
from julycode.permissions.rules import PermissionRuleParser, PermissionRuleStore, SessionPermissionRules
from julycode.permissions.sandbox import ProjectSandbox
from julycode.tools.base import ToolCall, ToolResult, ToolSpec


class PermissionController:
    def __init__(
        self,
        config: PermissionConfig,
        engine: PermissionEngine,
        session_rules: SessionPermissionRules,
        rule_store: PermissionRuleStore,
        prompter: PermissionPrompter | None = None,
    ) -> None:
        self.config = config
        self.engine = engine
        self.session_rules = session_rules
        self.rule_store = rule_store
        self.prompter = prompter
        self.parser = PermissionRuleParser()

    def evaluate(self, call: ToolCall, spec: ToolSpec) -> PermissionDecision:
        decision = self.engine.evaluate(call, spec)
        if decision.kind == "prompt" and self.prompter is None:
            return PermissionDecision(
                kind="deny",
                reason="当前环境无法进行权限确认",
                error_type="permission_confirmation_required",
                matched_rule=decision.matched_rule,
            )
        return decision

    async def resolve_prompt(self, prompt: PermissionPrompt) -> PermissionDecision:
        if self.prompter is None:
            return PermissionDecision(
                kind="deny",
                reason="当前环境无法进行权限确认",
                error_type="permission_confirmation_required",
            )
        choice = await self.prompter.request_permission(prompt)
        if choice == "allow_once":
            return PermissionDecision(kind="allow", reason="用户本次允许")
        if choice == "allow_session":
            rule = self.parser.parse_rule_key(prompt.suggested_rule_key, "session", "allow")
            self.session_rules.add(rule)
            return PermissionDecision(kind="allow", reason="用户本会话允许", matched_rule=rule)
        if choice == "allow_permanent":
            try:
                rule = self.parser.parse_rule_key(prompt.suggested_rule_key, "local", "allow")
                self.rule_store.add_local_rule(rule)
            except Exception as exc:
                return PermissionDecision(
                    kind="deny",
                    reason=f"持久化权限规则失败: {exc}",
                    error_type="permission_persist_failed",
                )
            return PermissionDecision(kind="allow", reason="用户永久允许", matched_rule=rule)
        return PermissionDecision(
            kind="deny",
            reason="用户拒绝工具调用",
            error_type="permission_user_denied",
        )

    def denial_result(self, call: ToolCall, decision: PermissionDecision) -> ToolResult:
        started = time.monotonic()
        data = {"reason": redact_secret(decision.reason)}
        if decision.matched_rule is not None:
            data["matched_rule"] = decision.matched_rule.raw_key
            data["rule_source"] = decision.matched_rule.source
        return ToolResult(
            tool_call_id=call.id,
            tool_name=call.name,
            success=False,
            data=data,
            error_type=decision.error_type or "permission_denied",
            error=redact_secret(decision.reason),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )


def create_permission_controller(
    cwd: Path,
    config: PermissionConfig,
    prompter: PermissionPrompter | None = None,
) -> PermissionController:
    session_rules = SessionPermissionRules()
    rule_store = PermissionRuleStore.load(cwd)
    engine = PermissionEngine(
        config=config,
        sandbox=ProjectSandbox(cwd),
        command_guard=DangerousCommandGuard(),
        rule_store=rule_store,
        session_rules=session_rules,
    )
    return PermissionController(config, engine, session_rules, rule_store, prompter)
