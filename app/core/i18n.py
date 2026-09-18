"""轻量双语支持：中文为源语言，EN 字典负责英文翻译。
用法：from app.core.i18n import tr
      tr("打开文件夹")            # 中文环境原样返回
      tr("共 {0} 张").format(3)   # 带参数用 {0} 占位
语言存于 QSettings general/language（zh / en）；切换时各界面调用
retranslate() 即时重译。"""
from PySide6.QtCore import QSettings

_EN = {
    # ---- 主窗口 ----
    "打开文件夹": "Open Folder",
    "刷新": "Refresh",
    "包含子文件夹": "Include subfolders",
    "引擎管理": "Engine Manager",
    "导出CSV": "Export CSV",
    "数据目录": "Data Folder",
    "图片列表": "Images",
    "标签编辑": "Tags",
    "打标控制": "Tagging",
    "框标签": "Box Label",
    "删除选中框": "Delete Selected",
    "拖拽画框 · 1-9 快选标签 · 双击框改标签 · Ctrl+Z 撤销 · Del 删除 · A/D 或 N/P 切图 · 滚轮缩放":
        "Drag to draw · 1-9 pick label · Double-click box to rename · "
        "Ctrl+Z undo · Del delete · A/D or N/P next/prev · Wheel zoom",
    "全部图片": "All images",
    "未打标": "Untagged",
    "已打标": "Tagged",
    "搜索标签或文件名…": "Search tags or filename…",
    "未打开文件夹": "No folder opened",
    "共 {0} 张 · 已打标 {1} · 显示 {2}": "{0} images · {1} tagged · {2} shown",
    "打标引擎（可多选叠加）": "Tagging engines (multi-select allowed)",
    "WD14 通用阈值": "WD14 threshold",
    "WD14 角色阈值": "WD14 character threshold",
    "保留下划线": "Keep underscores",
    "Florence：生成自然语言描述": "Florence: generate caption",
    "Florence：输出物体标签+框": "Florence: object labels + boxes",
    "Florence 短语定位(逗号分隔)": "Florence phrase grounding (comma separated)",
    "red car, person on the left … 留空则不启用":
        "red car, person on the left … leave empty to disable",
    "YOLO-World 类名(逗号分隔)": "YOLO-World classes (comma separated)",
    "YOLO 置信度": "YOLO confidence",
    "LocateAnything 提示词": "LocateAnything prompt",
    "留空 = 自动检测全部物体": "Empty = auto-detect all objects",
    "LocateAnything 类名(逗号分隔)": "LocateAnything classes (comma separated)",
    "LocateAnything 生成模式": "LocateAnything generation mode",
    "混合模式（推荐）": "Hybrid (recommended)",
    "快速模式": "Fast",
    "慢速模式": "Slow",
    "最大生成 token": "Max new tokens",
    "触发词": "Trigger word(s)",
    "如 mylora, style（逗号分隔，可空）": "e.g. mylora, style (comma separated, optional)",
    "替换模式": "Replace mode",
    "追加模式": "Append mode",
    "替换：清空后写入新标签\n追加：保留原标签再合并":
        "Replace: clear then write new tags\nAppend: keep existing tags and merge",
    "打标当前图片": "Tag Current Image",
    "批量打标全部": "Batch Tag All",
    "取消": "Cancel",
    "卸载模型": "Unload Models",
    "打标日志…": "Tagging log…",
    "就绪": "Ready",
    "未检测到 NVIDIA 显卡": "No NVIDIA GPU detected",
    "选择图片文件夹": "Select image folder",
    "选择数据目录（模型/运行时存放位置，放数据盘）":
        "Choose data folder (models/runtime; use a data drive)",
    "已设置数据目录：\n{0}\n重启程序后生效。": "Data folder set:\n{0}\nRestart the app to apply.",
    "提示": "Notice",
    "批量打标进行中，请先取消。": "Batch tagging is running. Cancel it first.",
    "请先选择一张图片": "Select an image first",
    "请先打开文件夹": "Open a folder first",
    "请先勾选至少一个打标引擎": "Check at least one engine first",
    "引擎未就绪": "Engine not ready",
    "「{0}」需要先安装依赖/下载模型。\n现在打开引擎管理？":
        "\"{0}\" needs dependency install / model download.\nOpen Engine Manager now?",
    "以下引擎尚未就绪：{0}\n现在打开引擎管理？":
        "These engines are not ready: {0}\nOpen Engine Manager now?",
    "已导出 {0}": "Exported {0}",
    "准备模型…": "Preparing models…",
    "[{0}] 加载模型…": "[{0}] Loading model…",
    "加载失败: {0}": "Load failed: {0}",
    "正在取消…": "Cancelling…",
    "已取消": "Cancelled",
    "打标完成": "Tagging finished",
    "{0}: 成功 {1}, 失败 {2}": "{0}: {1} ok, {2} failed",
    "打标中 {0}/{1}: {2}": "Tagging {0}/{1}: {2}",
    "失败 {0}: {1}": "Failed {0}: {1}",
    "已卸载全部模型": "All models unloaded",
    "语言已切换": "Language switched",
    "界面语言": "UI language",
    # ---- 引擎管理 ----
    "打标引擎按需安装：只有你点过的组件才会被安装，主程序启动时不会安装任何东西。":
        "Engines install on demand: only components you click get installed. "
        "Nothing is installed when the app starts.",
    "依赖安装到": "Install dependencies to",
    "程序自带运行时（默认，与系统完全隔离）": "Built-in runtime (default, isolated from system)",
    "自定义环境（我授权修改该环境）": "Custom environment (I authorize modifying it)",
    "自定义Python环境": "Custom Python environment",
    "如 F:\\envs\\myenv\\python.exe 或 conda 环境的 python.exe，留空=使用内嵌运行时":
        "e.g. F:\\envs\\myenv\\python.exe or a conda env python.exe; empty = built-in runtime",
    "浏览…": "Browse…",
    "验证": "Verify",
    "「程序自带运行时」与系统完全隔离；「自定义环境」= 你明确授权后向该环境安装依赖。":
        "Built-in runtime is fully isolated from your system; \"custom environment\" = "
        "dependencies are installed into it after your explicit choice.",
    "操作日志…": "Log…",
    "安装依赖": "Install Deps",
    "下载模型": "Download Model",
    "加载": "Load",
    "卸载": "Unload",
    "● 已加载": "● Loaded",
    "● 需要安装依赖": "● Deps required",
    "● 需要下载模型": "● Weights required",
    "● 就绪（未加载）": "● Ready (not loaded)",
    "显存: {0}    模型体积: {1}": "VRAM: {0}    Size: {1}",
    "选择 Python 解释器（python.exe）": "Select Python interpreter (python.exe)",
    "python.exe (python.exe python3.exe);;所有文件 (*)":
        "python.exe (python.exe python3.exe);;All files (*)",
    "路径无效或为空。留空则使用程序自带的独立运行时。":
        "Path invalid or empty. Leave empty to use the built-in runtime.",
    "验证完成（{0}）": "Verified ({0})",
    "√ 依赖齐全: {0}": "√ Dependencies OK: {0}",
    "{0} 缺: {1}": "{0} missing: {1}",
    "安装目标切换为：{0}": "Install target switched to: {0}",
    "开始安装依赖 ...": "installing dependencies ...",
    "开始下载模型（{0}）...": "downloading model ({0}) ...",
    "加载模型 ...": "loading model ...",
    "加载成功": "loaded OK",
    "依赖安装完成": "Deps installed",
    "依赖安装失败": "Deps install failed",
    "依赖已就绪": "Deps already ready",
    "模型下载完成": "Model downloaded",
    "后台任务进行中": "Task in progress",
    "安装/下载还在进行，确定关闭？": "Install/download still running. Close anyway?",
    "确定": "OK",
    "程序自带运行时": "built-in runtime",
    "自定义环境": "custom environment",
    # ---- 标签编辑器 ----
    "标签编辑器": "Tag Editor",
    "打标来源": "Tagging source",
    "手动框选": "Manual boxes",
    "旧版 txt": "Legacy txt",
    "手动下载:": "Manual download:",
    "复制": "Copy",
    "已复制": "Copied",
    "本地权重:": "Local weights:",
    "浏览": "Browse",
    "清除": "Clear",
    "选择权重文件": "Select weights file",
    "选择模型文件夹": "Select model folder",
    "已设置本地权重 {0}，跳过下载": "Local weights set at {0}, skipping download",
    "选包含 model.onnx 和 selected_tags.csv 的文件夹":
        "Folder containing model.onnx and selected_tags.csv",
    "选 Florence-2-base 模型文件夹（含 config.json）":
        "Florence-2-base model folder (with config.json)",
    "选 yolov8s-worldv2.pt 权重文件": "yolov8s-worldv2.pt weights file",
    "选 LocateAnything-3B 模型文件夹（含 config.json）":
        "LocateAnything-3B model folder (with config.json)",
    "选择当前打标来源：下方标签列表只显示并编辑该来源的标签，各来源互不混合；「手动框选」= 画布检测框的标签，在画布上画/改/删框；「旧版 txt」兼容升级前的合并标签文件":
        "Pick the tagging source: the list below shows and edits only that "
        "source's tags; sources are kept separate. \"Manual boxes\" shows the "
        "canvas box labels (draw/edit boxes on the canvas). \"Legacy txt\" "
        "reads the pre-upgrade merged tag files",
    "{0} 个标签": "{0} tags",
    "未选择图片": "No image selected",
    "输入标签后回车添加…": "Type a tag and press Enter…",
    "添加": "Add",
    "删除选中": "Delete Selected",
    "清空": "Clear",
    "批量替换…": "Batch Replace…",
    "标签统计…": "Tag Stats…",
    "黑名单…": "Blacklist…",
    "清空 {0} 的全部标签？": "Clear all tags of {0}?",
    "批量替换标签": "Batch Replace Tags",
    "当前图片": "Current image",
    "替换为空 = 删除该标签": "Replace with empty = delete tag",
    "应用": "Apply",
    "错误": "Error",
    "正则无效: {0}": "Invalid regex: {0}",
    "完成": "Done",
    "已更新 {0} 张图片": "Updated {0} images",
    "标签频率统计": "Tag Frequency",
    "删除所选标签（所有图片）": "Delete selected tags (all images)",
    "确认": "Confirm",
    "从所有图片删除 {0} 个标签？": "Delete {0} tags from all images?",
    "标签黑名单": "Tag Blacklist",
    "打标时自动过滤这些标签（逗号或换行分隔）：":
        "These tags are filtered when tagging (comma or newline separated):",
    "立即应用到全部图片（删除已有匹配标签）":
        "Apply to all images now (delete matching existing tags)",
    "正则表达式": "Regular expression",
    "忽略大小写": "Ignore case",
    "查找:": "Find:",
    "替换为:": "Replace with:",
    "范围:": "Scope:",
    "标签": "Tag",
    "出现次数": "Count",
    "保存": "Save",
    # ---- 引擎名称 / 描述（模型名也要随语言变化）----
    "WD14 动漫标签 (Danbooru)": "WD14 Anime Tags (Danbooru)",
    "SmilingWolf WD-SwinV2 打标器，输出 Danbooru 风格标签，适合动漫/二次元图。依赖随主程序内置。":
        "SmilingWolf WD-SwinV2 tagger, outputs Danbooru-style tags; great for "
        "anime images. Dependencies are built in.",
    "CPU 即可运行，速度约 0.3-1 秒/张": "Runs on CPU, ~0.3-1 s per image",
    "Florence-2 轻量全能 (微软)": "Florence-2 All-rounder (Microsoft)",
    "0.23B 小模型，三合一：自然语言描述 + 自动物体检测 + 短语定位（填短语即检测该目标）。MIT 协议，显存占用极低。":
        "0.23B small model, three-in-one: natural-language caption + automatic "
        "object detection + phrase grounding (type phrases to detect them). "
        "MIT license, very low VRAM.",
    "约 0.9-1.5GB 显存；无显卡可用 CPU（较慢，约 10-30 秒/张）":
        "~0.9-1.5GB VRAM; CPU works without GPU (slower, ~10-30 s/image)",
    "YOLO-World 极速检测 (腾讯)": "YOLO-World Fast Detection (Tencent AI Lab)",
    "毫秒级开放词表检测，批量打标最快。需在下方填写候选类名（英文），模型只找这些类别。":
        "Millisecond open-vocabulary detection, fastest for batch tagging. "
        "Type candidate class names (English) below; the model only finds "
        "those classes.",
    "约 1-2GB 显存，CPU 也可实时": "~1-2GB VRAM; CPU can also run in real time",
    "LocateAnything-3B 全自动物体标签 (NVIDIA)":
        "LocateAnything-3B Auto Labeling (NVIDIA)",
    "英伟达 3B 开放词表检测模型（并行出框解码）：可填类名做检测，也可免提示词自动找物体，输出物体标签+检测框。实测 int4 峰值显存约 5.5GB、单张约 3 秒。":
        "NVIDIA 3B open-vocabulary detection model (parallel box decoding): "
        "fill in class names for detection or auto-detect all objects without "
        "a prompt; outputs object labels + boxes. int4 peak VRAM ~5.5GB, "
        "~3 s per image.",
    "int4 量化峰值约 5.5GB 显存；12GB+ 显卡可跑原精度更快":
        "int4 quantized peak ~5.5GB VRAM; 12GB+ GPUs run original precision faster",
    # ---- 引擎状态（主窗口行内短标签）----
    "已加载": "Loaded",
    "需安装依赖": "Deps required",
    "需下载模型": "Weights required",
    # ---- 窗口标题 ----
    "TagForge · 打标工坊": "TagForge · Tagging Workshop",
    # ---- 画布 ----
    "图片加载失败": "Failed to load image",
    "修改框标签": "Edit box label",
    "修改为：": "New label:",
}

_lang = None


def current_language() -> str:
    global _lang
    if _lang is None:
        _lang = str(QSettings("Dabiao", "dabiao").value("general/language", "zh"))
    return _lang


def set_language(lang: str):
    global _lang
    _lang = "en" if lang == "en" else "zh"
    QSettings("Dabiao", "dabiao").setValue("general/language", _lang)


def tr(s: str) -> str:
    if current_language() == "en":
        return _EN.get(s, s)
    return s
