import threading
import time

import pytest

from automation.runner import Runner, Stopped
from automation.maa_backend import MaaBackend
from automation.workflow import Workflow, new_step


class FakeBackend:
    def __init__(self):
        self.actions = []
        self.found = False
        self.open = True

    def alive(self):
        return self.open

    def screenshot(self):
        return None

    def ocr(self, expected, roi):
        return (10, 20, 30, 40) if self.found else None

    def move(self, point, absolute=False):
        self.actions.append(("move", point, absolute))

    def click(self, point, button, count, absolute=False):
        self.actions.append(("click", point, button, count, absolute))

    def drag(self, *args):
        self.actions.append(("drag", *args))

    def scroll(self, *args):
        self.actions.append(("scroll", *args))

    def key(self, key):
        self.actions.append(("key", key))

    def text(self, value):
        self.actions.append(("text", value))


def test_roundtrip_and_branches(tmp_path):
    condition = new_step("if")
    condition["params"]["expected"] = "开始"
    condition["yes"].append(new_step("click"))
    condition["yes"][0]["params"]["source"] = "ocr"
    loop = new_step("repeat")
    loop["params"]["count"] = 2
    loop["body"].append(new_step("key"))
    workflow = Workflow("测试", [condition, loop])
    path = tmp_path / "flow.json"
    workflow.save(path)
    loaded = Workflow.load(path)
    backend = FakeBackend()
    backend.found = True
    Runner(backend).run(loaded.steps)
    assert backend.actions == [("click", (25, 40), "left", 1, True), ("key", "ENTER"), ("key", "ENTER")]


def test_ocr_timeout_and_window_closed():
    backend = FakeBackend()
    step = new_step("ocr")
    step["params"].update(expected="missing", timeout=0.01, interval=0.01)
    with pytest.raises(RuntimeError, match="OCR 未找到"):
        Runner(backend).run([step])
    backend.open = False
    with pytest.raises(RuntimeError, match="目标窗口已关闭"):
        Runner(backend).run([new_step("wait")])


def test_pause_and_stop():
    backend = FakeBackend()
    step = new_step("wait")
    step["params"]["seconds"] = 0.5
    runner = Runner(backend)
    runner.pause()
    outcome = []

    def work():
        try:
            runner.run([step])
        except Stopped:
            outcome.append("stopped")

    thread = threading.Thread(target=work)
    thread.start()
    time.sleep(0.05)
    assert thread.is_alive()
    runner.stop()
    thread.join(timeout=1)
    assert outcome == ["stopped"]


def test_window_relative_coordinates():
    backend = MaaBackend.__new__(MaaBackend)
    backend._shape = (1286, 720)
    assert backend._xy([0.5, 0.25]) == (643, 180)
    assert backend._roi([0.1, 0.2, 0.5, 0.5]) == [129, 144, 643, 360]
    with pytest.raises(ValueError, match="窗口内"):
        backend._roi([0.8, 0.8, 0.5, 0.5])
