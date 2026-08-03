from __future__ import annotations

from julycode.permissions.blacklist import DangerousCommandGuard
from julycode.permissions.models import PermissionConfig, PermissionDecision, PermissionPrompt
from julycode.permissions.rules import PermissionRuleStore, SessionPermissionRules
from julycode.permissions.sandbox import ProjectSandbox
from julycode.tools.base import ToolCall, ToolSpec


class PermissionEngine:
    def __init__(
        self,
        config: PermissionConfig,
        sandbox: ProjectSandbox,
        command_guard: DangerousCommandGuard,
        rule_store: PermissionRuleStore,
        session_rules: SessionPermissionRules,
    ) -> None:
        self.config = config
        self.sandbox = sandbox
        self.command_guard = command_guard
        self.rule_store = rule_store
        self.session_rules = session_rules

    def evaluate(self, call: ToolCall, spec: ToolSpec) -> PermissionDecision:
        if call.name == "run_command":
            denied = self.command_guard.check(str(call.arguments.get("command", "")))
            if denied is not None:
                return denied

        sandbox_denied = self.sandbox.check_tool_call(call)
        if sandbox_denied is not None:
            return sandbox_denied

        subject = self.sandbox.subject_for(call)
        for rule_set in self.rule_store.ordered_rule_sets(self.session_rules):
            match = rule_set.match(subject)
            if match is None:
                continue
            if match.rule.effect == "deny":
                return PermissionDecision(
                    kind="deny",
                    reason=f"权限规则拒绝: {match.rule.raw_key}",
                    error_type="permission_rule_denied",
                    matched_rule=match.rule,
                )
            if self.config.mode == "strict" and spec.safety == "side_effect":
                return self._prompt(call, subject.summary, f"严格模式需要确认: {match.rule.raw_key}", match.rule)
            return PermissionDecision(
                kind="allow",
                reason=f"权限规则允许: {match.rule.raw_key}",
                matched_rule=match.rule,
            )

        if spec.safety == "read_only":
            return PermissionDecision(kind="allow", reason="读类工具默认允许")
        if self.config.mode == "permissive":
            return PermissionDecision(kind="allow", reason="放行模式允许未命中的工具调用")
        return self._prompt(call, subject.summary, "有副作用工具需要用户确认", None)

    def _prompt(
        self,
        call: ToolCall,
        summary: str,
        reason: str,
        matched_rule: object | None,
    ) -> PermissionDecision:
        prompt = PermissionPrompt(
            call=call,
            tool_name=call.name,
            title=f"允许工具调用: {call.name}",
            summary=summary,
            reason=reason,
            suggested_rule_key=f"{_display_tool_name(call.name)}({summary})",
        )
        return PermissionDecision(
            kind="prompt",
            reason=reason,
            matched_rule=matched_rule,  # type: ignore[arg-type]
            prompt=prompt,
        )


def _display_tool_name(tool_name: str) -> str:
    if tool_name == "run_command":
        return "Bash"
    return tool_name
