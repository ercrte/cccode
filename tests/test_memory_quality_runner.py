from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = ROOT / "eval"
if str(EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(EVAL_ROOT))

from memory_quality.models import (
    ExpectedMemory,
    ExtractionCase,
    InheritanceCase,
    InheritanceExpectation,
    MemoryQualityRunOptions,
)
from memory_quality.offline import ScriptedMemoryQualityProvider
from memory_quality.runner import MemoryQualityRunner
from mewcode.memory.index import MemoryIndexBuilder
from mewcode.memory.models import KnowledgeContext, MemoryUpdateJob, SessionMemoryConfig
from mewcode.memory.notes import MemoryNoteStore
from mewcode.memory.updater import MemoryNoteUpdater
from mewcode.providers.base import ChatMessage
from mewcode.session_id import SessionId


def critical_expected() -> ExpectedMemory:
    return ExpectedMemory(
        key="language",
        scope="user",
        category="preference",
        critical=True,
        evidence=("以后始终用中文回答",),
        content_term_groups=(("中文",),),
    )


def project_expected() -> ExpectedMemory:
    return ExpectedMemory(
        key="test-framework",
        scope="project",
        category="project_knowledge",
        critical=False,
        evidence=("本项目长期使用 pytest",),
        content_term_groups=(("pytest",),),
    )


def extraction_case(*, positive: bool = True) -> ExtractionCase:
    return ExtractionCase(
        case_id="critical_001" if positive else "negative_001",
        tags=("zh",),
        messages=(ChatMessage(role="user", content="以后始终用中文回答" if positive else "这次简短回答"),),
        expected=(critical_expected(),) if positive else (),
    )


def inheritance_case() -> InheritanceCase:
    return InheritanceCase(
        case_id="inheritance_001",
        tags=("language", "testing"),
        source_prompt="本项目长期使用 pytest；以后始终用中文回答",
        source_expected=(project_expected(), critical_expected()),
        target_prompt="按既定约定回答测试框架和语言；未知时请明确询问",
        expectation=InheritanceExpectation(
            required_term_groups=(("pytest",), ("中文",)),
            forbidden_terms=("unittest", "英文"),
            restatement_terms=("请提供", "请说明"),
        ),
    )


@pytest.mark.asyncio
async def test_offline_extraction_provider_accepts_positive_and_skips_negative(tmp_path: Path) -> None:
    positive = extraction_case()
    config = SessionMemoryConfig(user_dir=str(tmp_path / ".user"))
    store = MemoryNoteStore(tmp_path, config)
    updater = MemoryNoteUpdater(store, MemoryIndexBuilder(store, config))
    job = MemoryUpdateJob(
        session_id=SessionId("20260706-080910-abcd"),
        cwd=tmp_path,
        turn_messages=positive.messages,
        final_message=ChatMessage(role="assistant", content="完成"),
        knowledge_context=KnowledgeContext(),
    )

    extracted = await updater.extract(job=job, provider=ScriptedMemoryQualityProvider(extraction_case=positive))

    assert len(extracted.accepted) == 1
    assert extracted.accepted[0].note.source_evidence == ("以后始终用中文回答",)

    negative = extraction_case(positive=False)
    negative_job = MemoryUpdateJob(
        session_id=job.session_id,
        cwd=tmp_path,
        turn_messages=negative.messages,
        final_message=job.final_message,
        knowledge_context=job.knowledge_context,
    )
    skipped = await updater.extract(
        job=negative_job,
        provider=ScriptedMemoryQualityProvider(extraction_case=negative),
    )
    assert skipped.accepted == ()


@pytest.mark.asyncio
async def test_run_extraction_is_deterministic_and_does_not_persist(tmp_path: Path) -> None:
    runner = MemoryQualityRunner()
    options = MemoryQualityRunOptions(workspace_root=tmp_path)

    results, metrics = await runner.run_extraction((extraction_case(), extraction_case(positive=False)), options)

    assert metrics.tp == 1
    assert metrics.fp == metrics.fn == 0
    assert len(results) == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_offline_inheritance_source_and_enabled_target(tmp_path: Path) -> None:
    runner = MemoryQualityRunner()
    options = MemoryQualityRunOptions(workspace_root=tmp_path)

    [result] = await runner.run_inheritance((inheritance_case(),), options)

    assert result.enabled.session_started_empty is True
    assert result.enabled.injected_user_memory is True
    assert result.enabled.injected_project_memory is True
    assert result.enabled.first_turn_correct is True
    assert result.baseline.requested_restatement is True
    assert result.baseline.injected_user_memory is False
    assert result.baseline.injected_project_memory is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_inheritance_pair_metrics_distinguish_baseline(tmp_path: Path) -> None:
    runner = MemoryQualityRunner()
    options = MemoryQualityRunOptions(workspace_root=tmp_path)
    results = await runner.run_inheritance((inheritance_case(),), options)

    assert results[0].baseline.final_text.startswith("请提供")
    assert "pytest" in results[0].enabled.final_text
    assert "中文" in results[0].enabled.final_text


def test_memory_quality_cli_offline(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "eval" / "run_memory_eval.py"),
            "--mode",
            "offline",
            "--cases",
            str(ROOT / "eval" / "cases" / "memory_quality"),
            "--output",
            str(tmp_path / "report"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "提取用例=120" in completed.stdout
    assert (tmp_path / "report" / "results.json").exists()
    assert (tmp_path / "report" / "report.md").exists()


def test_memory_quality_cli_online_without_config_exits_two(tmp_path: Path) -> None:
    empty_home = tmp_path / "home"
    empty_home.mkdir()
    env = {**os.environ, "HOME": str(empty_home)}
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "eval" / "run_memory_eval.py"),
            "--mode",
            "online",
            "--cases",
            str(ROOT / "eval" / "cases" / "memory_quality"),
            "--output",
            str(tmp_path / "online"),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "在线记忆质量评测配置错误" in completed.stderr
