<div align="center">

# TagForge · 打标工坊

**Local-first AI image tagging for LoRA / dataset training**

紫黑色调的本地图片打标工具，面向 AI 训练数据集制作

[![Platform](https://img.shields.io/badge/platform-Windows_10_11_64bit-blueviolet)](#)
[![Python](https://img.shields.io/badge/python-3.10%2B-8b5cf6)](#)
[![GUI](https://img.shields.io/badge/Qt-PySide6_&_Fluent_Design-8b5cf6)](#)
[![Engines](https://img.shields.io/badge/engines-4,_stackable-8b5cf6)](#)
[![License](https://img.shields.io/badge/license-MIT-4ade80)](#)
[![Privacy](https://img.shields.io/badge/privacy-100%25_local-4ade80)](#)

**[English](#english) · [简体中文](#简体中文)**

</div>

---

<a id="english"></a>

## English

TagForge is a desktop tagging workbench for building image training datasets
(LoRA, DreamBooth, classifiers, detection). Browse a folder, run up to four AI
engines on it — individually or stacked — then refine tags and draw boxes by
hand. **Every engine runs locally. No image is ever uploaded.**

<!-- TODO: screenshots — drop 1–2 PNGs here after publishing
<p align="center"><img src="docs/screenshot_main.png" width="820"/></p>
-->

### Features

- **Image-first layout** — the canvas owns the center; thumbnails, tag editor
  and tagging controls are three toggleable dock panels whose position and
  visibility are remembered between sessions
- **Four tagging engines, stackable** — tick any combination and batch-tag a
  whole folder in the background (progress bar, cancellable)
- **Manual box annotation** — drag to draw, drag to move, corner handles to
  resize, one label per box, YOLO-format files written on every change
- **Tag editor** — add/edit/reorder (drag & drop), autocomplete from the whole
  library, batch replace with regex, tag frequency stats, blacklist
- **Bilingual UI** — Chinese / English, switch in the toolbar, applied instantly
- **Export** — sidecar `.txt` (SD training format), CSV, YOLO boxes under
  `labels/`

### Engines

| Engine | Source | Best for | VRAM |
|---|---|---|---|
| WD14 (Danbooru) | ONNX | anime-style booru tags, zero config | CPU is fine |
| Florence-2 | Microsoft | natural-language captions, object labels, phrase grounding | ~1 GB |
| YOLO-World | Tencent | open-vocabulary detection, type English class names | ~1–2 GB |
| LocateAnything-3B | NVIDIA | prompt-driven boxes, auto-detects everything | ~5.5 GB (int4), ~3 s/image |

VRAM is auto-adapted: FP16 / int4 / CPU is chosen from your GPU, and heavy
engines fall back gracefully if a model fails to load.

### Getting started (from source)

```bash
# Windows 10/11 x64, Python 3.10+  (64-bit only — Qt 6 / onnxruntime have no 32-bit builds)
pip install -r requirements.txt
python main.py
```

> **64-bit required.** Qt 6 and onnxruntime (≥1.16) do not ship 32-bit Windows
> builds, so TagForge cannot support 32-bit systems. Linux and macOS work from
> source (the sidecar runtime is created from a local `python3` venv); packaged
> builds are currently Windows-first.

### Engines install on demand

The app **installs nothing at startup**. The first time you tick an engine you
are guided into **Engine Manager**:

- **WD14** — only ~450 MB of weights (dependencies are already built in)
- **Other engines** — pick where dependencies are installed to, at the top of
  Engine Manager:
  - **Built-in runtime (default)** — an isolated Python inside the data
    folder's `runtime/`; CUDA PyTorch is fetched automatically on NVIDIA GPUs
  - **Custom environment** — point to your own `python.exe` (conda/venv is
    fine) and hit *Verify*. Choosing this option explicitly authorizes
    installing the missing packages into that environment (already-installed
    packages are skipped by pip). A complete custom environment is reused
    read-only even under the default option — zero downloads
- then → download weights → load

Weights and the runtime live next to the program by default (`models/`,
`runtime/`); use the toolbar **Data Folder** button to move them to any data
drive (persisted, applies after restart).

### Manual annotation shortcuts

| Key | Action |
|---|---|
| Drag | draw a box with the current label |
| `1`–`9` | quick-pick a label (first 9 of the label pool) |
| Double-click a box | rename its label |
| `Ctrl+Z` | undo last draw / delete / move / resize |
| `Del` | delete selected box |
| `Esc` | deselect |
| `N` / `P` | next / previous image (canvas-focused only, safe while typing) |

### Build the exe

```bash
pip install pyinstaller
pyinstaller build.spec
```

Output lands in `dist/TagForge/`. The bundle is small because PyTorch is not
included; heavy engines only set up a runtime when actually used, and always
after your confirmation.

### Project structure

```
main.py                 entry point
app/
  core/                 data model, batch worker, settings, GPU check, i18n
  engines/              base + wd14 / florence2 / yolo-world / locate-anything + sidecar
  ui/                   main window, box canvas, tag editor, engine manager, purple-dark theme
build.spec              PyInstaller config
```

### Credits

- [WD14 Tagger](https://huggingface.co/SmilingWolf) — SmilingWolf
- [Florence-2](https://huggingface.co/microsoft/florence-2-base) — Microsoft
- [YOLO-World](https://github.com/AILab-CVC/YOLO-World) — Tencent AI Lab
- LocateAnything-3B — NVIDIA
- [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) — zhiyiYo

### License

[MIT](LICENSE)

---

<a id="简体中文"></a>

## 简体中文

TagForge 是一个桌面打标工作台，用于制作图像训练数据集（LoRA、DreamBooth、
分类器、检测）。打开文件夹，用最多四个 AI 引擎单独或叠加批量打标，再手工
精修标签、画框。**所有引擎全部本地运行，不联网上传任何图片。**

<!-- TODO：发布后在这里放 1–2 张截图
<p align="center"><img src="docs/screenshot_main.png" width="820"/></p>
-->

### 功能

- **图片为主体的布局** — 画布占据中央；缩略图、标签编辑、打标控制是三个
  可开关的停靠面板，位置与显隐自动记忆
- **四个打标引擎，可多选叠加** — 勾选任意组合，后台批量打标整个文件夹
  （进度条、可取消）
- **手工画框标注** — 拖拽画框、点选拖动、角柄缩放，每框一个标签，修改即写
  YOLO 格式文件
- **标签编辑器** — 增删改、拖拽排序、全库自动补全、正则批量替换、标签频率
  统计、黑名单
- **双语界面** — 中文 / English，工具栏右侧切换，即时生效
- **导出** — 同名 `.txt`（SD 训练格式）、CSV、`labels/` 目录下的 YOLO 框

### 引擎

| 引擎 | 来源 | 擅长 | 显存 |
|---|---|---|---|
| WD14 (Danbooru) | ONNX | 动漫风格 booru 标签，零配置 | CPU 即可 |
| Florence-2 | 微软 | 自然语言描述、物体标签、短语定位 | ~1 GB |
| YOLO-World | 腾讯 | 开放词汇检测，填英文类名 | ~1–2 GB |
| LocateAnything-3B | NVIDIA | 按提示出框 / 自动检测全部物体 | int4 峰值约 5.5 GB，约 3 秒/张 |

显存自适应：按显卡自动选择 FP16 / int4 / CPU，重模型加载失败自动降级。

### 运行（源码）

```bash
# Windows 10/11 x64，Python 3.10+（仅支持 64 位 —— Qt 6 与 onnxruntime 均无 32 位版本）
pip install -r requirements.txt
python main.py
```

> **仅支持 64 位系统。** Qt 6 与 onnxruntime（≥1.16）都不再提供 32 位 Windows
> 构建，因此 TagForge 无法支持 32 位系统。Linux / macOS 可从源码运行
> （sidecar 运行时由本地 python3 venv 创建）；打包版目前以 Windows 为主。

### 引擎按需安装

主程序启动**不会安装任何东西**。首次勾选某个引擎时会引导打开**引擎管理**：

- **WD14** — 只需约 450 MB 模型权重（依赖已内置）
- **其他引擎** — 在引擎管理顶部选择**依赖安装到**哪里：
  - **程序自带运行时（默认）** — 装进数据目录 `runtime/` 的独立 Python，
    与系统完全隔离；有 N 卡自动下载 CUDA 版 PyTorch
  - **自定义环境** — 填自己的 `python.exe` 路径（conda/venv 均可）点
    「验证」；选择此档即明确授权向该环境安装缺失依赖（已装过的包 pip
    自动跳过）。依赖齐全的自定义环境在默认档下也会被只读复用、零下载
- 然后 → 下载模型权重 → 加载

权重与运行时默认保存在程序目录（`models/`、`runtime/`）；用工具栏
**数据目录** 按钮可改到任意数据盘（写入配置，重启生效）。

### 手工打标快捷键

| 按键 | 功能 |
|---|---|
| 拖拽 | 用当前标签画框 |
| `1`–`9` | 快选标签（标签池前 9 个） |
| 双击框 | 修改该框标签 |
| `Ctrl+Z` | 撤销上一次 画框 / 删除 / 移动 / 缩放 |
| `Del` | 删除选中框 |
| `Esc` | 取消选中 |
| `N` / `P` | 下一张 / 上一张（仅画布聚焦时生效，不影响打字） |

### 打包 exe

```bash
pip install pyinstaller
pyinstaller build.spec
```

产物在 `dist/TagForge/`。主程序体积小（不含 PyTorch）；重引擎只在实际
使用时才部署运行时，且始终先经用户确认。

### 目录结构

```
main.py                 入口
app/
  core/                 数据模型、批量 worker、设置、GPU 检测、多语言
  engines/              base + wd14 / florence2 / yoloworld / locateanything + sidecar
  ui/                   主窗口、画框画布、标签编辑器、引擎管理、紫黑主题
build.spec              PyInstaller 配置
```

### 致谢

- [WD14 Tagger](https://huggingface.co/SmilingWolf) — SmilingWolf
- [Florence-2](https://huggingface.co/microsoft/florence-2-base) — Microsoft
- [YOLO-World](https://github.com/AILab-CVC/YOLO-World) — 腾讯 AI Lab
- LocateAnything-3B — NVIDIA
- [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) — zhiyiYo

### 许可证

[MIT](LICENSE)
