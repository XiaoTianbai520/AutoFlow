from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VERSION = 1
KINDS = {
    "ocr", "move", "click", "drag", "scroll", "key", "text", "wait", "if", "repeat"
}


def new_step(kind: str) -> dict[str, Any]:
    defaults = {
        "ocr": {"expected": "", "roi": [0.0, 0.0, 1.0, 1.0], "timeout": 10.0, "interval": 0.5},
        "move": {"point": [0.5, 0.5], "source": "point"},
        "click": {"point": [0.5, 0.5], "source": "point", "button": "left", "count": 1},
        "drag": {"start": [0.3, 0.5], "end": [0.7, 0.5], "duration": 0.4},
        "scroll": {"dx": 0, "dy": -120},
        "key": {"key": "ENTER"},
        "text": {"text": ""},
        "wait": {"seconds": 1.0},
        "if": {"expected": "", "roi": [0.0, 0.0, 1.0, 1.0]},
        "repeat": {"count": 2},
    }
    if kind not in KINDS:
        raise ValueError(f"未知步骤类型: {kind}")
    step = {"id": uuid.uuid4().hex, "kind": kind, "params": defaults[kind].copy()}
    if kind == "if":
        step.update(yes=[], no=[])
    if kind == "repeat":
        step["body"] = []
    return step


@dataclass
class Workflow:
    name: str = "未命名脚本"
    steps: list[dict[str, Any]] = field(default_factory=list)
    window: dict[str, str] = field(default_factory=dict)
    version: int = VERSION

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "name": self.name, "window": self.window, "steps": self.steps}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Workflow":
        if not isinstance(raw, dict) or raw.get("version") != VERSION:
            raise ValueError("不支持的脚本格式版本")
        if not isinstance(raw.get("name"), str) or not isinstance(raw.get("steps"), list):
            raise ValueError("脚本缺少名称或步骤列表")
        if not isinstance(raw.get("window", {}), dict):
            raise ValueError("窗口信息格式错误")
        _validate_steps(raw["steps"])
        return cls(raw["name"], raw["steps"], raw.get("window", {}))

    @classmethod
    def load(cls, path: Path) -> "Workflow":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)


def _validate_steps(steps: list[dict[str, Any]], depth: int = 0) -> None:
    if depth > 10 or len(steps) > 1000:
        raise ValueError("脚本嵌套或步骤数量超出限制")
    for step in steps:
        if not isinstance(step, dict) or step.get("kind") not in KINDS:
            raise ValueError("脚本包含未知步骤")
        if not isinstance(step.get("id"), str) or not isinstance(step.get("params"), dict):
            raise ValueError("步骤缺少 ID 或参数")
        for key in ("yes", "no") if step["kind"] == "if" else ("body",) if step["kind"] == "repeat" else ():
            if not isinstance(step.get(key), list):
                raise ValueError("分支或循环内容格式错误")
            _validate_steps(step[key], depth + 1)


def find_step(steps: list[dict[str, Any]], step_id: str) -> dict[str, Any] | None:
    for step in steps:
        if step["id"] == step_id:
            return step
        for key in ("yes", "no", "body"):
            found = find_step(step.get(key, []), step_id)
            if found:
                return found
    return None
