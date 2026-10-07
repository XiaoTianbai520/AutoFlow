from __future__ import annotations

import threading
import time
from typing import Any, Callable, Protocol


class Backend(Protocol):
    def alive(self) -> bool: ...
    def screenshot(self) -> Any: ...
    def ocr(self, expected: str, roi: list[float]) -> tuple[int, int, int, int] | None: ...
    def move(self, point: list[float] | tuple[int, int], absolute: bool = False) -> None: ...
    def click(self, point: list[float] | tuple[int, int], button: str, count: int, absolute: bool = False) -> None: ...
    def drag(self, start: list[float], end: list[float], duration: float) -> None: ...
    def scroll(self, dx: int, dy: int) -> None: ...
    def key(self, key: str) -> None: ...
    def text(self, value: str) -> None: ...


class Stopped(Exception):
    pass


class Runner:
    def __init__(self, backend: Backend, report: Callable[[str, str | None], None] | None = None):
        self.backend = backend
        self.report = report or (lambda message, step_id: None)
        self._stop = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self.last_match: tuple[int, int, int, int] | None = None

    def stop(self) -> None:
        self._stop.set()
        self._resume.set()

    def pause(self) -> None:
        self._resume.clear()

    def resume(self) -> None:
        self._resume.set()

    def checkpoint(self) -> None:
        if self._stop.is_set():
            raise Stopped()
        while not self._resume.wait(0.05):
            if self._stop.is_set():
                raise Stopped()
        if self._stop.is_set():
            raise Stopped()
        if not self.backend.alive():
            raise RuntimeError("目标窗口已关闭，请重新选择窗口")

    def sleep(self, seconds: float) -> None:
        remaining = max(0.0, seconds)
        while remaining > 0:
            self.checkpoint()
            start = time.monotonic()
            time.sleep(min(0.05, remaining))
            remaining -= time.monotonic() - start

    def run(self, steps: list[dict[str, Any]]) -> None:
        self.checkpoint()
        self._run_steps(steps)

    def _run_steps(self, steps: list[dict[str, Any]]) -> None:
        for step in steps:
            self.checkpoint()
            kind, p, sid = step["kind"], step["params"], step["id"]
            self.report(f"开始：{kind}", sid)
            try:
                self._run_step(kind, p, step)
            except (Stopped, RuntimeError):
                raise
            except Exception as exc:
                raise RuntimeError(f"步骤 {kind} 执行失败：{exc}") from exc
            self.report(f"完成：{kind}", sid)

    def _run_step(self, kind: str, p: dict[str, Any], step: dict[str, Any]) -> None:
        if kind == "ocr":
            deadline = time.monotonic() + max(0.0, float(p["timeout"]))
            while True:
                self.checkpoint()
                self.last_match = self.backend.ocr(str(p["expected"]), p["roi"])
                if self.last_match:
                    return
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"OCR 未找到文字：{p['expected']}")
                self.sleep(min(float(p["interval"]), deadline - time.monotonic()))
        elif kind == "if":
            self.last_match = self.backend.ocr(str(p["expected"]), p["roi"])
            self._run_steps(step["yes"] if self.last_match else step["no"])
        elif kind == "repeat":
            count = int(p["count"])
            if not 1 <= count <= 1000:
                raise ValueError("循环次数必须在 1 至 1000 之间")
            for index in range(count):
                self.checkpoint()
                self.report(f"循环 {index + 1}/{count}", step["id"])
                self._run_steps(step["body"])
        elif kind in ("move", "click"):
            use_match = p.get("source") == "ocr"
            if use_match and not self.last_match:
                raise RuntimeError("尚无 OCR 匹配结果")
            point = self.last_match if use_match else p["point"]
            if use_match:
                x, y, w, h = point
                point = (x + w // 2, y + h // 2)
            if kind == "move":
                self.backend.move(point, use_match)
            else:
                self.backend.click(point, str(p["button"]), int(p["count"]), use_match)
        elif kind == "drag":
            self.backend.drag(p["start"], p["end"], float(p["duration"]))
        elif kind == "scroll":
            self.backend.scroll(int(p["dx"]), int(p["dy"]))
        elif kind == "key":
            self.backend.key(str(p["key"]))
        elif kind == "text":
            self.backend.text(str(p["text"]))
        elif kind == "wait":
            self.sleep(float(p["seconds"]))
