from __future__ import annotations

import json

from mewcode.memory.extraction import (
    MemoryCandidateValidator,
    MemoryExtractionError,
    parse_memory_candidates,
)
from mewcode.memory.index import MemoryIndexBuilder
from mewcode.memory.models import MemoryExtractionResult, MemoryUpdateJob, MemoryScope
from mewcode.memory.notes import MemoryNoteStore
from mewcode.providers.base import ChatMessage, ChatRequest, LLMProvider


# 保持既有公开异常名兼容，同时避免 extraction 与 updater 循环导入。
MemoryUpdateError = MemoryExtractionError


class MemoryNoteUpdater:
    def __init__(
        self,
        note_store: MemoryNoteStore,
        index_builder: MemoryIndexBuilder,
        *,
        validator: MemoryCandidateValidator | None = None,
    ) -> None:
        self.note_store = note_store
        self.index_builder = index_builder
        self.validator = validator or MemoryCandidateValidator(note_store, index_builder.config)

    async def extract(self, *, job: MemoryUpdateJob, provider: LLMProvider) -> MemoryExtractionResult:
        request = ChatRequest(messages=(ChatMessage(role="user", content=self._prompt(job)),), tools=())
        text_parts: list[str] = []
        final_message: ChatMessage | None = None
        try:
            async for event in provider.stream_chat(request):
                if event.type == "text_delta":
                    text_parts.append(event.text)
                elif event.type == "message_done":
                    final_message = event.message
                elif event.type == "error":
                    raise MemoryUpdateError(event.error or "自动记忆更新失败")
        except MemoryUpdateError:
            raise
        except Exception as exc:
            raise MemoryUpdateError(f"自动记忆更新失败: {exc}") from exc

        if final_message is not None and final_message.tool_calls:
            raise MemoryUpdateError("自动记忆更新请求中模型尝试调用工具")
        raw_text = (final_message.content if final_message is not None else "") or "".join(text_parts)
        candidates = parse_memory_candidates(raw_text)
        return self.validator.validate(candidates, job)

    def apply(self, result: MemoryExtractionResult):
        affected_scopes: set[MemoryScope] = set()
        for operation in result.accepted:
            self.note_store.write_note(operation.note)
            for note_id in operation.supersedes:
                self.note_store.delete_note(operation.note.scope, note_id)
            affected_scopes.add(operation.note.scope)
        return tuple(self.index_builder.build(scope) for scope in sorted(affected_scopes))

    async def update(self, *, job: MemoryUpdateJob, provider: LLMProvider):
        result = await self.extract(job=job, provider=provider)
        return self.apply(result)

    def _prompt(self, job: MemoryUpdateJob) -> str:
        payload = {
            "session_id": str(job.session_id),
            "turn_messages": [
                {"role": message.role, "content": message.content}
                for message in job.turn_messages
            ],
            "final_message": job.final_message.content,
            "user_memory_index": (
                job.knowledge_context.user_memory_index.content
                if job.knowledge_context.user_memory_index is not None
                else ""
            ),
            "project_memory_index": (
                job.knowledge_context.project_memory_index.content
                if job.knowledge_context.project_memory_index is not None
                else ""
            ),
        }
        return (
            "你正在为 MewCode 提取可跨会话使用的长期记忆。禁止调用任何工具。\n"
            "只返回 JSON，不要输出 Markdown。格式为 {\"operations\": [...]}。\n\n"
            # ── category 分类指南 ──
            "## 记忆类别（category）判断规则\n\n"
            "### preference（用户偏好）\n"
            "定义：用户首次表达或补充的跨任务行为偏好、工作风格、默认选择。\n"
            "典型触发词：以后、今后、始终、默认、每次、必须、禁止、不要再、一律\n"
            "正例：\n"
            '- "以后始终使用中文回答" → preference（首次表达语言偏好）\n'
            '- "默认使用 pytest 进行测试" → preference（首次表达工具偏好）\n'
            "反例（不是 preference）：\n"
            '- "纠正一下，不要再用 unittest" → correction（明确纠正了之前说法）\n'
            '- "本项目使用 Python 3.11" → project_knowledge（描述项目事实）\n\n'
            "### correction（纠正反馈）\n"
            "定义：用户明确纠正/覆盖之前说过的偏好或规则。必须包含纠正信号词。\n"
            "中文典型触发词：纠正、不要再用、别再、改一下、不是...而是、不再、改成\n"
            "英文典型触发词：again、anymore、no longer、instead、correcting、correction、not ... anymore\n"
            "正确判断：同时包含「纠正信号词」+「持续性标记词」(以后/今后/from now on/always) → correction\n"
            "正例：\n"
            '- "纠正之前的偏好，今后禁止使用表情符号" → correction（纠正信号+持续性标记）\n'
            '- "不要再用英文回答，以后始终改用中文" → correction（不要再用+以后始终）\n'
            '- "From now on, do not use unittest; always use pytest" → correction（含"From now on"暗示转换）\n'
            '- "Never auto-format files again" → correction（含"again"纠正信号）\n'
            '- "Do not skip type checks again; always run them" → correction（含"again"）\n'
            "反例（不是 correction）：\n"
            '- "以后禁止在回复里使用 emoji" → preference（首次表达，无纠正信号词）\n'
            '- "今后默认使用绝对路径报告文件位置" → preference（首次表达）\n'
            '- "Always use pytest for tests" → preference（无纠正信号词，只是陈述偏好）\n\n'
            "### project_knowledge（项目知识）\n"
            "定义：项目的事实性信息、技术决策、版本要求、架构约定。描述「是什么」。\n"
            "正例：\n"
            '- "本项目使用 Python 3.11" → scope=project, project_knowledge, critical=False\n'
            '- "生产数据库使用 PostgreSQL" → scope=project, project_knowledge, critical=False\n'
            '- "所有数据库迁移必须可逆" → scope=project, project_knowledge, critical=True（含「必须」的硬约束）\n\n'
            "### reference（参考资料）\n"
            "定义：指向文件/文档/外部资源的路径或位置指针。描述「在哪里」。\n"
            "正例：\n"
            '- "架构说明入口是 docs/architecture.md" → reference\n'
            '- "ADR 存放在 docs/adr 目录下" → reference\n'
            '- "API 合约文档在 openapi.yaml" → reference\n'
            '- "常用测试命令是 pytest -q" → reference（指向具体命令/路径）\n'
            '- "配置文件名是 .mewcode.yaml" → reference（指向具体文件名）\n'
            '- "代码风格依据 pyproject.toml" → reference（指向具体文件）\n'
            '区分：「X 的文档/命令/配置在 Y」是 reference，「本项目使用 X」是 project_knowledge。\n\n'
            # ── scope 分类指南 ──
            "## 作用域（scope）判断规则\n\n"
            "### user\n"
            "跨项目通用的个人偏好和纠正反馈。跟人走，不跟项目走。\n"
            "正例：「以后始终使用中文回答」「默认使用 pytest」「禁止自动提交代码」\n\n"
            "### project\n"
            "当前项目的技术事实、约定、决策和参考资源。跟项目走。\n"
            '判断信号：出现"本项目"、"请长期记住"且内容涉及技术栈/版本/路径/架构。\n'
            '正例：「本项目使用 Python 3.11」「请长期记住：Web 框架是 FastAPI」\n\n'
            # ── critical 判断指南 ──
            "## 关键偏好（critical）判断规则\n\n"
            "**critical=True 必须同时满足以下所有条件：**\n"
            "1. 用户明确表达了跨任务持续生效的**行为约束**（必须做什么/禁止做什么）\n"
            "2. 证据包含强制性标记词：必须、禁止、决不能、never、must、always、do not\n"
            "3. confidence >= 0.95\n\n"
            "**critical=False 的情况（重要！大部分记忆都不是 critical）：**\n"
            "- 项目事实信息（版本号、使用的工具/库/框架名称）→ 不是行为约束，critical=False\n"
            '- 文档/文件/配置路径指针 → reference 类别，critical=False\n'
            '- "请长期记住"但没有强制性标记词（必须/禁止/must/never）的内容 → critical=False\n'
            '- 纯描述性信息（"X 使用 Y"、"Z 的路径是 W"）→ 就算有"请长期记住"也不是 critical\n\n'
            "哪些类别可以标记 critical：\n"
            "- preference：可以（如「必须使用中文」「禁止自动提交代码」）\n"
            "- correction：可以（如「不要再使用 unittest，必须用 pytest」）\n"
            "- project_knowledge：仅当含「必须/禁止/must/never」等强约束词时可以（如「所有迁移必须可逆」）\n"
            "- reference：不可以（路径指针不是行为约束）\n\n"
            "正例 (critical=True)：\n"
            '- "所有数据库迁移必须可逆" → 含"必须"，硬性约束，critical=True\n'
            '- "禁止使用 git reset --hard" → 含"禁止"，硬性约束，critical=True\n'
            '- "You must keep public APIs backward compatible" → 含"must"，critical=True\n\n'
            "反例 (critical=False)：\n"
            '- "本项目使用 Python 3.11" → 项目事实，没有强制词，critical=False\n'
            '- "Web 框架是 FastAPI" → 项目事实，critical=False\n'
            '- "请长期记住：缓存服务使用 Redis" → 纯事实，没有强制词，critical=False\n'
            '- "请长期记住：包管理器统一使用 uv" → 纯事实，没有强制词，critical=False\n'
            '- "架构说明入口是 docs/architecture.md" → 路径指针，reference 类别，critical=False\n\n'
            # ── JSON 格式与字段说明 ──
            "## JSON 格式\n\n"
            "operation.action 只能是 create、update、skip。skip 不需要其他字段。\n"
            "非 skip 必须包含 scope、category、note_id、title、body、evidence、durability、"
            "critical、confidence、tags、supersedes。\n"
            "evidence 必须是 turn_messages 中 role=user 消息的逐字原文数组；"
            "助手回复和工具输出不能作为用户偏好或纠正的证据。\n"
            "durability 只能是 persistent、temporary、uncertain；只有明确跨任务持续有效的信息才是 persistent。\n"
            "confidence 是 0 到 1 的数字。\n\n"
            "## 必须 skip 的情况\n\n"
            "临时格式、一次性任务、短期进度、模型猜测、闲聊、敏感凭据和只来自助手或工具的内容必须 skip。\n"
            "重复事实使用 update 或 skip；明确纠正旧规则时在 supersedes 中列出被替代的既有 note_id。\n\n"
            f"{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
        )


__all__ = ["MemoryNoteUpdater", "MemoryUpdateError"]
