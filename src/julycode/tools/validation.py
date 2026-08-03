from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def validate_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> list[str]:
    return _validate(schema, arguments, path="参数")


def _validate(schema: Mapping[str, Any], value: Any, *, path: str) -> list[str]:
    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type is None:
        return errors

    if expected_type == "object":
        if not isinstance(value, Mapping):
            return [f"{path} 必须是对象"]
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        for name in required:
            if name not in value:
                errors.append(f"{path}.{name} 是必填字段")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    errors.append(f"{path}.{name} 不是允许的字段")
        for name, child_schema in properties.items():
            if name in value and isinstance(child_schema, Mapping):
                errors.extend(_validate(child_schema, value[name], path=f"{path}.{name}"))
        return errors

    if expected_type == "array":
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            return [f"{path} 必须是数组"]
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                errors.extend(_validate(item_schema, item, path=f"{path}[{index}]"))
        return _validate_enum(schema, value, path, errors)

    if expected_type == "string" and not isinstance(value, str):
        errors.append(f"{path} 必须是字符串")
    elif expected_type == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        errors.append(f"{path} 必须是数字")
    elif expected_type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        errors.append(f"{path} 必须是整数")
    elif expected_type == "boolean" and not isinstance(value, bool):
        errors.append(f"{path} 必须是布尔值")
    elif expected_type not in {"string", "number", "integer", "boolean"}:
        errors.append(f"{path} 使用了不支持的类型 {expected_type}")

    return _validate_enum(schema, value, path, errors)


def _validate_enum(
    schema: Mapping[str, Any],
    value: Any,
    path: str,
    errors: list[str],
) -> list[str]:
    enum = schema.get("enum")
    if enum is not None and value not in enum:
        errors.append(f"{path} 必须是 {list(enum)} 之一")
    return errors
