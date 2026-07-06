from __future__ import annotations

import json
from pathlib import Path

import pytest

from mewcode.memory.extraction import MemoryCandidateValidator, MemoryExtractionError, parse_memory_candidates
from mewcode.memory.models import KnowledgeContext, MemoryCandidate, MemoryUpdateJob, SessionMemoryConfig
from mewcode.memory.notes import MemoryNoteStore
from mewcode.providers.base import ChatMessage
from mewcode.session_id import SessionId
from tests.test_memory_notes import note


def make_job(tmp_path: Path, *messages: ChatMessage) -> MemoryUpdateJob:
    return MemoryUpdateJob(
        session_id=SessionId("20260706-080910-abcd"),
        cwd=tmp_path,
        turn_messages=messages or (ChatMessage(role="user", content="以后始终使用中文回答"),),
        final_message=ChatMessage(role="assistant", content="明白"),
        knowledge_context=KnowledgeContext(),
    )


def candidate(**overrides: object) -> MemoryCandidate:
    values: dict[str, object] = {
        "action": "create",
        "scope": "user",
        "category": "preference",
        "note_id": "language",
        "title": "回复语言",
        "body": "用户要求以后始终使用中文回答。",
        "evidence": ("以后始终使用中文回答",),
        "durability": "persistent",
        "critical": True,
        "confidence": 0.99,
        "tags": ("language",),
        "supersedes": (),
    }
    values.update(overrides)
    return MemoryCandidate(**values)  # type: ignore[arg-type]


def validator(tmp_path: Path, *, threshold: float = 0.95) -> tuple[MemoryCandidateValidator, MemoryNoteStore]:
    config = SessionMemoryConfig(
        user_dir=str(tmp_path / "home" / ".mewcode"),
        critical_preference_min_confidence=threshold,
    )
    store = MemoryNoteStore(tmp_path, config)
    return MemoryCandidateValidator(store, config), store


def test_parse_candidates_from_json_and_fence() -> None:
    operation = {
        "action": "create",
        "scope": "user",
        "category": "preference",
        "note_id": "language",
        "title": "语言",
        "body": "默认中文",
        "evidence": ["以后默认中文"],
        "durability": "persistent",
        "critical": True,
        "confidence": 0.99,
        "tags": ["language"],
        "supersedes": [],
    }
    raw = json.dumps({"operations": [operation]}, ensure_ascii=False)

    parsed = parse_memory_candidates(raw)
    fenced = parse_memory_candidates(f"```json\n{raw}\n```")

    assert parsed == fenced
    assert parsed[0].evidence == ("以后默认中文",)
    assert parsed[0].critical is True


@pytest.mark.parametrize("raw", ["bad", "{}", '{"operations": [1]}'])
def test_parse_rejects_invalid_json_schema(raw: str) -> None:
    with pytest.raises(MemoryExtractionError):
        parse_memory_candidates(raw)


def test_candidate_with_wrong_field_types_is_rejected_as_invalid_schema(tmp_path: Path) -> None:
    [parsed] = parse_memory_candidates(
        '{"operations":[{"action":"create","scope":"user","category":"preference",'
        '"note_id":"x","title":"x","body":"x","evidence":"bad",'
        '"durability":"persistent","critical":"yes","confidence":true,"tags":[],"supersedes":[]}]}'
    )
    check, _ = validator(tmp_path)

    result = check.validate((parsed,), make_job(tmp_path))

    assert result.rejected[0].code == "invalid_schema"
    assert "evidence" in result.rejected[0].message


def test_skip_is_neither_accepted_nor_rejected(tmp_path: Path) -> None:
    check, _ = validator(tmp_path)

    result = check.validate((MemoryCandidate(action="skip"),), make_job(tmp_path))

    assert result.accepted == ()
    assert result.rejected == ()


@pytest.mark.parametrize("durability", ["temporary", "uncertain", ""])
def test_rejects_non_persistent_candidates(tmp_path: Path, durability: str) -> None:
    check, _ = validator(tmp_path)

    result = check.validate((candidate(durability=durability),), make_job(tmp_path))

    assert result.rejected[0].code == "not_persistent"


def test_requires_exact_user_evidence(tmp_path: Path) -> None:
    check, _ = validator(tmp_path)
    job = make_job(
        tmp_path,
        ChatMessage(role="user", content="请继续"),
        ChatMessage(role="assistant", content="以后始终使用中文回答"),
        ChatMessage(role="tool", content="以后始终使用中文回答", tool_call_id="x"),
    )

    result = check.validate((candidate(),), job)

    assert result.rejected[0].code == "missing_user_evidence"


def test_accepts_explicit_critical_preference(tmp_path: Path) -> None:
    check, _ = validator(tmp_path)

    result = check.validate((candidate(),), make_job(tmp_path))

    assert result.rejected == ()
    assert result.accepted[0].note.critical is True
    assert result.accepted[0].note.source_evidence == ("以后始终使用中文回答",)


def test_rejects_implicit_or_low_confidence_critical_preference(tmp_path: Path) -> None:
    check, _ = validator(tmp_path)
    implicit_job = make_job(tmp_path, ChatMessage(role="user", content="我喜欢中文"))

    implicit = check.validate((candidate(evidence=("我喜欢中文",)),), implicit_job)
    low = check.validate((candidate(confidence=0.9),), make_job(tmp_path))

    assert implicit.rejected[0].code == "critical_not_explicit"
    assert low.rejected[0].code == "critical_low_confidence"


def test_rejects_sensitive_candidate(tmp_path: Path) -> None:
    check, _ = validator(tmp_path)
    text = "请长期记住 Bearer abcdefghijklmnop"
    job = make_job(tmp_path, ChatMessage(role="user", content=text))

    result = check.validate(
        (candidate(body=text, evidence=(text,), critical=False, category="project_knowledge", scope="project"),),
        job,
    )

    assert result.rejected[0].code == "sensitive_content"


def test_rejects_duplicate_existing_and_batch_candidates(tmp_path: Path) -> None:
    check, store = validator(tmp_path)
    store.write_note(note("existing", scope="user", category="preference", body="用户要求以后始终使用中文回答。"))

    existing = check.validate((candidate(note_id="another"),), make_job(tmp_path))
    store.delete_note("user", "existing")
    batch = check.validate((candidate(), candidate(note_id="language-two")), make_job(tmp_path))

    assert existing.rejected[0].code == "duplicate"
    assert len(batch.accepted) == 1
    assert batch.rejected[0].code == "duplicate"


def test_update_preserves_created_at(tmp_path: Path) -> None:
    check, store = validator(tmp_path)
    store.write_note(note("language", scope="user", category="preference", body="旧规则"))

    result = check.validate((candidate(action="update"),), make_job(tmp_path))

    assert result.accepted[0].note.created_at == "2026-06-12T08:09:10Z"


def test_supersedes_must_reference_existing_note(tmp_path: Path) -> None:
    check, store = validator(tmp_path)
    missing = check.validate((candidate(supersedes=("old",)),), make_job(tmp_path))
    store.write_note(note("old", scope="user", category="preference", body="旧规则"))
    valid = check.validate((candidate(supersedes=("old",)),), make_job(tmp_path))

    assert missing.rejected[0].code == "invalid_supersedes"
    assert valid.accepted[0].supersedes == ("old",)


def test_accepts_critical_project_knowledge(tmp_path: Path) -> None:
    """critical=True 且 category=project_knowledge 的候选应被接受。"""
    check, _store = validator(tmp_path)
    job = make_job(tmp_path, ChatMessage(role="user", content="请长期记住：所有数据库迁移必须可逆"))
    cand = candidate(
        scope="project",
        category="project_knowledge",
        note_id="migration-reversible",
        title="迁移必须可逆",
        body="所有数据库迁移必须可逆。",
        evidence=("请长期记住：所有数据库迁移必须可逆",),
        critical=True,
        confidence=0.99,
    )

    result = check.validate((cand,), job)

    assert result.rejected == ()
    assert len(result.accepted) == 1
    assert result.accepted[0].note.critical is True
    assert result.accepted[0].note.category == "project_knowledge"


def test_evidence_normalized_matching(tmp_path: Path) -> None:
    """证据的标点/空白差异应被归一化匹配容忍。"""
    check, _store = validator(tmp_path)
    # 消息末尾有句号，但 evidence 没有
    job = make_job(tmp_path, ChatMessage(role="user", content="以后始终使用中文回答。"))
    cand = candidate(evidence=("以后始终使用中文回答",))

    result = check.validate((cand,), job)

    assert result.rejected == ()
    assert len(result.accepted) == 1


def test_evidence_still_rejects_mismatch(tmp_path: Path) -> None:
    """归一化后内容实质不同的 evidence 仍应被拒绝。"""
    check, _store = validator(tmp_path)
    job = make_job(tmp_path, ChatMessage(role="user", content="我喜欢简洁"))
    cand = candidate(evidence=("以后必须简洁",))

    result = check.validate((cand,), job)

    assert len(result.rejected) == 1
    assert result.rejected[0].code == "missing_user_evidence"
