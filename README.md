# TagForge · 打标工坊

> **本地 AI 图片打标、图像描述、目标检测与数据集标注工具。**
> 面向 LoRA / Stable Diffusion / YOLO 训练数据制作，支持 Windows 桌面端批量处理图片、生成标签与描述、绘制检测框并导出训练数据。

<div align="center">

[![Latest Release](https://img.shields.io/github/v/release/social-phobia-D/TagForge?display_name=tag&sort=semver&color=8b5cf6)](https://github.com/social-phobia-D/TagForge/releases)
[![Platform](https://img.shields.io/badge/platform-Windows_10%2F11_x64-2563eb)](https://github.com/social-phobia-D/TagForge/releases)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776ab)](https://www.python.org/)
[![Qt](https://img.shields.io/badge/GUI-PySide6-41cd52)](https://doc.qt.io/qtforpython/)
[![License](https://img.shields.io/badge/license-MIT-22c55e)](LICENSE)

**AI image tagging · image captioning · dataset annotation · LoRA · YOLO · WD14 · Florence-2 · YOLO-World · LocateAnything-3B**

[下载 Windows 版](https://github.com/social-phobia-D/TagForge/releases/latest) · [查看源码](https://github.com/social-phobia-D/TagForge) · [提交问题](https://github.com/social-phobia-D/TagForge/issues)

</div>

## 项目定位

TagForge 是一个 **local-first 的 AI 图片数据集工作台**。它把“浏览图片、批量推理、整理标签、绘制框、导出数据”放在一个桌面应用里完成，适合以下场景：

- 为 **LoRA / Stable Diffusion** 准备图片标签和自然语言描述
- 为 **YOLO / 目标检测** 数据集批量生成或手工修正边界框
- 对一批图片执行 AI 自动打标，再人工复核和统一标签
- 在不上传原图的前提下，管理本地训练素材和标注结果

它不是在线图片生成器，也不是训练框架；它专注于 **训练数据准备和标注**。模型权重、Python 依赖和 CUDA 组件会在用户选择相应引擎后按需下载，图片推理在本机完成。

## 核心能力

- **批量图片打标**：打开文件夹，选择单张或多张图片，在后台执行推理，支持进度显示和取消。
- **多引擎组合**：四个引擎可单独使用，也可以按顺序组合，分别生成标签、描述或检测框。
- **标签整理**：标签增删改、拖拽排序、自动补全、正则批量替换、频率统计和黑名单过滤。
- **手工框标注**：拖拽创建框，移动、缩放、改名和删除；手工结果与 AI 来源分开保存，重新推理不会覆盖手工标注。
- **双语界面**：中文 / English 即时切换，界面主题、面板位置和显隐状态可记忆。
- **本地数据管理**：模型、运行时和设置默认保存在程序所在数据目录，也可以在应用内切换到其他磁盘。
- **多格式导出**：导出同名 `.txt` 标签文件、CSV，以及 `labels/` 下的 YOLO 框标注和来源元数据。

## 四个 AI 引擎

| 引擎 | 适合任务 | 主要输出 | 运行方式 |
|---|---|---|---|
| **WD14** | 二次元 / Danbooru 风格标签 | 角色、画面和风格标签 | ONNX，CPU 也可运行 |
| **Florence-2** | 通用图像理解 | 自然语言描述、物体标签、短语定位框 | Transformers，支持 CPU / GPU |
| **YOLO-World** | 已知类别的开放词汇检测 | 按输入类别生成目标框 | 输入英文类别名，适合批量检测 |
| **LocateAnything-3B** | 提示词驱动的通用检测 | 物体标签与检测框 | NVIDIA 模型，显存需求较高 |

> 四个引擎的输出并不相同：WD14 更偏向图像标签分类；Florence-2、YOLO-World 和 LocateAnything-3B 更适合描述、类别定位或目标检测。显存不足时，应用会尝试降级到更低精度或 CPU 模式，但实际速度取决于显卡、图片尺寸和模型配置。

## 快速开始

### Windows 便携版（推荐）

1. 前往 [Releases](https://github.com/social-phobia-D/TagForge/releases) 下载 `TagForge_win64.zip`。
2. 解压完整压缩包，不要只复制其中的 EXE。
3. 打开解压后的 `TagForge/` 文件夹，运行 `TagForge.exe`。
4. 在 **引擎管理** 中选择引擎，按提示下载依赖和模型，或指定已有的本地环境 / 模型目录。

便携版必须保留以下结构：

```text
TagForge/
├─ TagForge.exe
└─ _internal/
```

如果 Windows 报告 `QtGui`、`QtCore` 或其他 DLL 无法加载，请用最新 Release 压缩包整体替换旧的 `TagForge/` 文件夹，不要只替换 EXE。

### 从源码运行

```powershell
# Windows 10/11 x64，Python 3.10+
pip install -r requirements.txt
python main.py
```

Qt 6、onnxruntime 和部分深度学习依赖只提供 64 位构建。源码运行支持本地 Python 环境；官方便携版当前以 Windows x64 为主。

## 模型和依赖

主程序启动时不会自动安装重量级依赖。首次使用某个引擎时，在 **引擎管理** 中选择：

- **程序自带运行时**：创建隔离的 Python 运行时，适合不想污染系统环境的用户。
- **自定义 Python 环境**：选择已有的 conda / venv / Python 环境并验证，缺少的依赖会在用户确认后安装。
- **已有模型目录**：选择包含 `config.json`、权重文件和 tokenizer 配置的模型目录。Hugging Face 的 `snapshots/<revision>/` 目录通常就是可选目录。

常见模型来源：

- WD14：SmilingWolf / Hugging Face
- Florence-2：Microsoft / Hugging Face
- YOLO-World：Tencent AI Lab / Ultralytics
- LocateAnything-3B：NVIDIA / Hugging Face

模型权重通常体积较大，首次加载需要网络下载；下载完成后，图片推理可以在本地离线进行。

## 输出文件

对图片目录执行标注后，常见结果如下：

```text
images/
├─ image_001.png
├─ image_001.txt                 # Stable Diffusion / LoRA 标签或描述
└─ labels/
   ├─ image_001.txt              # YOLO 检测框
   └─ image_001.sources.json     # 框的来源信息
```

此外可从界面导出 CSV。标准 YOLO 文件保持可直接用于常见训练工具，来源元数据单独保存，便于后续复核和重新推理。

## 导出可训练的 YOLO 数据集

如果当前图片已经通过 Florence-2、YOLO-World、LocateAnything-3B 或手工画框生成了检测框，可以使用工具栏中的两个按钮：

1. **划分训练/验证集**：选择一个空的外部目录，按比例复制图片和框标注，生成 `images/train`、`images/val`、`labels/train`、`labels/val` 和根目录 `classes.txt`。原始图片目录不会被修改。
2. **生成 `data.yaml`**：选择刚才的数据集目录，程序读取 `classes.txt` 并生成 Ultralytics 可用的 `data.yaml`。

最终目录类似：

```text
my_dataset/
├─ data.yaml
├─ classes.txt
├─ images/train/
├─ images/val/
├─ labels/train/
└─ labels/val/
```

只有带检测框的引擎结果会进入 YOLO 标签；WD14 的图像级标签仍保存在对应的标签文本文件中，不会被当作检测框导出。
## 手工标注快捷键

| 按键 | 功能 |
|---|---|
| 拖拽 | 使用当前标签绘制检测框 |
| `1`–`9` | 快速选择标签池中的前 9 个标签 |
| 双击框 | 修改框标签 |
| `Ctrl+Z` | 撤销绘制、删除、移动或缩放 |
| `Del` | 删除选中的框 |
| `Esc` | 取消选择 |
| `N` / `P` | 下一张 / 上一张图片（画布聚焦时） |

## 从源码打包

```powershell
pip install pyinstaller
pyinstaller build.spec
```

构建结果位于 `dist/TagForge/`。PyTorch 等重量级训练依赖不会打进主程序，模型引擎按需安装，便携版因此更适合作为图像标注工具分发。

## 隐私与网络行为

- 原始图片不会自动上传到 TagForge 服务；推理在本机执行。
- 使用某个新引擎时，程序可能从 Hugging Face、Ultralytics 或其他模型源下载权重和依赖。
- 应用会把模型、运行时、设置和日志写入本地数据目录；用户可以在界面中调整数据目录。

## 项目结构

```text
main.py                 # 程序入口
app/core/               # 数据模型、设置、批处理、导出和多语言
app/engines/            # WD14、Florence-2、YOLO-World、LocateAnything-3B
app/ui/                 # 主窗口、画布、标签编辑器、引擎管理
build.spec              # PyInstaller 配置
```

## v1.0.0

首个正式发布版本，包含：

- Windows x64 便携版
- WD14、Florence-2、YOLO-World、LocateAnything-3B 四个引擎
- 批量图片打标、自然语言描述、开放词汇检测和手工框标注
- 标签编辑、来源隔离、CSV / TXT / YOLO 导出
- 中英文界面、按需安装引擎依赖、本地模型目录复用

## English

**TagForge** is a local-first desktop application for **AI image tagging, image captioning, object detection, and dataset annotation**. It is designed for preparing LoRA / Stable Diffusion and YOLO training datasets on Windows.

Use it to batch-process an image folder, run one or more local AI engines, review and edit tags, draw or correct bounding boxes, and export TXT / CSV / YOLO-compatible annotations. The application does not upload source images for inference; model weights and runtime dependencies are downloaded only when the user enables an engine.

### Search keywords

`AI image tagging`, `automatic image annotation`, `image captioning`, `dataset labeling`, `LoRA dataset`, `Stable Diffusion tags`, `YOLO annotation`, `WD14 tagger`, `Florence-2`, `YOLO-World`, `LocateAnything-3B`, `local Windows AI tool`, `bounding box annotation`.

### Portable build

Download `TagForge_win64.zip` from the [latest GitHub Release](https://github.com/social-phobia-D/TagForge/releases/latest), extract the complete archive, and launch `TagForge/TagForge.exe`. Keep `_internal/` beside the executable. Models and heavy engine dependencies are installed on demand.

### Source development

```bash
pip install -r requirements.txt
python main.py
pyinstaller build.spec
```

The project is MIT-licensed. See [LICENSE](LICENSE) for details.

## Credits

- [WD14 Tagger](https://huggingface.co/SmilingWolf) — SmilingWolf
- [Florence-2](https://huggingface.co/microsoft/Florence-2-base) — Microsoft
- [YOLO-World](https://github.com/AILab-CVC/YOLO-World) — Tencent AI Lab
- [LocateAnything-3B](https://huggingface.co/nvidia/LocateAnything-3B) — NVIDIA
- [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) — zhiyiYo

## License

[MIT](LICENSE)