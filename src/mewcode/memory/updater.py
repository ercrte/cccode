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
            '- "From now on, do not use unittest; always use pytest" → correction（From now on+never表示行为转换）\n'
            '- "Never auto-format files again" → correction（never+again=纠正）\n'
            '- "Do not skip type checks again; always run them" → correction（again=纠正）\n'
            '- "From now on, never amend an existing commit" → correction（From now on+never=行为转换）\n'
            "反例（不是 correction）：\n"
            '- "以后禁止在回复里使用 emoji" → preference（首次表达，无纠正信号词）\n'
            '- "今后默认使用绝对路径报告文件位置" → preference（首次表达，无纠正信号词）\n'
            '- "Always use pytest for tests" → preference（无纠正信号词，首次表达）\n\n'
            "### project_knowledge（项目知识）\n"
            "定义：项目的事实性信息、技术决策、版本要求、架构约定。描述「是什么」。\n"
            "正例：\n"
            '- "本项目使用 Python 3.11" → scope=project, project_knowledge, critical=False\n'
            '- "生产数据库使用 PostgreSQL" → scope=project, project_knowledge, critical=False\n'
            '- "所有数据库迁移必须可逆" → scope=project, project_knowledge, critical=True（含「必须」的硬约束）\n\n'
            "### reference（参考资料）\n"
            "定义：指向文件/文档/外部资源的路径或位置指针。描述「在哪里能找到」。\n"
            "正例：\n"
            '- "架构说明入口是 docs/architecture.md" → reference（指向文件路径）\n'
            '- "ADR 存放在 docs/adr 目录下" → reference（指向目录路径）\n'
            '- "API 合约文档在 openapi.yaml" → reference（指向文件名）\n'
            '- "常用测试命令是 pytest -q" → reference（指向具体命令）\n'
            '- "安全规范入口是 SECURITY.md" → reference（指向文件路径）\n'
            '- "代码风格依据 pyproject.toml" → reference（指向配置文件）\n'
            "**判断口诀：说「X 在哪里」→ reference；说「X 是什么/用什么」→ project_knowledge。**\n"
            "常见的 reference 模式：「入口是 X」「文档在 X」「参考 X」「配置在 X」「命令是 X」\n\n"
            # ── scope 分类指南 ──
            "## 作用域（scope）判断规则\n\n"
            "### user\n"
            "跨项目通用的个人偏好和纠正反馈。跟人走，不跟项目走。\n"
            "判断：约束的是「AI 助手的行为方式」而非「项目的技术属性」→ user\n"
            "正例：\n"
            '- "以后始终使用中文回答" → user（AI 助手的语言行为）\n'
            '- "默认使用 pytest" → user（AI 助手的工具选择行为）\n'
            '- "禁止自动提交代码" → user（AI 助手的操作行为）\n'
            '- "You must keep public APIs backward compatible" → user（AI 助手的编码行为约束）\n\n'
            "### project\n"
            "当前项目的技术事实、约定、决策和参考资源。跟项目走。\n"
            "判断：描述的是「项目本身是什么/用什么/在哪里」而非「AI 助手怎么做」→ project\n"
            "正例：\n"
            '- "本项目使用 Python 3.11" → project（项目技术栈事实）\n'
            '- "请长期记住：Web 框架是 FastAPI" → project（项目技术栈事实）\n'
            '- "请长期记住：默认日志格式是结构化 JSON" → project（项目技术约定）\n'
            '- "架构说明入口是 docs/architecture.md" → project（项目文件路径）\n\n'
            # ── critical 判断指南 ──
            "## 关键偏好（critical）判断规则\n\n"
            "**critical=True：用户对 AI 助手行为的硬性约束（如何回答、如何操作、不能做什么）**\n\n"
            "判断流程（两步）：\n"
            "1. 这是对 AI 助手的行为要求吗？（不是的话 → critical=False）\n"
            "2. 证据包含持续性/约束性标记词吗？（没有的话 → critical=False）\n\n"
            "**持续性/约束性标记词（任一即可）：**\n"
            "以后、今后、始终、总是、每次、默认、必须、禁止、不要再、一律、不再、请记住、长期记住、永久记住\n"
            "from now on、always、never、by default、must、do not、don't、remember that、remember permanently、permanently、again、anymore、no longer\n\n"
            "**critical=True 的正例（AI 助手行为约束）：**\n"
            '- "以后始终使用中文回答" → 对助手语言的要求 + 始终 → critical=True\n'
            '- "默认使用 pytest 进行测试" → 对助手工具选择的要求 + 默认 → critical=True\n'
            '- "禁止自动提交代码" → 对助手操作的限制 + 禁止 → critical=True\n'
            '- "By default, keep responses under ten lines" → 对助手回复的要求 + by default → critical=True\n'
            '- "Never auto-format files again; ask me first" → 对助手操作的限制 + never+again → critical=True\n'
            '- "以后默认先说明阻塞原因，再向我提问" → 对助手交流方式的要求 + 以后默认 → critical=True\n\n'
            "**critical=False 的正例（不是对 AI 助手的行为约束）：**\n"
            '- "本项目使用 Python 3.11" → 项目事实，不是助手行为要求 → critical=False\n'
            '- "Web 框架是 FastAPI" → 项目事实，不是助手行为要求 → critical=False\n'
            '- "请长期记住：缓存服务使用 Redis" → 项目事实，不是助手行为要求 → critical=False\n'
            '- "所有数据库迁移必须可逆" → 项目设计原则（约束的是数据库迁移，不是助手行为）→ critical=False\n'
            '- "必须保持公共 API 向后兼容" → 项目设计原则（约束的是 API 设计，不是助手行为）→ critical=False\n'
            '- "架构说明入口是 docs/architecture.md" → 路径指针，不是行为要求 → critical=False\n\n'
            "哪些类别可以标记 critical：\n"
            "- preference：可以（如行为偏好）\n"
            "- correction：可以（如纠正行为偏好）\n"
            "- project_knowledge：几乎不（只有极少数约束助手行为的项目规则可以，如「禁止修改与任务无关的文件」）\n"
            "- reference：不可以\n\n"
            # ── JSON 格式与字段说明 ──
            "## JSON 格式\n\n"
            "operation.action 只能是 create、update、skip。skip 不需要其他字段。\n"
            "非 skip 必须包含 scope、category、note_id、title、body、evidence、durability、"
            "critical、confidence、tags、supersedes。\n"
            "**类型约束（必须严格遵守）：**\n"
            "- note_id: 必须是字符串，不能是数字、null 或空字符串\n"
            "- title、body: 必须是非空字符串\n"
            "- evidence、tags: 必须是字符串数组，即使为空也必须写 []\n"
            "- supersedes: 必须是字符串数组，不需要替代旧笔记时写 []，不要写 null 或空字符串\n"
            "- critical: 必须是布尔值 true 或 false，不要加引号\n"
            "- confidence: 必须是数字（0 到 1），不要加引号\n"
            "evidence 必须是 turn_messages 中 role=user 消息的逐字原文数组；"
            "助手回复和工具输出不能作为用户偏好或纠正的证据。\n"
            "durability 只能是 persistent、temporary、uncertain；只有明确跨任务持续有效的信息才是 persistent。\n\n"
            "## 必须 skip 的情况\n\n"
            "临时格式、一次性任务、短期进度、模型猜测、闲聊、敏感凭据和只来自助手或工具的内容必须 skip。\n"
            "重复事实使用 update 或 skip；明确纠正旧规则时在 supersedes 中列出被替代的既有 note_id。\n\n"
            f"{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
        )


__all__ = ["MemoryNoteUpdater", "MemoryUpdateError"]
