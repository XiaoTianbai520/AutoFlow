# AutoFlow

Windows 本地自动化操作平台。选择窗口，通过步骤列表编排 OCR、鼠标和键盘操作；脚本保存在本机 JSON 文件中。

## 从源码运行

需要 Windows 10/11、Python 3.11+。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scripts\fetch_models.py
.venv\Scripts\python main.py
```

OCR 模型仅在安装准备阶段下载，运行时可离线使用。PP-OCRv4 中文模型同时支持英文和数字。

## 使用

1. 在顶部选择目标窗口，点击“刷新截图”。
2. 新建脚本并添加步骤。在“识别文字”或“判断文字”中框选区域；在鼠标步骤中从截图选取位置。
3. 选中“找到时”“未找到时”或“循环内容”分组，再添加其内部步骤。
4. 点击“测试选中步骤”或“运行脚本”。运行时可暂停、继续、停止。

坐标保存在目标窗口截图的比例位置，不依赖固定的 1280×720 分辨率。部分应用不接受后台 Win32 输入；如遇到截图或输入失败，可尝试让目标窗口处于前台并以相同权限运行 AutoFlow。

## 打包

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

产物位于 `dist\AutoFlow\`，包含离线 OCR 模型。许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
运行时请保留整个 `dist\AutoFlow\` 目录，不要只复制 `AutoFlow.exe`；Qt 和 OCR 依赖位于同目录的 `_internal` 中。

## 脚本格式

脚本位于 `%LOCALAPPDATA%\AutoFlow\scripts`，可通过界面导入导出。顶层字段为 `version`、`name`、`window`、`steps`；`version` 当前为 1。步骤包含 `id`、`kind`、`params`，条件步骤另含 `yes` 和 `no` 数组，循环步骤另含 `body` 数组。
