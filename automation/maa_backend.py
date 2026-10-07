from __future__ import annotations

import ctypes
import sys
import re
from dataclasses import dataclass
from pathlib import Path
from ctypes import wintypes

from .paths import data_dir


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    process_id: int


def list_windows() -> list[WindowInfo]:
    if sys.platform != "win32":
        return []
    user32 = ctypes.windll.user32
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    result: list[WindowInfo] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        size = user32.GetWindowTextLengthW(hwnd) + 1
        title_buffer = ctypes.create_unicode_buffer(size)
        user32.GetWindowTextW(hwnd, title_buffer, size)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        title = title_buffer.value.strip()
        if title:
            result.append(WindowInfo(int(hwnd), title, int(pid.value)))
        return True

    user32.EnumWindows(callback_type(visit), 0)
    return sorted(result, key=lambda item: item.title.lower())


def resource_root() -> Path:
    if getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS) / "resources"
    return Path(__file__).resolve().parent.parent / "resources"


class MaaBackend:
    def __init__(self, window: WindowInfo):
        if sys.platform != "win32":
            raise RuntimeError("仅支持 Windows")
        from maa.controller import Win32Controller, MaaWin32ScreencapMethodEnum, MaaWin32InputMethodEnum
        from maa.resource import Resource
        from maa.tasker import Tasker
        from maa.toolkit import Toolkit

        root = resource_root()
        required = [root / "model" / "ocr" / name for name in ("det.onnx", "rec.onnx", "keys.txt")]
        if any(not path.is_file() for path in required):
            raise RuntimeError("缺少离线 OCR 模型。请先运行 python scripts/fetch_models.py")
        self.window = window
        data_dir().mkdir(parents=True, exist_ok=True)
        Toolkit.init_option(str(data_dir()))
        self.controller = None
        self._shape = (1, 1)
        failures = []
        for method in (
            MaaWin32ScreencapMethodEnum.FramePool,
            MaaWin32ScreencapMethodEnum.PrintWindow,
            MaaWin32ScreencapMethodEnum.DXGI_DesktopDup_Window,
            MaaWin32ScreencapMethodEnum.ScreenDC,
            MaaWin32ScreencapMethodEnum.GDI,
        ):
            candidate = Win32Controller(
                hWnd=window.hwnd,
                screencap_method=method,
                mouse_method=MaaWin32InputMethodEnum.Seize,
                keyboard_method=MaaWin32InputMethodEnum.Seize,
            )
            if not candidate.post_connection().wait().succeeded:
                failures.append(method.name)
                continue
            image = candidate.post_screencap().wait().get()
            if image is None or image.size == 0 or image.max() <= 8:
                failures.append(f"{method.name}: 黑屏")
                continue
            self.controller = candidate
            self._shape = (int(image.shape[1]), int(image.shape[0]))
            break
        if self.controller is None:
            raise RuntimeError("窗口截图失败或全黑。请把目标窗口置于前台，并尝试以相同权限运行本程序。"
                               f"（已尝试：{', '.join(failures)}）")
        self.resource = Resource()
        if not self.resource.post_bundle(str(root)).wait().succeeded:
            raise RuntimeError("OCR 资源加载失败")
        self.tasker = Tasker()
        self.tasker.bind(self.resource, self.controller)
        if not self.tasker.inited:
            raise RuntimeError("MaaFramework 初始化失败")

    def alive(self) -> bool:
        user32 = ctypes.windll.user32
        user32.IsWindow.argtypes = [wintypes.HWND]
        return bool(user32.IsWindow(self.window.hwnd))

    def screenshot(self):
        if not self.alive():
            raise RuntimeError("目标窗口已关闭")
        job = self.controller.post_screencap().wait()
        image = job.get()
        if image is None or image.size == 0:
            raise RuntimeError("窗口截图失败，请确认目标窗口仍可见")
        if image.max() <= 8:
            raise RuntimeError("当前截图全黑。请把目标窗口置于前台后刷新截图")
        self._shape = (int(image.shape[1]), int(image.shape[0]))
        return image

    def _xy(self, point: list[float] | tuple[int, int], absolute: bool = False) -> tuple[int, int]:
        if absolute:
            return int(point[0]), int(point[1])
        width, height = self._shape
        return round(float(point[0]) * width), round(float(point[1]) * height)

    def _roi(self, roi: list[float]) -> list[int]:
        width, height = self._shape
        x, y, w, h = (float(v) for v in roi)
        if any(not 0 <= v <= 1 for v in (x, y, w, h)) or x + w > 1.0001 or y + h > 1.0001:
            raise ValueError("OCR 区域必须位于窗口内")
        return [round(x * width), round(y * height), max(1, round(w * width)), max(1, round(h * height))]

    def ocr(self, expected: str, roi: list[float]) -> tuple[int, int, int, int] | None:
        if not expected.strip():
            raise ValueError("OCR 文字不能为空")
        image = self.screenshot()
        pipeline = {"__AutoFlowOCR": {
            "recognition": {"type": "OCR", "param": {"expected": re.escape(expected), "roi": self._roi(roi), "threshold": 0.3}},
            "action": {"type": "DoNothing", "param": {}},
            "rate_limit": 0, "pre_delay": 0, "post_delay": 0, "timeout": 0,
        }}
        task = self.tasker.post_task("__AutoFlowOCR", pipeline).wait().get()
        if not task or not task.nodes:
            return None
        recognition = task.nodes[-1].recognition
        if not recognition or not recognition.hit or not recognition.box:
            return None
        box = recognition.box
        return tuple(int(v) for v in box)

    def _job(self, job, action: str) -> None:
        if not job.wait().succeeded:
            raise RuntimeError(f"{action}失败，请确认目标窗口可接收输入")

    def move(self, point, absolute=False) -> None:
        self.screenshot()
        x, y = self._xy(point, absolute)
        self._job(self.controller.post_touch_move(x, y), "鼠标移动")

    def click(self, point, button: str, count: int, absolute=False) -> None:
        self.screenshot()
        x, y = self._xy(point, absolute)
        if button not in ("left", "right", "middle") or count not in (1, 2):
            raise ValueError("点击参数不正确")
        contact = {"left": 0, "right": 1, "middle": 2}[button]
        for _ in range(count):
            self._job(self.controller.post_click(x, y, contact=contact), "鼠标点击")

    def drag(self, start: list[float], end: list[float], duration: float) -> None:
        self.screenshot()
        x1, y1 = self._xy(start)
        x2, y2 = self._xy(end)
        self._job(self.controller.post_swipe(x1, y1, x2, y2, max(1, round(duration * 1000))), "拖动")

    def scroll(self, dx: int, dy: int) -> None:
        self._job(self.controller.post_scroll(dx, dy), "滚轮")

    def key(self, key: str) -> None:
        codes = {"ENTER": 13, "ESC": 27, "TAB": 9, "SPACE": 32, "BACKSPACE": 8,
                 "UP": 38, "DOWN": 40, "LEFT": 37, "RIGHT": 39}
        code = codes.get(key.upper(), ord(key.upper()) if len(key) == 1 else None)
        if code is None:
            raise ValueError("不支持的按键，可用单个字母/数字或 ENTER、ESC、TAB 等")
        self._job(self.controller.post_click_key(code), "按键")

    def text(self, value: str) -> None:
        self._job(self.controller.post_input_text(value), "输入文字")
