from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QRect, Qt, QThread, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QSplitter, QStatusBar, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .maa_backend import MaaBackend, WindowInfo, list_windows
from .paths import data_dir
from .runner import Runner, Stopped
from .workflow import Workflow, find_step, new_step

NAMES = {"ocr": "识别文字", "move": "移动鼠标", "click": "鼠标点击", "drag": "拖动鼠标",
         "scroll": "滚动页面", "key": "按下按键", "text": "输入文字", "wait": "等待",
         "if": "判断文字", "repeat": "重复执行"}


def script_dir() -> Path:
    base = data_dir() / "scripts"
    base.mkdir(parents=True, exist_ok=True)
    return base


class Preview(QLabel):
    picked = Signal(str, object)

    def __init__(self):
        super().__init__("选择窗口并点击“刷新截图”")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)
        self.setStyleSheet("background:#101e2e; color:#90a7bb; border:1px solid #304459; border-radius:10px;")
        self.pix: QPixmap | None = None
        self.mode = ""
        self.start: QPoint | None = None
        self.cursor: QPoint | None = None

    def set_image(self, image) -> None:
        height, width, _ = image.shape
        rgb = image[:, :, ::-1].copy()
        self.pix = QPixmap.fromImage(QImage(rgb.data, width, height, 3 * width, QImage.Format_RGB888).copy())
        self.update()

    def _image_rect(self) -> QRect:
        if not self.pix:
            return QRect()
        size = self.pix.size().scaled(self.size(), Qt.KeepAspectRatio)
        return QRect((self.width() - size.width()) // 2, (self.height() - size.height()) // 2,
                     size.width(), size.height())

    def _normalized(self, pos: QPoint) -> list[float] | None:
        rect = self._image_rect()
        if not rect.contains(pos) or not rect.width() or not rect.height():
            return None
        return [round((pos.x() - rect.left()) / rect.width(), 6),
                round((pos.y() - rect.top()) / rect.height(), 6)]

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.pix:
            return
        painter = QPainter(self)
        rect = self._image_rect()
        painter.drawPixmap(rect, self.pix)
        if self.mode == "roi" and self.start and self.cursor:
            painter.setPen(QPen(QColor("#44d4ba"), 2, Qt.DashLine))
            painter.setBrush(QColor(68, 212, 186, 45))
            painter.drawRect(QRect(self.start, self.cursor).normalized())

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or not self.mode:
            return
        point = self._normalized(event.position().toPoint())
        if point is None:
            return
        if self.mode == "roi":
            self.start = event.position().toPoint()
            self.cursor = self.start
        else:
            self.picked.emit(self.mode, point)
            self.mode = ""
        self.update()

    def mouseMoveEvent(self, event):
        if self.mode == "roi" and self.start:
            self.cursor = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if self.mode != "roi" or not self.start:
            return
        end = event.position().toPoint()
        first, last = self._normalized(self.start), self._normalized(end)
        self.start = self.cursor = None
        self.mode = ""
        if first and last:
            x1, x2 = sorted((first[0], last[0]))
            y1, y2 = sorted((first[1], last[1]))
            if x2 - x1 > 0.005 and y2 - y1 > 0.005:
                self.picked.emit("roi", [x1, y1, x2 - x1, y2 - y1])
        self.update()


class RunThread(QThread):
    message = Signal(str, object)
    frame = Signal(object)
    result = Signal(str)

    def __init__(self, window: WindowInfo, steps: list[dict[str, Any]]):
        super().__init__()
        self.window = window
        self.steps = steps
        self.runner: Runner | None = None
        self.stop_requested = False
        self.paused = False

    def run(self):
        try:
            backend = MaaBackend(self.window)
            def report(message, sid):
                self.message.emit(message, sid)
                if message.startswith("完成"):
                    try:
                        self.frame.emit(backend.screenshot())
                    except RuntimeError:
                        pass
            self.runner = Runner(backend, report)
            if self.stop_requested:
                self.runner.stop()
            if self.paused:
                self.runner.pause()
            self.runner.run(self.steps)
            self.result.emit("运行完成")
        except Stopped:
            self.result.emit("已停止")
        except Exception as exc:
            self.result.emit(f"运行失败：{exc}")

    def stop(self):
        self.stop_requested = True
        if self.runner:
            self.runner.stop()

    def set_paused(self, paused: bool):
        self.paused = paused
        if self.runner:
            self.runner.pause() if paused else self.runner.resume()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AutoFlow · 自动化操作平台")
        self.resize(1320, 820)
        self.workflow = Workflow()
        self.path: Path | None = None
        self.dirty = False
        self.windows: list[WindowInfo] = []
        self.backend: MaaBackend | None = None
        self.worker: RunThread | None = None
        self._building = False
        self._selected_id: str | None = None
        self._pick_field = ""
        self._setup()
        self.refresh_scripts()
        self.refresh_windows()

    def _button(self, text: str, callback, accent=False) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(callback)
        if accent:
            button.setProperty("accent", True)
        return button

    def _setup(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 14)
        root.setSpacing(14)
        header = QHBoxLayout()
        logo = QLabel("AUTOFLOW")
        logo.setObjectName("logo")
        header.addWidget(logo)
        header.addSpacing(18)
        header.addWidget(QLabel("把重复操作编成清晰的步骤"))
        header.addStretch()
        header.addWidget(self._button("新建脚本", self.new_script))
        header.addWidget(self._button("保存", self.save_script, True))
        header.addWidget(self._button("导入", self.import_script))
        header.addWidget(self._button("导出", self.export_script))
        root.addLayout(header)

        window_bar = QHBoxLayout()
        window_bar.addWidget(QLabel("目标窗口"))
        self.window_combo = QComboBox()
        self.window_combo.setMinimumWidth(400)
        self.window_combo.currentIndexChanged.connect(self.window_changed)
        window_bar.addWidget(self.window_combo, 1)
        window_bar.addWidget(self._button("刷新列表", self.refresh_windows))
        window_bar.addWidget(self._button("刷新截图", self.refresh_preview))
        root.addLayout(window_bar)

        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("脚本"))
        self.scripts = QListWidget()
        self.scripts.setMaximumHeight(140)
        self.scripts.itemClicked.connect(self.select_script)
        left_layout.addWidget(self.scripts)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("脚本名称")
        self.name_edit.textChanged.connect(self.name_changed)
        left_layout.addWidget(self.name_edit)
        left_layout.addWidget(QLabel("步骤 · 从上到下执行"))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(self.step_changed)
        left_layout.addWidget(self.tree, 1)
        step_buttons = QHBoxLayout()
        self.add_menu = QMenu(self)
        for kind, title in NAMES.items():
            self.add_menu.addAction(title, lambda checked=False, k=kind: self.add_step(k))
        add_button = self._button("＋ 添加步骤", lambda: self.add_menu.exec(add_button.mapToGlobal(QPoint(0, add_button.height()))), True)
        step_buttons.addWidget(add_button)
        step_buttons.addWidget(self._button("↑", lambda: self.move_step(-1)))
        step_buttons.addWidget(self._button("↓", lambda: self.move_step(1)))
        step_buttons.addWidget(self._button("删除", self.delete_step))
        left_layout.addLayout(step_buttons)
        split.addWidget(left)

        middle = QWidget()
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        preview_title = QHBoxLayout()
        preview_title.addWidget(QLabel("窗口画面"))
        preview_title.addStretch()
        self.preview_hint = QLabel("在步骤参数中选择采集目标")
        preview_title.addWidget(self.preview_hint)
        middle_layout.addLayout(preview_title)
        self.preview = Preview()
        self.preview.picked.connect(self.preview_picked)
        middle_layout.addWidget(self.preview, 1)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(160)
        self.log.setPlaceholderText("运行日志会显示在这里")
        middle_layout.addWidget(self.log)
        split.addWidget(middle)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("步骤参数"))
        self.form_container = QWidget()
        self.form = QFormLayout(self.form_container)
        self.form.setSpacing(12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.form_container)
        right_layout.addWidget(scroll, 1)
        right_layout.addWidget(self._button("测试选中步骤", self.test_selected))
        split.addWidget(right)
        split.setSizes([330, 650, 330])

        controls = QHBoxLayout()
        controls.addWidget(self._button("运行脚本", self.run_script, True))
        self.pause_button = self._button("暂停", self.pause_script)
        controls.addWidget(self.pause_button)
        controls.addWidget(self._button("停止", self.stop_script))
        controls.addStretch()
        self.state = QLabel("就绪")
        controls.addWidget(self.state)
        root.addLayout(controls)
        self.setStatusBar(QStatusBar())
        self.setStyleSheet("""
            QWidget { background:#17283a; color:#e9f0f5; font: 10pt 'Microsoft YaHei UI'; }
            QLabel#logo { font: 700 16pt 'Segoe UI'; color:#54dbc5; letter-spacing:2px; }
            QPushButton { background:#263c50; border:1px solid #3b5367; border-radius:7px; padding:8px 11px; }
            QPushButton:hover { background:#34536a; }
            QPushButton[accent="true"] { background:#38bea9; color:#092b34; border:0; font-weight:700; }
            QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QTextEdit,QListWidget,QTreeWidget,QScrollArea {
                background:#102133; border:1px solid #365069; border-radius:7px; padding:5px; }
            QTreeWidget::item:selected,QListWidget::item:selected { background:#267a83; }
            QSplitter::handle { background:#30485a; width:8px; }
        """)

    def _error(self, message: str):
        QMessageBox.warning(self, "AutoFlow", message)
        self.log.append(message)

    def mark_dirty(self):
        if not self._building:
            self.dirty = True
            self.setWindowTitle(f"AutoFlow · {self.workflow.name} *")

    def refresh_scripts(self):
        self.scripts.clear()
        for path in sorted(script_dir().glob("*.json")):
            try:
                workflow = Workflow.load(path)
            except Exception:
                continue
            self.scripts.addItem(workflow.name)
            self.scripts.item(self.scripts.count() - 1).setData(Qt.UserRole, str(path))

    def select_script(self, item):
        if self.dirty:
            self.save_script()
        try:
            self.load_script(Path(item.data(Qt.UserRole)))
        except Exception as exc:
            self._error(f"打开脚本失败：{exc}")

    def load_script(self, path: Path):
        self.workflow = Workflow.load(path)
        self.path = path
        self.dirty = False
        self._building = True
        self.name_edit.setText(self.workflow.name)
        self._building = False
        self.rebuild_tree()
        self.window_combo.blockSignals(True)
        self.window_combo.setCurrentIndex(-1)
        self.window_combo.blockSignals(False)
        self.refresh_windows()
        self.dirty = False
        self.setWindowTitle(f"AutoFlow · {self.workflow.name}")

    def new_script(self):
        if self.dirty:
            self.save_script()
        self.workflow = Workflow()
        self.path = None
        self._building = True
        self.name_edit.setText(self.workflow.name)
        self._building = False
        self.rebuild_tree()
        self.mark_dirty()

    def name_changed(self, value: str):
        self.workflow.name = value.strip() or "未命名脚本"
        self.mark_dirty()

    def save_script(self):
        if self.path is None:
            self.path = script_dir() / f"{uuid.uuid4().hex}.json"
        self.workflow.save(self.path)
        self.dirty = False
        self.setWindowTitle(f"AutoFlow · {self.workflow.name}")
        self.refresh_scripts()
        self.statusBar().showMessage("脚本已保存", 3000)

    def import_script(self):
        name, _ = QFileDialog.getOpenFileName(self, "导入脚本", "", "JSON 脚本 (*.json)")
        if not name:
            return
        try:
            imported = Workflow.load(Path(name))
            self.workflow = imported
            self.path = None
            self.save_script()
            self.load_script(self.path)
        except Exception as exc:
            self._error(f"导入失败：{exc}")

    def export_script(self):
        name, _ = QFileDialog.getSaveFileName(self, "导出脚本", f"{self.workflow.name}.json", "JSON 脚本 (*.json)")
        if name:
            try:
                self.workflow.save(Path(name))
                self.statusBar().showMessage("导出完成", 3000)
            except Exception as exc:
                self._error(f"导出失败：{exc}")

    def refresh_windows(self):
        previous = self.current_window()
        self.windows = list_windows()
        self.window_combo.blockSignals(True)
        self.window_combo.clear()
        for window in self.windows:
            self.window_combo.addItem(f"{window.title}  ·  PID {window.process_id}", window.hwnd)
        preferred = previous.hwnd if previous else None
        if preferred is None and self.workflow.window:
            preferred = next((w.hwnd for w in self.windows if w.title == self.workflow.window.get("title")), None)
        index = next((i for i, w in enumerate(self.windows) if w.hwnd == preferred), -1)
        self.window_combo.setCurrentIndex(index)
        self.window_combo.blockSignals(False)
        self.window_changed()

    def current_window(self) -> WindowInfo | None:
        index = self.window_combo.currentIndex()
        return self.windows[index] if 0 <= index < len(self.windows) else None

    def window_changed(self):
        self.backend = None
        window = self.current_window()
        if window:
            self.workflow.window = {"title": window.title}
            self.mark_dirty()
            self.refresh_preview()

    def refresh_preview(self):
        if self.worker and self.worker.isRunning():
            return
        window = self.current_window()
        if not window:
            return
        try:
            if not self.backend or self.backend.window.hwnd != window.hwnd:
                self.backend = MaaBackend(window)
            self.preview.set_image(self.backend.screenshot())
        except Exception as exc:
            self._error(f"刷新截图失败：{exc}")

    def _fill_tree(self, parent, steps):
        for index, step in enumerate(steps, 1):
            item = QTreeWidgetItem(parent, [f"{index:02d}  {NAMES[step['kind']]}  {self._summary(step)}"])
            item.setData(0, Qt.UserRole, step["id"])
            if step["kind"] == "if":
                for key, label in (("yes", "找到时"), ("no", "未找到时")):
                    group = QTreeWidgetItem(item, [label])
                    group.setData(0, Qt.UserRole, (step["id"], key))
                    self._fill_tree(group, step[key])
            elif step["kind"] == "repeat":
                group = QTreeWidgetItem(item, ["循环内容"])
                group.setData(0, Qt.UserRole, (step["id"], "body"))
                self._fill_tree(group, step["body"])

    def _summary(self, step):
        p = step["params"]
        kind = step["kind"]
        if kind in ("ocr", "if"):
            return f"· {p.get('expected', '')}"
        if kind == "repeat":
            return f"· {p.get('count', 0)} 次"
        if kind == "wait":
            return f"· {p.get('seconds', 0)} 秒"
        return ""

    def rebuild_tree(self, select_id: str | None = None):
        self.tree.blockSignals(True)
        self.tree.clear()
        self._fill_tree(self.tree, self.workflow.steps)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        if select_id:
            item = self._find_item(select_id)
            if item:
                self.tree.setCurrentItem(item)
        else:
            self._selected_id = None
            self.render_form(None)

    def _find_item(self, sid):
        def walk(item):
            if item.data(0, Qt.UserRole) == sid:
                return item
            for i in range(item.childCount()):
                result = walk(item.child(i))
                if result:
                    return result
            return None
        for i in range(self.tree.topLevelItemCount()):
            result = walk(self.tree.topLevelItem(i))
            if result:
                return result
        return None

    def _container(self, item):
        data = item.data(0, Qt.UserRole) if item else None
        if isinstance(data, tuple):
            return find_step(self.workflow.steps, data[0])[data[1]], len(find_step(self.workflow.steps, data[0])[data[1]])
        if isinstance(data, str):
            parent = item.parent()
            if parent and isinstance(parent.data(0, Qt.UserRole), tuple):
                group = parent.data(0, Qt.UserRole)
                target = find_step(self.workflow.steps, group[0])[group[1]]
            else:
                target = self.workflow.steps
            return target, next(i + 1 for i, step in enumerate(target) if step["id"] == data)
        return self.workflow.steps, len(self.workflow.steps)

    def add_step(self, kind: str):
        target, index = self._container(self.tree.currentItem())
        step = new_step(kind)
        target.insert(index, step)
        self.mark_dirty()
        self.rebuild_tree(step["id"])

    def delete_step(self):
        item = self.tree.currentItem()
        if not item or not isinstance(item.data(0, Qt.UserRole), str):
            return
        target, index = self._container(item)
        target.pop(index - 1)
        self.mark_dirty()
        self.rebuild_tree()

    def move_step(self, direction: int):
        item = self.tree.currentItem()
        if not item or not isinstance(item.data(0, Qt.UserRole), str):
            return
        target, after = self._container(item)
        index = after - 1
        other = index + direction
        if 0 <= other < len(target):
            target[index], target[other] = target[other], target[index]
            self.mark_dirty()
            self.rebuild_tree(target[other]["id"])

    def step_changed(self, item, previous):
        data = item.data(0, Qt.UserRole) if item else None
        self._selected_id = data if isinstance(data, str) else None
        self.render_form(find_step(self.workflow.steps, self._selected_id) if self._selected_id else None)

    def _field(self, p, key, label, kind="text", choices=None):
        value = p.get(key)
        if kind == "choice":
            widget = QComboBox()
            for text, data in choices:
                widget.addItem(text, data)
            widget.setCurrentIndex(max(0, widget.findData(value)))
            widget.currentIndexChanged.connect(lambda: self._set_param(p, key, widget.currentData()))
        elif kind == "int":
            widget = QSpinBox()
            widget.setRange(-100000, 100000)
            widget.setValue(int(value))
            widget.valueChanged.connect(lambda v: self._set_param(p, key, v))
        elif kind == "float":
            widget = QDoubleSpinBox()
            widget.setRange(0, 3600)
            widget.setDecimals(2)
            widget.setValue(float(value))
            widget.valueChanged.connect(lambda v: self._set_param(p, key, v))
        else:
            widget = QLineEdit(str(value))
            widget.textChanged.connect(lambda v: self._set_param(p, key, v))
        self.form.addRow(label, widget)
        return widget

    def _set_param(self, p, key, value):
        p[key] = value
        self.mark_dirty()
        if self._selected_id:
            item = self._find_item(self._selected_id)
            step = find_step(self.workflow.steps, self._selected_id)
            if item and step:
                item.setText(0, f"{NAMES[step['kind']]}  {self._summary(step)}")

    def _picker(self, label, field, mode):
        button = self._button(label, lambda: self.begin_pick(field, mode))
        self.form.addRow("截图选取", button)

    def begin_pick(self, field, mode):
        if not self.preview.pix:
            self.refresh_preview()
        self._pick_field = field
        self.preview.mode = mode
        self.preview_hint.setText("在截图中拖拽区域" if mode == "roi" else "在截图中点击目标位置")

    def preview_picked(self, mode, value):
        step = find_step(self.workflow.steps, self._selected_id) if self._selected_id else None
        if step:
            step["params"][self._pick_field] = value
            self.mark_dirty()
            self.render_form(step)
        self.preview_hint.setText("已采集")

    def render_form(self, step):
        while self.form.rowCount():
            self.form.removeRow(0)
        if not step:
            self.form.addRow(QLabel("选择一个步骤以编辑参数。\n选择“找到时”等分组后可向分支添加步骤。"))
            return
        p, kind = step["params"], step["kind"]
        title = QLabel(NAMES[kind])
        title.setStyleSheet("font-size:16px;font-weight:700;color:#54dbc5;")
        self.form.addRow(title)
        if kind in ("ocr", "if"):
            self._field(p, "expected", "要找的文字")
            self.form.addRow("识别区域", QLabel(str(p["roi"])))
            self._picker("在截图中框选区域", "roi", "roi")
            if kind == "ocr":
                self._field(p, "timeout", "最多等待（秒）", "float")
                self._field(p, "interval", "检查间隔（秒）", "float").setMinimum(0.05)
        elif kind in ("move", "click"):
            self._field(p, "source", "目标位置", "choice", [("截图选点", "point"), ("上次 OCR 结果中心", "ocr")])
            self.form.addRow("坐标", QLabel(str(p["point"])))
            self._picker("在截图中点击位置", "point", "point")
            if kind == "click":
                self._field(p, "button", "鼠标按键", "choice", [("左键", "left"), ("右键", "right"), ("中键", "middle")])
                self._field(p, "count", "点击次数", "int").setRange(1, 2)
        elif kind == "drag":
            for key, label in (("start", "起点"), ("end", "终点")):
                self.form.addRow(label, QLabel(str(p[key])))
                self._picker(f"选择{label}", key, "point")
            self._field(p, "duration", "时长（秒）", "float")
        elif kind == "scroll":
            self._field(p, "dx", "水平滚动", "int")
            self._field(p, "dy", "垂直滚动", "int")
        elif kind == "key":
            self._field(p, "key", "按键")
        elif kind == "text":
            self._field(p, "text", "输入内容")
        elif kind == "wait":
            self._field(p, "seconds", "等待（秒）", "float")
        elif kind == "repeat":
            self._field(p, "count", "重复次数", "int").setRange(1, 1000)
        hint = QLabel("坐标按窗口画面比例保存；更改窗口大小后会自动换算。")
        hint.setWordWrap(True)
        self.form.addRow(hint)

    def _start_worker(self, steps):
        if self.worker and self.worker.isRunning():
            return
        window = self.current_window()
        if not window:
            self._error("请先选择目标窗口")
            return
        self.worker = RunThread(window, json.loads(json.dumps(steps)))
        self.worker.message.connect(self.on_message)
        self.worker.frame.connect(self.preview.set_image)
        self.worker.result.connect(self.on_result)
        self.worker.start()
        self.state.setText("运行中")

    def run_script(self):
        if self.dirty:
            self.save_script()
        self._start_worker(self.workflow.steps)

    def test_selected(self):
        step = find_step(self.workflow.steps, self._selected_id) if self._selected_id else None
        if step:
            steps = [step]
            if step["kind"] in ("move", "click") and step["params"].get("source") == "ocr":
                source = self._recognition_before(self.workflow.steps, step["id"])
                if not source:
                    self._error("该步骤使用 OCR 结果，请先在它之前添加识别文字或判断文字步骤")
                    return
                initial = new_step("ocr")
                initial["params"].update(expected=source["params"]["expected"], roi=source["params"]["roi"])
                steps = [initial, step]
            self._start_worker(steps)

    def _recognition_before(self, steps, target_id):
        latest = None

        def walk(items):
            nonlocal latest
            for item in items:
                if item["id"] == target_id:
                    return True
                if item["kind"] in ("ocr", "if"):
                    latest = item
                for key in ("yes", "no", "body"):
                    if walk(item.get(key, [])):
                        return True
            return False

        walk(steps)
        return latest

    def pause_script(self):
        if self.worker and self.worker.isRunning():
            self.worker.set_paused(not self.worker.paused)
            self.pause_button.setText("继续" if self.worker.paused else "暂停")
            self.state.setText("已暂停" if self.worker.paused else "运行中")

    def stop_script(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.state.setText("正在停止")

    def on_message(self, message, sid):
        self.log.append(message)
        item = self._find_item(sid) if sid else None
        if item:
            self.tree.setCurrentItem(item)

    def on_result(self, message):
        self.log.append(message)
        self.state.setText(message)
        self.pause_button.setText("暂停")
        self.backend = None

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(5000)
        if self.dirty:
            self.save_script()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
