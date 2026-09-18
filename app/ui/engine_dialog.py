import os
import subprocess
import sys

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
    QMessageBox,
)

from app.engines.base import get_engines, FROZEN
from app.engines.sidecar import (
    check_python_deps, ensure_engine_runtime, sidecar_deps_ready)
from app.core.batch_worker import EngineLoadWorker
from app.core.i18n import tr


class InstallWorker(QThread):
    log_line = Signal(str)
    install_done = Signal(bool, str)

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine

    def run(self):
        try:
            if FROZEN:
                ok = ensure_engine_runtime(self.engine, self.log_line.emit)
                self.install_done.emit(
                    ok, tr("依赖安装完成") if ok else tr("依赖安装失败"))
                return
            pkgs = self.engine.missing_deps()
            if not pkgs:
                self.install_done.emit(True, tr("依赖已就绪"))
                return
            from app.engines.sidecar import install_engine_deps
            ok = install_engine_deps(sys.executable, pkgs, self.log_line.emit)
            self.install_done.emit(ok, tr("依赖安装完成") if ok else tr("依赖安装失败"))
        except Exception as e:
            self.install_done.emit(False, str(e))


class DownloadWorker(QThread):
    log_line = Signal(str)
    download_done = Signal(bool, str)

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._last_pct = -10

    def _on_progress(self, value: int):
        """每 10% 记一行，避免刷屏（进度条在打标控制区，这里是日志备份）"""
        if value - self._last_pct >= 10:
            self._last_pct = value
            self.log_line.emit(tr("下载进度 {0}%").format(value))

    def run(self):
        try:
            self.engine.download(log_cb=self.log_line.emit,
                                 progress_cb=self._on_progress)
            self.download_done.emit(True, tr("模型下载完成"))
        except Exception as e:
            self.download_done.emit(False, str(e))


class EngineCard(QFrame):
    def __init__(self, engine, dialog):
        super().__init__()
        self.engine = engine
        self.dialog = dialog
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)

        row1 = QHBoxLayout()
        title = QLabel(tr(engine.title))
        title.setStyleSheet("font-weight: bold; color: #c4b5fd; font-size: 14px;")
        self.status = QLabel("")
        row1.addWidget(title)
        row1.addStretch()
        row1.addWidget(self.status)
        lay.addLayout(row1)

        desc = QLabel(tr(engine.description))
        desc.setWordWrap(True)
        desc.setObjectName("HintLabel")
        lay.addWidget(desc)

        hint = QLabel(tr("显存: {0}    模型体积: {1}").format(
            tr(engine.vram_note), engine.weights_size))
        hint.setObjectName("HintLabel")
        lay.addWidget(hint)

        # 受限网络：展示手动下载地址（文字可直接选中复制）
        if engine.weight_urls:
            row_u = QHBoxLayout()
            row_u.addWidget(QLabel(tr("手动下载:")))
            self.url_lbl = QLabel("\n".join(engine.weight_urls))
            self.url_lbl.setObjectName("HintLabel")
            self.url_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.url_lbl.setWordWrap(True)
            row_u.addWidget(self.url_lbl, 1)
            self.btn_copy = QPushButton(tr("复制"))
            self.btn_copy.clicked.connect(self._copy_urls)
            row_u.addWidget(self.btn_copy)
            lay.addLayout(row_u)

        # 本地权重：自己下载好模型后指个路，跳过在线下载
        row_c = QHBoxLayout()
        row_c.addWidget(QLabel(tr("本地权重:")))
        self.custom_edit = QLineEdit(engine.custom_weights())
        self.custom_edit.setPlaceholderText(tr(engine.custom_hint))
        self.custom_edit.setReadOnly(True)
        row_c.addWidget(self.custom_edit, 1)
        self.btn_pick = QPushButton(tr("浏览"))
        self.btn_pick.clicked.connect(self._pick_custom)
        self.btn_clear_c = QPushButton(tr("清除"))
        self.btn_clear_c.clicked.connect(self._clear_custom)
        row_c.addWidget(self.btn_pick)
        row_c.addWidget(self.btn_clear_c)
        lay.addLayout(row_c)

        row3 = QHBoxLayout()
        try:
            from qfluentwidgets import FluentIcon, PrimaryPushButton, PushButton
            self.btn_install = PushButton(FluentIcon.SETTING.icon(), tr("安装依赖"))
            self.btn_download = PushButton(FluentIcon.DOWNLOAD.icon(), tr("下载模型"))
            self.btn_load = PrimaryPushButton(FluentIcon.PLAY.icon(), tr("加载"))
        except Exception:
            self.btn_install = QPushButton(tr("安装依赖"))
            self.btn_download = QPushButton(tr("下载模型"))
            self.btn_load = QPushButton(tr("加载"))
        row3.addWidget(self.btn_install)
        row3.addWidget(self.btn_download)
        row3.addWidget(self.btn_load)
        row3.addStretch()
        lay.addLayout(row3)

        self.btn_install.clicked.connect(self._install)
        self.btn_download.clicked.connect(self._download)
        self.btn_load.clicked.connect(self._toggle_load)

    def deps_ready(self) -> bool:
        if FROZEN and self.engine.need_torch:
            return sidecar_deps_ready(self.engine)
        return self.engine.deps_installed()

    def refresh(self):
        if self.engine.loaded:
            self.status.setText(tr("● 已加载"))
            self.status.setStyleSheet("color: #4ade80;")
        elif not self.deps_ready():
            self.status.setText(tr("● 需要安装依赖"))
            self.status.setStyleSheet("color: #fb923c;")
        elif not self.engine.weights_ready():
            self.status.setText(tr("● 需要下载模型"))
            self.status.setStyleSheet("color: #fb923c;")
        else:
            self.status.setText(tr("● 就绪（未加载）"))
            self.status.setStyleSheet("color: #a78bfa;")

        busy = self.dialog.busy
        self.btn_install.setEnabled(not busy and not self.deps_ready())
        self.btn_download.setEnabled(not busy and self.deps_ready()
                                     and not self.engine.weights_ready())
        if self.engine.loaded:
            self.btn_load.setText(tr("卸载"))
            self.btn_load.setEnabled(not busy)
        else:
            self.btn_load.setText(tr("加载"))
            self.btn_load.setEnabled(
                not busy and self.deps_ready() and self.engine.weights_ready())
        try:  # 加载/卸载图标随状态切换
            from qfluentwidgets import FluentIcon
            self.btn_load.setIcon(
                (FluentIcon.CLOSE if self.engine.loaded else FluentIcon.PLAY).icon())
        except Exception:
            pass

    def _copy_urls(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText("\n".join(self.engine.weight_urls))
        self.btn_copy.setText(tr("已复制"))
        QTimer.singleShot(1500, lambda: self.btn_copy.setText(tr("复制")))

    def _pick_custom(self):
        if self.engine.custom_is_file:
            p, _ = QFileDialog.getOpenFileName(self, tr("选择权重文件"))
        else:
            p = QFileDialog.getExistingDirectory(self, tr("选择模型文件夹"))
        if p:
            QSettings("Dabiao", "dabiao").setValue(
                f"weights/{self.engine.key}", p)
            self.custom_edit.setText(p)
            self.dialog.refresh_all()
            self.dialog.engines_changed.emit()

    def _clear_custom(self):
        QSettings("Dabiao", "dabiao").setValue(
            f"weights/{self.engine.key}", "")
        self.custom_edit.setText("")
        self.dialog.refresh_all()
        self.dialog.engines_changed.emit()

    def _install(self):
        self.dialog.install(self.engine)

    def _download(self):
        self.dialog.download(self.engine)

    def _toggle_load(self):
        if self.engine.loaded:
            self.engine.unload()
            self.dialog.refresh_all()
        else:
            self.dialog.load(self.engine)


class EngineDialog(QDialog):
    engines_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("引擎管理"))
        self.setMinimumSize(680, 620)
        self.busy = False
        self._workers = []

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(tr("打标引擎按需安装：只有你点过的组件才会被安装，主程序启动时不会安装任何东西。")))

        q = QSettings("Dabiao", "dabiao")
        os.environ.setdefault("DABIAO_INSTALL_TARGET",
                              str(q.value("general/install_target", "runtime")))
        saved_python = str(q.value("general/custom_python", "") or "").strip()
        os.environ.setdefault("DABIAO_SIDECAR_PYTHON", saved_python)

        # 安装目标：程序自带运行时 / 自定义环境
        tgt_row = QHBoxLayout()
        tgt_row.addWidget(QLabel(tr("依赖安装到")))
        try:
            from qfluentwidgets import ComboBox
            self.target_combo = ComboBox()
        except Exception:
            self.target_combo = QComboBox()
        self.target_combo.addItems(
            [tr("程序自带运行时（默认，与系统完全隔离）"), tr("自定义环境（我授权修改该环境）")])
        self.target_combo.setCurrentIndex(
            1 if os.environ.get("DABIAO_INSTALL_TARGET") == "custom" else 0)
        self.target_combo.currentIndexChanged.connect(self._on_target_changed)
        tgt_row.addWidget(self.target_combo, 1)
        lay.addLayout(tgt_row)

        # 自定义 Python 环境（可选，只读复用，绝不向其安装任何包）
        env_row = QHBoxLayout()
        env_row.addWidget(QLabel(tr("自定义Python环境")))
        self.custom_py = QLineEdit()
        self.custom_py.setText(os.environ.get("DABIAO_SIDECAR_PYTHON", ""))
        self.custom_py.setPlaceholderText(
            tr("如 F:\\envs\\myenv\\python.exe 或 conda 环境的 python.exe，留空=使用内嵌运行时"))
        env_row.addWidget(self.custom_py, 1)
        try:
            from qfluentwidgets import PushButton
            btn_browse = PushButton(tr("浏览…"))
            btn_check = PushButton(tr("验证"))
        except Exception:
            btn_browse = QPushButton(tr("浏览…"))
            btn_check = QPushButton(tr("验证"))
        env_row.addWidget(btn_browse)
        env_row.addWidget(btn_check)
        lay.addLayout(env_row)
        self.env_status = QLabel(
            tr("「程序自带运行时」与系统完全隔离；「自定义环境」= 你明确授权后向该环境安装依赖。"))
        self.env_status.setObjectName("HintLabel")
        self.env_status.setWordWrap(True)
        lay.addWidget(self.env_status)
        btn_browse.clicked.connect(self._browse_python)
        btn_check.clicked.connect(self._check_env)
        self.custom_py.editingFinished.connect(self._apply_custom_python)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setSpacing(10)
        self.cards = []
        for eng in get_engines():
            card = EngineCard(eng, self)
            self.cards.append(card)
            v.addWidget(card)
        v.addStretch()
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(150)
        self.log.setPlaceholderText(tr("操作日志…"))
        lay.addWidget(self.log)

        self.refresh_all()

    # ---------- 公共 ----------
    def _browse_python(self):
        p, _f = QFileDialog.getOpenFileName(
            self, tr("选择 Python 解释器（python.exe）"), "",
            tr("python.exe (python.exe python3.exe);;所有文件 (*)"))
        if p:
            self.custom_py.setText(p)
            self._apply_custom_python()

    def _apply_custom_python(self):
        p = self.custom_py.text().strip()
        os.environ["DABIAO_SIDECAR_PYTHON"] = p
        QSettings("Dabiao", "dabiao").setValue("general/custom_python", p)
        self.refresh_all()

    def _on_target_changed(self, i):
        t = "custom" if i == 1 else "runtime"
        os.environ["DABIAO_INSTALL_TARGET"] = t
        QSettings("Dabiao", "dabiao").setValue("general/install_target", t)
        self._log(tr("安装目标切换为：{0}").format(
            tr("自定义环境") if i == 1 else tr("程序自带运行时")))
        self.refresh_all()

    def _check_env(self):
        p = self.custom_py.text().strip()
        if not p or not os.path.exists(p):
            self.env_status.setText(tr("路径无效或为空。留空则使用程序自带的独立运行时。"))
            return
        results = []
        for e in get_engines():
            if not e.need_torch:
                continue
            missing = check_python_deps(p, e)
            results.append((e, missing))
        ok = [tr(e.title) for e, m in results if not m]
        bad = [tr("{0} 缺: {1}").format(tr(e.title), ", ".join(m))
               for e, m in results if m]
        lines = [tr("验证完成（{0}）").format(p)]
        if ok:
            lines.append(tr("√ 依赖齐全: {0}").format("；".join(ok)))
        for b in bad:
            lines.append("× " + b)
        self.env_status.setText("\n".join(lines))
        self.refresh_all()

    def refresh_all(self):
        for c in self.cards:
            c.refresh()
        self.engines_changed.emit()

    def _log(self, msg: str):
        self.log.appendPlainText(msg)

    def _start(self, worker: QThread):
        self.busy = True
        self._workers.append(worker)
        worker.finished.connect(lambda: self._worker_finished(worker))
        worker.start()

    def _worker_finished(self, worker: QThread):
        if worker in self._workers:
            self._workers.remove(worker)
        if not self._workers:
            self.busy = False
            self.refresh_all()

    # ---------- 动作 ----------
    def install(self, engine: "EngineBase"):
        if self.busy:
            return
        self._log(f"[{tr(engine.title)}] {tr('开始安装依赖 ...')}")
        w = InstallWorker(engine)
        w.log_line.connect(self._log)
        w.install_done.connect(
            lambda ok, msg, e=engine: self._log(f"[{tr(e.title)}] {msg}"))
        self._start(w)

    def download(self, engine):
        if self.busy:
            return
        self._log(f"[{tr(engine.title)}] {tr('开始下载模型（{0}）...').format(engine.weights_size)}")
        w = DownloadWorker(engine)
        w.log_line.connect(self._log)
        w.download_done.connect(
            lambda ok, msg, e=engine: self._log(f"[{tr(e.title)}] {msg}"))
        self._start(w)

    def load(self, engine):
        if self.busy:
            return
        self._log(f"[{tr(engine.title)}] {tr('加载模型 ...')}")
        w = EngineLoadWorker(engine, {})
        w.log_line.connect(self._log)
        state = {"last": -10}

        def on_prog(v, s=state, e=engine):
            if v - s["last"] >= 10:
                s["last"] = v
                self._log(f"[{tr(e.title)}] {tr('加载进度 {0}%').format(v)}")

        w.progress.connect(on_prog)
        w.load_done.connect(
            lambda ok, msg, e=engine: self._log(
                f"[{tr(e.title)}] {tr('加载成功') if ok else tr('加载失败: {0}').format(msg)}"))
        self._start(w)

    def closeEvent(self, ev):
        if self.busy:
            try:
                from qfluentwidgets import MessageBox
                w = MessageBox(tr("后台任务进行中"), tr("安装/下载还在进行，确定关闭？"), self)
                w.yesButton.setText(tr("确定"))
                w.cancelButton.setText(tr("取消"))
                if not w.exec():
                    ev.ignore()
                    return
            except Exception:
                if QMessageBox.question(
                        self, tr("后台任务进行中"),
                        tr("安装/下载还在进行，确定关闭？")) != QMessageBox.Yes:
                    ev.ignore()
                    return
        ev.accept()
