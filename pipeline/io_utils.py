"""JSON 校验与原子写入；任何失败均不覆盖上一版。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from schemas import validate_payload

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _dumps(value, *, level: int = 0, indent: int = 2) -> str:
    """对象换行缩进、标量数组压成一行的序列化；与 json.dumps 输出语义一致。"""
    pad = " " * (indent * level)
    inner = " " * (indent * (level + 1))
    if isinstance(value, dict):
        if not value:
            return "{}"
        rows = [f"{inner}{json.dumps(key, ensure_ascii=False)}: {_dumps(item, level=level + 1, indent=indent)}"
                for key, item in value.items()]
        return "{\n" + ",\n".join(rows) + f"\n{pad}" + "}"
    if isinstance(value, list):
        if not value:
            return "[]"
        if all(not isinstance(item, (dict, list)) for item in value):
            return "[" + ", ".join(json.dumps(item, ensure_ascii=False, allow_nan=False) for item in value) + "]"
        rows = [f"{inner}{_dumps(item, level=level + 1, indent=indent)}" for item in value]
        return "[\n" + ",\n".join(rows) + f"\n{pad}" + "]"
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def protected_write(name: str, payload: dict, *, directory: Path | None = None,
                    compact_arrays: bool = False) -> Path:
    target_dir = directory or DATA
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    try:
        validate_payload(name, payload)
    except Exception as exc:
        print(f"警告：{name} schema 校验失败，保留上一版 JSON：{exc}")
        raise
    descriptor, temp_name = tempfile.mkstemp(prefix=name + ".", suffix=".tmp", dir=target_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            if compact_arrays:
                handle.write(_dumps(payload))
            else:
                json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    print(f"已校验并写入 {target}")
    return target


def validate_file(name: str, *, directory: Path | None = None) -> None:
    path = (directory or DATA) / name
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate_payload(name, payload)
    print(f"{name} schema 校验通过")
