from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = ROOT / "eval"
if str(EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(EVAL_ROOT))

from memory_quality.loader import MemoryQualityConfigError, MemoryQualityDatasetLoader
from memory_quality.models import MemoryQualityDataset


def expected(*, key: str = "language", critical: bool = True) -> dict[str, object]:
    return {
        "key": key,
        "scope": "user" if critical else "project",
        "category": "preference" if critical else "project_knowledge",
        "critical": critical,
        "evidence": ["以后始终用中文" if critical else "项目使用 pytest"],
        "content_term_groups": [["中文"]] if critical else [["pytest"]],
    }


def write_dataset(root: Path, *, extraction: dict[str, object] | None = None, inheritance: dict[str, object] | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    extraction = extraction or {
        "version": "1.0",
        "cases": [
            {
                "id": "critical_001",
                "tags": ["zh"],
                "messages": [{"role": "user", "content": "以后始终用中文"}],
                "expected": [expected()],
            }
        ],
    }
    inheritance = inheritance or {
        "version": "1.0",
        "cases": [
            {
                "id": "inheritance_001",
                "tags": ["language"],
                "source_prompt": "项目使用 pytest；以后始终用中文",
                "source_expected": [expected(key="framework", critical=False), expected()],
                "target_prompt": "按既定约定回答，未知时请询问",
                "expectation": {
                    "required_term_groups": [["pytest"], ["中文"]],
                    "forbidden_terms": ["unittest"],
                    "restatement_terms": ["请提供"],
                },
            }
        ],
    }
    (root / "extraction.json").write_text(json.dumps(extraction, ensure_ascii=False), encoding="utf-8")
    (root / "inheritance.json").write_text(json.dumps(inheritance, ensure_ascii=False), encoding="utf-8")


def test_loads_extraction_and_inheritance_schema(tmp_path: Path) -> None:
    write_dataset(tmp_path)

    dataset = MemoryQualityDatasetLoader().load(tmp_path)

    assert dataset.version == "1.0"
    assert dataset.extraction_cases[0].expected[0].critical is True
    assert len(dataset.inheritance_cases[0].source_expected) == 2


def test_rejects_version_mismatch(tmp_path: Path) -> None:
    write_dataset(tmp_path)
    path = tmp_path / "inheritance.json"
    raw = json.loads(path.read_text())
    raw["version"] = "2.0"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(MemoryQualityConfigError, match="版本"):
        MemoryQualityDatasetLoader().load(tmp_path)


def test_rejects_duplicate_case_id_across_files(tmp_path: Path) -> None:
    write_dataset(tmp_path)
    path = tmp_path / "inheritance.json"
    raw = json.loads(path.read_text())
    raw["cases"][0]["id"] = "critical_001"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(MemoryQualityConfigError, match="重复"):
        MemoryQualityDatasetLoader().load(tmp_path)


def test_rejects_evidence_not_in_user_message(tmp_path: Path) -> None:
    write_dataset(tmp_path)
    path = tmp_path / "extraction.json"
    raw = json.loads(path.read_text())
    raw["cases"][0]["expected"][0]["evidence"] = ["不存在"]
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(MemoryQualityConfigError, match="用户消息"):
        MemoryQualityDatasetLoader().load(tmp_path)


def test_rejects_invalid_role_and_empty_term_group(tmp_path: Path) -> None:
    write_dataset(tmp_path)
    path = tmp_path / "extraction.json"
    raw = json.loads(path.read_text())
    raw["cases"][0]["messages"][0]["role"] = "system"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(MemoryQualityConfigError, match="role"):
        MemoryQualityDatasetLoader().load(tmp_path)

    write_dataset(tmp_path)
    raw = json.loads(path.read_text())
    raw["cases"][0]["expected"][0]["content_term_groups"] = [[]]
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(MemoryQualityConfigError, match="词组"):
        MemoryQualityDatasetLoader().load(tmp_path)


def test_acceptance_size_and_coverage(tmp_path: Path) -> None:
    loader = MemoryQualityDatasetLoader()
    base = loader.load(_write_minimum_files(tmp_path))

    loader.validate_acceptance_size(base)

    too_small = MemoryQualityDataset(base.version, base.extraction_cases[:-1], base.inheritance_cases)
    with pytest.raises(MemoryQualityConfigError, match="120"):
        loader.validate_acceptance_size(too_small)


def test_repository_dataset_meets_acceptance_size() -> None:
    loader = MemoryQualityDatasetLoader()
    dataset = loader.load(ROOT / "eval" / "cases" / "memory_quality")

    loader.validate_acceptance_size(dataset)

    assert len(dataset.extraction_cases) == 120
    assert sum(1 for case in dataset.extraction_cases for item in case.expected if item.critical) == 50
    assert sum(1 for case in dataset.extraction_cases for item in case.expected if not item.critical) == 30
    assert sum(1 for case in dataset.extraction_cases if not case.expected) == 40
    assert len(dataset.inheritance_cases) == 20


def _write_minimum_files(root: Path) -> Path:
    coverage = list(MemoryQualityDatasetLoader.REQUIRED_COVERAGE_TAGS)
    cases: list[dict[str, object]] = []
    for index in range(50):
        text = f"以后始终用中文 {index}"
        item = expected(key=f"critical-{index}")
        item["evidence"] = [text]
        cases.append(
            {
                "id": f"critical_{index:03d}",
                "tags": coverage if index == 0 else ["critical"],
                "messages": [{"role": "user", "content": text}],
                "expected": [item],
            }
        )
    for index in range(30):
        text = f"项目使用 pytest {index}"
        item = expected(key=f"memory-{index}", critical=False)
        item["evidence"] = [text]
        cases.append(
            {
                "id": f"memory_{index:03d}",
                "tags": ["memory"],
                "messages": [{"role": "user", "content": text}],
                "expected": [item],
            }
        )
    for index in range(40):
        cases.append(
            {
                "id": f"negative_{index:03d}",
                "tags": ["temporary"],
                "messages": [{"role": "user", "content": f"这次简短回答 {index}"}],
                "expected": [],
            }
        )
    inheritance_cases = []
    for index in range(20):
        project = expected(key=f"framework-{index}", critical=False)
        project["evidence"] = [f"项目使用 pytest {index}"]
        preference = expected(key=f"language-{index}")
        preference["evidence"] = [f"以后始终用中文 {index}"]
        source = f"项目使用 pytest {index}；以后始终用中文 {index}"
        inheritance_cases.append(
            {
                "id": f"inheritance_{index:03d}",
                "tags": ["inheritance"],
                "source_prompt": source,
                "source_expected": [project, preference],
                "target_prompt": "按既定约定回答，未知时请询问",
                "expectation": {
                    "required_term_groups": [["pytest"], ["中文"]],
                    "forbidden_terms": [],
                    "restatement_terms": ["请提供"],
                },
            }
        )
    write_dataset(
        root,
        extraction={"version": "1.0", "cases": cases},
        inheritance={"version": "1.0", "cases": inheritance_cases},
    )
    return root
