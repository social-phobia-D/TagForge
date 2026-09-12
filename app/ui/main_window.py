"""主窗口：文件夹浏览 / 缩略图 / 引擎面板 / 打标 / 编辑"""
import os

from PySide6.QtCore import QByteArray, QEvent, QSize, QSettings, Qt, QTimer, QThread, Signal
from PySide6.QtGui import QAction, QIcon, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDockWidget, QDoubleSpinBox, QFileDialog,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSlider, QToolBar,
    QVBoxLayout, QWidget,
)

from app.core.batch_worker import BatchWorker, EngineLoadWorker
from app.core.gpu_check import gpu_info
from app.core.i18n import tr, current_language, set_language
from app.core.image_store import ImageStore
from app.ui.box_canvas import BoxCanvas
from app.core.settings import AppSettings
from app.core.tag_writer import export_csv
from app.engines.base import get_engines
from app.ui.engine_dialog import EngineDialog
from app.ui.tag_editor import TagEditor


class ThumbWorker(QThread):
    thumb_ready = Signal(str, QImage)

    def __init__(self, paths, size=168, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.size = size
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        from app.core.thumb_cache import cached_thumb, store_thumb
        for p in self.paths:
            if self._stop:
                break
            img = cached_thumb(p, self.size)
            if img.isNull():
                img = QImage(p)
                if img.isNull():
                    continue
                img = img.scaled(self.size, self.size,
                                 Qt.KeepAspectRatio, Qt.SmoothTransformation)
                store_thumb(p, self.size, img)  # 失败静默，不影响主流程
            self.thumb_ready.emit(p, img)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = AppSettings()
        self.store = ImageStore()
        self.engines = get_engines()
        self.thumb_worker = None
        self.batch = None
        self._load_worker = None
        self._pending_loads = []
        self._paths_to_tag = []
        self._thumb_items = {}
        self.engine_rows = {}
        self._engine_status = {}   # key -> "loaded"/"deps"/"weights"/"ready"

        self.setWindowTitle(tr("TagForge · 打标工坊"))
        self.resize(1500, 900)
        self.setAcceptDrops(True)

        self._build_toolbar()
        self._build_central()
        self._wire()
        self._build_statusbar()

        last = self.settings.get_last_dir()
        if last and os.path.isdir(last):
            self.load_folder(last)

    # ================= UI 构建 =================
    def _build_toolbar(self):
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        self.toolbar = tb

        try:
            from qfluentwidgets import FluentIcon
            ic_open, ic_refresh = FluentIcon.FOLDER, FluentIcon.SYNC
            ic_engines, ic_csv, ic_data = (
                FluentIcon.APPLICATION, FluentIcon.SAVE, FluentIcon.FOLDER_ADD)
        except Exception:
            ic_open = ic_refresh = ic_engines = ic_csv = ic_data = None

        a_open = QAction(tr("打开文件夹"), self)
        if ic_open:
            a_open.setIcon(ic_open.icon())
        a_open.triggered.connect(self.choose_folder)
        tb.addAction(a_open)

        a_refresh = QAction(tr("刷新"), self)
        if ic_refresh:
            a_refresh.setIcon(ic_refresh.icon())
        a_refresh.triggered.connect(self.reload_folder)
        tb.addAction(a_refresh)

        self.chk_recursive = QCheckBox(tr("包含子文件夹"))
        self.chk_recursive.setChecked(self.settings.get_recursive())
        self.chk_recursive.stateChanged.connect(self._on_recursive_changed)
        tb.addWidget(self.chk_recursive)

        tb.addSeparator()
        a_engines = QAction(tr("引擎管理"), self)
        if ic_engines:
            a_engines.setIcon(ic_engines.icon())
        a_engines.triggered.connect(lambda: self.open_engine_dialog())
        tb.addAction(a_engines)

        a_csv = QAction(tr("导出CSV"), self)
        if ic_csv:
            a_csv.setIcon(ic_csv.icon())
        a_csv.triggered.connect(self._export_csv)
        tb.addAction(a_csv)

        a_data = QAction(tr("数据目录"), self)
        if ic_data:
            a_data.setIcon(ic_data.icon())
        a_data.triggered.connect(self._choose_data_dir)
        tb.addAction(a_data)
        self.a_open, self.a_refresh = a_open, a_refresh
        self.a_engines, self.a_csv, self.a_data = a_engines, a_csv, a_data

    def _choose_data_dir(self):
        cur = os.environ.get("DABIAO_DATA_DIR") or ""
        d = QFileDialog.getExistingDirectory(
            self, tr("选择数据目录（模型/运行时存放位置，放数据盘）"), cur)
        if not d:
            return
        QSettings("Dabiao", "dabiao").setValue("general/data_dir", d)
        QMessageBox.information(
            self, tr("数据目录"), tr("已设置数据目录：\n{0}\n重启程序后生效。").format(d))

    def _build_central(self):
        # ---- 中央：图片画布为主体 ----
        central = QWidget()
        cv = QVBoxLayout(central)
        cv.setContentsMargins(6, 6, 6, 6)
        cv.setSpacing(4)

        box_bar = QHBoxLayout()
        self.lbl_box_tag = QLabel(tr("框标签"))
        box_bar.addWidget(self.lbl_box_tag)
        self.box_label = QComboBox()
        self.box_label.setEditable(True)
        self.box_label.setMinimumWidth(180)
        box_bar.addWidget(self.box_label, 1)
        self.btn_box_del = QPushButton(tr("删除选中框"))
        box_bar.addWidget(self.btn_box_del)
        self.hint_lbl = QLabel(
            tr("拖拽画框 · 1-9 快选标签 · 双击框改标签 · Ctrl+Z 撤销 · Del 删除 · A/D 或 N/P 切图 · 滚轮缩放"))
        self.hint_lbl.setObjectName("HintLabel")
        box_bar.addWidget(self.hint_lbl)
        cv.addLayout(box_bar)

        self.preview = BoxCanvas()
        self.preview.label_provider = lambda: (
            self.box_label.currentText().strip() or "object")
        cv.addWidget(self.preview, 1)
        self.setCentralWidget(central)
        # N/P、A/D 全局切图（不再要求画布必须持有焦点），输入框聚焦时自动让路
        for key, fn in (("N", self._next_image), ("P", self._prev_image),
                        ("D", self._next_image), ("A", self._prev_image)):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(self._guard_focus(fn))

        # ---- 左停靠：缩略图列表 ----
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 8, 4, 8)

        filter_row = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItems([tr("全部图片"), tr("未打标"), tr("已打标")])
        self.filter.setMinimumWidth(56)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("搜索标签或文件名…"))
        self.search.setMinimumWidth(56)
        filter_row.addWidget(self.filter, 1)
        filter_row.addWidget(self.search, 2)
        lv.addLayout(filter_row)

        self.count_label = QLabel(tr("未打开文件夹"))
        self.count_label.setObjectName("HintLabel")
        lv.addWidget(self.count_label)

        self.thumb_list = QListWidget()
        self.thumb_list.setViewMode(QListWidget.IconMode)
        self.thumb_list.setIconSize(QSize(168, 168))
        self.thumb_list.setGridSize(QSize(180, 205))
        self.thumb_list.setResizeMode(QListWidget.Adjust)
        self.thumb_list.setWrapping(True)
        self.thumb_list.setSpacing(4)
        self.thumb_list.setUniformItemSizes(True)
        self.thumb_list.setTextElideMode(Qt.ElideRight)
        self.thumb_list.installEventFilter(self)
        self._thumb_compact = None
        # 紧凑模式的行宽必须显式设为视口宽（ListMode 默认行宽=文字自然宽，
        # 长文件名会把行撑到 600px 并出现横向滚动条）
        self._thumb_hint_timer = QTimer(self)
        self._thumb_hint_timer.setSingleShot(True)
        self._thumb_hint_timer.setInterval(100)
        self._thumb_hint_timer.timeout.connect(self._update_compact_hints)
        lv.addWidget(self.thumb_list, 1)

        self.dock_thumbs = QDockWidget(tr("图片列表"), self)
        self.dock_thumbs.setObjectName("dock_thumbs")
        self.dock_thumbs.setWidget(left)
        self.dock_thumbs.setMinimumWidth(140)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_thumbs)

        # ---- 右停靠：标签编辑 ----
        self.editor = TagEditor(self.settings, self.store)
        self.dock_editor = QDockWidget(tr("标签编辑"), self)
        self.dock_editor.setObjectName("dock_editor")
        self.dock_editor.setWidget(self.editor)
        self.dock_editor.setMinimumWidth(260)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock_editor)

        # ---- 下停靠：打标控制（引擎/参数/进度/日志） ----
        ctrl = QWidget()
        rv = QVBoxLayout(ctrl)
        rv.setContentsMargins(8, 8, 8, 8)

        self.eng_box = QGroupBox(tr("打标引擎（可多选叠加）"))
        eg = QVBoxLayout(self.eng_box)
        for eng in self.engines:
            row = QHBoxLayout()
            chk = QCheckBox(tr(eng.title))
            chk.setToolTip(f"{tr(eng.description)}\n{tr(eng.vram_note)}")
            status = QLabel("")
            status.setObjectName("HintLabel")
            row.addWidget(chk)
            row.addStretch()
            row.addWidget(status)
            eg.addLayout(row)
            self.engine_rows[eng.key] = (chk, status)
            chk.toggled.connect(lambda on, e=eng: self._on_engine_toggled(e, on))
        eg.addSpacing(4)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        self.param_widgets = {}  # engine key -> 参数控件列表（随勾选显隐）

        self.lbl_wd14_thr = QLabel(tr("WD14 通用阈值"))
        grid.addWidget(self.lbl_wd14_thr, 0, 0)
        self.wd14_thr = QSlider(Qt.Horizontal)
        self.wd14_thr.setRange(5, 95)
        self.wd14_thr.setValue(int(self.settings.get_wd14_threshold() * 100))
        self.wd14_thr_lbl = QLabel(f"{self.wd14_thr.value() / 100:.2f}")
        grid.addWidget(self.wd14_thr, 0, 1)
        grid.addWidget(self.wd14_thr_lbl, 0, 2)

        self.lbl_wd14_char = QLabel(tr("WD14 角色阈值"))
        grid.addWidget(self.lbl_wd14_char, 1, 0)
        self.wd14_char = QSlider(Qt.Horizontal)
        self.wd14_char.setRange(50, 99)
        self.wd14_char.setValue(int(self.settings.get_wd14_char_threshold() * 100))
        self.wd14_char_lbl = QLabel(f"{self.wd14_char.value() / 100:.2f}")
        grid.addWidget(self.wd14_char, 1, 1)
        grid.addWidget(self.wd14_char_lbl, 1, 2)

        self.wd14_us = QCheckBox(tr("保留下划线"))
        self.wd14_us.setChecked(self.settings.get_wd14_underscores())
        grid.addWidget(self.wd14_us, 2, 0, 1, 3)
        self.param_widgets["wd14"] = [
            self.lbl_wd14_thr, self.wd14_thr, self.wd14_thr_lbl,
            self.lbl_wd14_char, self.wd14_char, self.wd14_char_lbl, self.wd14_us]

        self.fl_caption = QCheckBox(tr("Florence：生成自然语言描述"))
        self.fl_caption.setChecked(self.settings.get_florence_caption())
        self.fl_objects = QCheckBox(tr("Florence：输出物体标签+框"))
        self.fl_objects.setChecked(self.settings.get_florence_objects())
        grid.addWidget(self.fl_caption, 3, 0, 1, 3)
        grid.addWidget(self.fl_objects, 4, 0, 1, 3)

        self.lbl_fl_phrases = QLabel(tr("Florence 短语定位(逗号分隔)"))
        grid.addWidget(self.lbl_fl_phrases, 5, 0)
        self.fl_phrases = QLineEdit()
        self.fl_phrases.setPlaceholderText(tr("red car, person on the left … 留空则不启用"))
        self.fl_phrases.setText(self.settings.get_florence_phrases())
        grid.addWidget(self.fl_phrases, 5, 1, 1, 2)
        self.param_widgets["florence2"] = [
            self.fl_caption, self.fl_objects, self.lbl_fl_phrases, self.fl_phrases]

        self.lbl_yolo_cls = QLabel(tr("YOLO-World 类名(逗号分隔)"))
        grid.addWidget(self.lbl_yolo_cls, 6, 0)
        self.yolo_classes = QLineEdit()
        self.yolo_classes.setPlaceholderText("person, car, dog …")
        self.yolo_classes.setText(self.settings.get_yolo_classes())
        grid.addWidget(self.yolo_classes, 6, 1, 1, 2)

        self.lbl_yolo_conf = QLabel(tr("YOLO 置信度"))
        grid.addWidget(self.lbl_yolo_conf, 7, 0)
        self.yolo_conf = QDoubleSpinBox()
        self.yolo_conf.setRange(0.05, 0.95)
        self.yolo_conf.setSingleStep(0.05)
        self.yolo_conf.setValue(self.settings.get_yolo_conf())
        grid.addWidget(self.yolo_conf, 7, 1)
        self.param_widgets["yoloworld"] = [
            self.lbl_yolo_cls, self.yolo_classes, self.lbl_yolo_conf, self.yolo_conf]

        self.lbl_la_prompt = QLabel(tr("LocateAnything 提示词"))
        grid.addWidget(self.lbl_la_prompt, 8, 0)
        self.la_prompt = QLineEdit()
        self.la_prompt.setPlaceholderText(tr("留空 = 自动检测全部物体"))
        grid.addWidget(self.la_prompt, 8, 1, 1, 2)
        self.param_widgets["locateanything"] = [self.lbl_la_prompt, self.la_prompt]
        eg.addLayout(grid)
        self._update_param_visibility()
        rv.addWidget(self.eng_box)

        act_row = QHBoxLayout()
        self.lbl_trigger = QLabel(tr("触发词"))
        act_row.addWidget(self.lbl_trigger)
        self.trigger_edit = QLineEdit()
        self.trigger_edit.setPlaceholderText(tr("如 mylora, style（逗号分隔，可空）"))
        self.trigger_edit.setText(self.settings.get_trigger_word())
        self.merge_combo = QComboBox()
        self.merge_combo.addItems([tr("替换模式"), tr("追加模式")])
        self.merge_combo.setToolTip(tr("替换：清空后写入新标签\n追加：保留原标签再合并"))
        act_row.addWidget(self.trigger_edit, 1)
        act_row.addWidget(self.merge_combo)
        rv.addLayout(act_row)

        btn_row = QHBoxLayout()
        try:
            from qfluentwidgets import FluentIcon, PrimaryPushButton
            self.btn_tag_one = PrimaryPushButton(FluentIcon.TAG.icon(), tr("打标当前图片"))
            self.btn_tag_all = PrimaryPushButton(FluentIcon.PLAY.icon(), tr("批量打标全部"))
        except Exception:
            self.btn_tag_one = QPushButton(tr("打标当前图片"))
            self.btn_tag_all = QPushButton(tr("批量打标全部"))
        self.btn_tag_one.setObjectName("AccentBtn")
        self.btn_tag_all.setObjectName("AccentBtn")
        self.btn_cancel = QPushButton(tr("取消"))
        self.btn_cancel.setObjectName("DangerBtn")
        self.btn_cancel.setEnabled(False)
        self.btn_unload = QPushButton(tr("卸载模型"))
        btn_row.addWidget(self.btn_tag_one)
        btn_row.addWidget(self.btn_tag_all)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_unload)
        btn_row.addStretch()
        rv.addLayout(btn_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        rv.addWidget(self.progress)

        self.tag_log = QPlainTextEdit()
        self.tag_log.setReadOnly(True)
        self.tag_log.setPlaceholderText(tr("打标日志…"))
        rv.addWidget(self.tag_log, 1)

        self.dock_ctrl = QDockWidget(tr("打标控制"), self)
        self.dock_ctrl.setObjectName("dock_ctrl")
        self.dock_ctrl.setWidget(ctrl)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock_ctrl)

        self._restore_layout()
        self._add_view_toggles()
        # 布局稳定后再复核缩略图紧凑模式（show 级联的 Resize 事件到达时
        # 视口宽度可能还是旧值，导致窄停靠启动时状态残留）
        QTimer.singleShot(0, self._update_thumb_mode)

    def eventFilter(self, obj, ev):
        if obj is self.thumb_list and ev.type() == QEvent.Type.Resize:
            self._update_thumb_mode()
            if self._thumb_compact:
                self._thumb_hint_timer.start()  # 行宽跟随视口（防抖）
        return super().eventFilter(obj, ev)

    def _update_thumb_mode(self):
        """缩略图栏太窄时切换为纯文件名列表，提供更极限的压缩空间"""
        compact = self.thumb_list.viewport().width() < 190
        if compact == self._thumb_compact:
            return
        self._thumb_compact = compact
        self._apply_thumb_mode()

    def _apply_thumb_mode(self):
        if self._thumb_compact is None:
            return
        lst = self.thumb_list
        if self._thumb_compact:
            lst.setViewMode(QListWidget.ListMode)
            lst.setWrapping(False)
            lst.setIconSize(QSize(0, 0))
            lst.setGridSize(QSize())
            lst.setSpacing(1)
            hint = QSize(max(40, lst.viewport().width() - 1), 22)
        else:
            lst.setViewMode(QListWidget.IconMode)
            lst.setWrapping(True)
            lst.setIconSize(QSize(168, 168))
            lst.setGridSize(QSize(180, 205))
            lst.setSpacing(4)
            hint = QSize(180, 200)
        for i in range(lst.count()):
            lst.item(i).setSizeHint(hint)

    def _update_compact_hints(self):
        """紧凑模式行宽跟随视口宽度，超长文件名由 ElideRight 出省略号"""
        if not self._thumb_compact:
            return
        w = max(40, self.thumb_list.viewport().width() - 1)
        for i in range(self.thumb_list.count()):
            self.thumb_list.item(i).setSizeHint(QSize(w, 22))

    def _restore_layout(self):
        q = QSettings("Dabiao", "dabiao")
        st = q.value("main/window_state")
        if st is None:
            # 默认尺寸：中央画布占最大空间
            self.resizeDocks([self.dock_thumbs, self.dock_editor],
                             [270, 320], Qt.Horizontal)
            self.resizeDocks([self.dock_ctrl], [350], Qt.Vertical)
            return
        if isinstance(st, str):
            st = QByteArray(st.encode("utf-8"))
        self.restoreState(st)

    def _add_view_toggles(self):
        """工具栏尾部：三个面板的显示/隐藏开关 + 语言切换"""
        self.toolbar.addSeparator()
        try:
            from qfluentwidgets import FluentIcon
            icons = [FluentIcon.TILES, FluentIcon.LABEL, FluentIcon.SETTING]
        except Exception:
            icons = [None, None, None]
        for dock, ic in zip(
                (self.dock_thumbs, self.dock_editor, self.dock_ctrl), icons):
            act = dock.toggleViewAction()
            if ic:
                act.setIcon(ic.icon())
            self.toolbar.addAction(act)

        self.toolbar.addSeparator()
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["中文", "English"])
        self.lang_combo.setCurrentIndex(
            0 if current_language() == "zh" else 1)
        self.lang_combo.setToolTip(tr("界面语言"))
        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        self.toolbar.addWidget(self.lang_combo)

    def _on_lang_changed(self, i):
        lang = "en" if i == 1 else "zh"
        if lang == current_language():
            return
        set_language(lang)
        self.retranslate()
        try:
            from qfluentwidgets import InfoBar, InfoBarPosition
            InfoBar.success(
                "", tr("语言已切换"),
                parent=self, duration=4000,
                orient=Qt.Horizontal, position=InfoBarPosition.TOP)
        except Exception:
            QMessageBox.information(self, tr("提示"), tr("语言已切换"))

    def _update_engine_texts(self):
        """引擎勾选框的名称/悬浮提示随语言切换"""
        for eng in self.engines:
            chk, _ = self.engine_rows[eng.key]
            chk.setText(tr(eng.title))
            chk.setToolTip(f"{tr(eng.description)}\n{tr(eng.vram_note)}")

    def retranslate(self):
        """语言切换后即时重译全部静态文本"""
        self.setWindowTitle(tr("TagForge · 打标工坊"))
        for a, s in ((self.a_open, "打开文件夹"), (self.a_refresh, "刷新"),
                     (self.a_engines, "引擎管理"), (self.a_csv, "导出CSV"),
                     (self.a_data, "数据目录")):
            a.setText(tr(s))
        self.chk_recursive.setText(tr("包含子文件夹"))
        self.lbl_box_tag.setText(tr("框标签"))
        self.btn_box_del.setText(tr("删除选中框"))
        self.hint_lbl.setText(
            tr("拖拽画框 · 1-9 快选标签 · 双击框改标签 · Ctrl+Z 撤销 · Del 删除 · A/D 或 N/P 切图 · 滚轮缩放"))
        for i, s in enumerate(("全部图片", "未打标", "已打标")):
            self.filter.setItemText(i, tr(s))
        self.search.setPlaceholderText(tr("搜索标签或文件名…"))
        self.dock_thumbs.setWindowTitle(tr("图片列表"))
        self.dock_editor.setWindowTitle(tr("标签编辑"))
        self.dock_ctrl.setWindowTitle(tr("打标控制"))
        self.eng_box.setTitle(tr("打标引擎（可多选叠加）"))
        self.lbl_wd14_thr.setText(tr("WD14 通用阈值"))
        self.lbl_wd14_char.setText(tr("WD14 角色阈值"))
        self.wd14_us.setText(tr("保留下划线"))
        self.fl_caption.setText(tr("Florence：生成自然语言描述"))
        self.fl_objects.setText(tr("Florence：输出物体标签+框"))
        self.lbl_fl_phrases.setText(tr("Florence 短语定位(逗号分隔)"))
        self.fl_phrases.setPlaceholderText(
            tr("red car, person on the left … 留空则不启用"))
        self.lbl_yolo_cls.setText(tr("YOLO-World 类名(逗号分隔)"))
        self.lbl_yolo_conf.setText(tr("YOLO 置信度"))
        self.lbl_la_prompt.setText(tr("LocateAnything 提示词"))
        self.la_prompt.setPlaceholderText(tr("留空 = 自动检测全部物体"))
        self.lbl_trigger.setText(tr("触发词"))
        self.trigger_edit.setPlaceholderText(tr("如 mylora, style（逗号分隔，可空）"))
        self.merge_combo.setItemText(0, tr("替换模式"))
        self.merge_combo.setItemText(1, tr("追加模式"))
        self.merge_combo.setToolTip(
            tr("替换：清空后写入新标签\n追加：保留原标签再合并"))
        self.btn_tag_one.setText(tr("打标当前图片"))
        self.btn_tag_all.setText(tr("批量打标全部"))
        self.btn_cancel.setText(tr("取消"))
        self.btn_unload.setText(tr("卸载模型"))
        self.tag_log.setPlaceholderText(tr("打标日志…"))
        self.lang_combo.setToolTip(tr("界面语言"))
        self._update_engine_texts()
        for eng in self.engines:
            self._apply_engine_status(eng.key)  # 只重译文本，不做任何检查
        self._update_count_label()
        if self.batch is None:
            self.status_msg.setText(tr("就绪"))
        if not self._gpu_found:
            self.gpu_lbl.setText(tr("未检测到 NVIDIA 显卡"))
        self.editor.retranslate()
        self.preview.retranslate()

    def _build_statusbar(self):
        self.status_msg = QLabel(tr("就绪"))
        self.statusBar().addWidget(self.status_msg, 1)
        name, mb = gpu_info()
        self._gpu_found = bool(name)
        gpu_text = f"{name} · {mb // 1024}GB" if name else tr("未检测到 NVIDIA 显卡")
        self.gpu_lbl = QLabel(gpu_text)
        self.statusBar().addPermanentWidget(self.gpu_lbl)

    def _wire(self):
        self.store.changed.connect(self._on_store_changed)
        self.thumb_list.itemSelectionChanged.connect(self._on_select)
        self.filter.currentIndexChanged.connect(lambda _: self.apply_filter())
        # 搜索防抖：每键触发全量过滤在大图库（万张级）会卡，停顿 200ms 再执行
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self.apply_filter)
        self.search.textChanged.connect(lambda _: self._search_timer.start())

        self.wd14_thr.valueChanged.connect(self._on_thr_changed)
        self.wd14_char.valueChanged.connect(self._on_char_changed)
        self.wd14_us.toggled.connect(self.settings.set_wd14_underscores)
        self.fl_caption.toggled.connect(self.settings.set_florence_caption)
        self.fl_objects.toggled.connect(self.settings.set_florence_objects)
        self.fl_phrases.editingFinished.connect(
            lambda: self.settings.set_florence_phrases(self.fl_phrases.text()))
        self.yolo_classes.editingFinished.connect(
            lambda: self.settings.set_yolo_classes(self.yolo_classes.text()))
        self.yolo_conf.valueChanged.connect(self.settings.set_yolo_conf)
        self.trigger_edit.editingFinished.connect(
            lambda: self.settings.set_trigger_word(self.trigger_edit.text()))
        self.merge_combo.currentIndexChanged.connect(
            lambda i: self.settings.set_merge_mode("replace" if i == 0 else "append"))

        self.btn_tag_one.clicked.connect(self.tag_current)
        self.btn_tag_all.clicked.connect(self.tag_all)
        self.btn_cancel.clicked.connect(self._cancel_batch)
        self.btn_unload.clicked.connect(self._unload_models)
        self.preview.boxes_changed.connect(self._on_boxes_changed)
        self.preview.label_picked.connect(
            lambda lb: self.box_label.setEditText(lb))
        self.preview.box_edit_requested.connect(self._rename_box_label)
        self.btn_box_del.clicked.connect(self.preview.delete_selected)
        self.editor.src_filter.currentIndexChanged.connect(
            lambda _i: self._sync_box_visibility())
        self.editor.tags_changed.connect(self._on_editor_tags)
        self.editor.tag_selected.connect(self._on_editor_tag_selected)

    def _on_editor_tags(self, _path, _tags):
        self.apply_filter()
        self._update_count_label()
        if self.editor._cur_src() == "manual":
            self.preview.refresh_boxes()  # 编辑器删/改名框后画布同步

    def _on_editor_tag_selected(self, row, label):
        # 编辑器选中标签 → 画布对应框高亮（手动来源行=框序号，其余按名匹配）
        self.preview.editor_pick(
            row if self.editor._cur_src() == "manual" else -1, label)

    def _sync_box_visibility(self):
        """画布检测框随打标来源显示/隐藏（WD14 等分类来源没有框）"""
        self.preview.set_boxes_visible(self.editor.show_boxes())

    def _on_boxes_changed(self):
        self._refresh_box_label_pool(self.preview.item)
        if self.editor._cur_src() == "manual" and self.editor.item:
            self.editor.set_item(self.editor.item)  # 同步刷新框标签列表

    # ================= 文件夹 =================
    def choose_folder(self):
        d = QFileDialog.getExistingDirectory(
            self, tr("选择图片文件夹"), self.settings.get_last_dir() or "")
        if d:
            self.load_folder(d)

    def reload_folder(self):
        if self.store.folder:
            self.load_folder(self.store.folder)

    def load_folder(self, folder):
        if self.batch and self.batch.isRunning():
            QMessageBox.information(self, tr("提示"), tr("批量打标进行中，请先取消。"))
            return
        self.settings.set_last_dir(folder)
        self.store.set_folder(folder, self.chk_recursive.isChecked())

    def _on_recursive_changed(self):
        self.settings.set_recursive(self.chk_recursive.isChecked())
        if self.store.folder:
            self.reload_folder()

    def _on_store_changed(self):
        self._stop_thumb()
        self.thumb_list.clear()
        self._thumb_items = {}
        for it in self.store.items:
            li = QListWidgetItem(it.name)
            li.setData(Qt.UserRole, it.path)
            self.thumb_list.addItem(li)
            self._thumb_items[it.path] = li
        self._apply_thumb_mode()
        self.thumb_worker = ThumbWorker([it.path for it in self.store.items])
        self.thumb_worker.thumb_ready.connect(self._on_thumb_ready)
        self.thumb_worker.start()
        self.editor.set_item(None)
        self._show_preview(None)
        self.apply_filter()

    def _stop_thumb(self):
        if self.thumb_worker:
            self.thumb_worker.stop()
            self.thumb_worker = None

    def _on_thumb_ready(self, path, image: QImage):
        li = self._thumb_items.get(path)
        if li:
            li.setIcon(QIcon(QPixmap.fromImage(image)))

    # ================= 筛选/统计 =================
    def apply_filter(self):
        mode = self.filter.currentIndex()
        q = self.search.text().strip().lower()
        for path, li in self._thumb_items.items():
            item = self.store.find(path)
            if item is None:
                continue
            vis = True
            if mode == 1 and item.has_tags:
                vis = False
            elif mode == 2 and not item.has_tags:
                vis = False
            if q:
                vis = vis and (q in os.path.basename(path).lower() or
                               any(q in t.lower() for t in item.tags))
            li.setHidden(not vis)
        self._update_count_label()

    def _update_count_label(self):
        if not self.store.items:
            self.count_label.setText(tr("未打开文件夹"))
            return
        total = len(self.store.items)
        tagged = sum(1 for it in self.store.items if it.has_tags)
        shown = sum(1 for li in self._thumb_items.values() if not li.isHidden())
        self.count_label.setText(tr("共 {0} 张 · 已打标 {1} · 显示 {2}").format(
            total, tagged, shown))

    # ================= 选择/预览 =================
    def _selected_item(self):
        lis = self.thumb_list.selectedItems()
        if not lis:
            return None
        return self.store.find(lis[0].data(Qt.UserRole))

    def _on_select(self):
        item = self._selected_item()
        self.editor.set_item(item)
        self._show_preview(item)
        if item:
            self.status_msg.setText(item.path)

    def _show_preview(self, item):
        self.preview.set_item(item)
        self._sync_box_visibility()
        self._refresh_box_label_pool(item)

    def _rename_box_label(self, box):
        """双击框 → 从标签池选一个改名（也可手输新标签）"""
        pool = []
        if self.preview.item:
            pool += [b.label for b in self.preview.item.boxes if b.label]
        pool += self.store.all_tags()[:200]
        seen = set()
        uniq = [x for x in pool if x and not (x in seen or seen.add(x))]
        if box.label and box.label not in uniq:
            uniq.insert(0, box.label)
        new, ok = QInputDialog.getItem(
            self, tr("修改框标签"), tr("修改为："), uniq[:60],
            0 if uniq else -1, True)
        if ok and new.strip():
            box.label = new.strip()
            self.preview._save()
            self.preview.update()
            self._refresh_box_label_pool(self.preview.item)

    def _visible_paths(self):
        return [li.data(Qt.UserRole) for li in self._thumb_items.values()
                if not li.isHidden()]

    def _jump_image(self, step):
        paths = self._visible_paths()
        if not paths:
            return
        cur = self._selected_item()
        i = paths.index(cur.path) if cur and cur.path in paths else -step
        nxt = paths[(i + step) % len(paths)]
        li = self._thumb_items.get(nxt)
        if li:
            self.thumb_list.setCurrentItem(li)
            self.thumb_list.scrollToItem(li)
            self.preview.setFocus()

    def _guard_focus(self, fn):
        """全局快捷键的焦点保护：文本输入控件聚焦时不抢键"""
        from PySide6.QtWidgets import QApplication, QLineEdit, QTextEdit, \
            QPlainTextEdit

        def go():
            w = QApplication.focusWidget()
            if isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit)):
                return
            if isinstance(w, QComboBox) and w.isEditable():
                return
            fn()
        return go

    def _next_image(self):
        self._jump_image(1)

    def _prev_image(self):
        self._jump_image(-1)

    def _refresh_box_label_pool(self, item):
        cur = self.box_label.currentText()
        pool = []
        if item:
            pool += [b.label for b in item.boxes if b.label]
        pool += self.store.all_tags()[:300]
        seen = set()
        uniq = [x for x in pool if not (x in seen or seen.add(x))]
        self.box_label.clear()
        self.box_label.addItems(uniq[:400])
        self.box_label.setEditText(cur)
        self.preview.quick_labels = uniq[:9]

    # ================= 引擎 =================
    def _on_engine_toggled(self, eng, checked):
        if checked:
            if not (eng.deps_installed() and eng.weights_ready()):
                ret = QMessageBox.question(
                    self, tr("引擎未就绪"),
                    tr("「{0}」需要先安装依赖/下载模型。\n现在打开引擎管理？").format(
                        tr(eng.title)),
                    QMessageBox.Yes | QMessageBox.No)
                if ret == QMessageBox.Yes:
                    self.open_engine_dialog()
                if not (eng.deps_installed() and eng.weights_ready()):
                    self._set_engine_checked(eng.key, False)
        else:
            if self.batch and self.batch.isRunning():
                # 批量进行中不允许卸载，恢复勾选
                self._set_engine_checked(eng.key, True)
            elif eng.loaded:
                eng.unload()
        self._update_param_visibility()
        self._refresh_engine_rows()

    def _update_param_visibility(self):
        """只显示已勾选引擎的参数行"""
        for eng in self.engines:
            vis = self.engine_rows[eng.key][0].isChecked()
            for w in self.param_widgets.get(eng.key, []):
                w.setVisible(vis)

    def _set_engine_checked(self, key, on):
        chk = self.engine_rows[key][0]
        chk.blockSignals(True)
        chk.setChecked(on)
        chk.blockSignals(False)

    def _refresh_engine_rows(self):
        """重新检查引擎状态。涉及子进程/文件系统访问，勿在语言切换等
        高频路径调用；切语言用 _apply_engine_status 只重译文本。"""
        for eng in self.engines:
            if eng.loaded:
                k = "loaded"
            elif not eng.deps_installed():
                k = "deps"
            elif not eng.weights_ready():
                k = "weights"
            else:
                k = "ready"
            self._engine_status[eng.key] = k
            self._apply_engine_status(eng.key)

    def _apply_engine_status(self, key):
        k = self._engine_status.get(key)
        status = self.engine_rows[key][1]
        if k is None:
            return
        if k == "loaded":
            status.setText(tr("已加载"))
            status.setStyleSheet("color:#4ade80;")
        elif k == "deps":
            status.setText(tr("需安装依赖"))
            status.setStyleSheet("color:#fb923c;")
        elif k == "weights":
            status.setText(tr("需下载模型"))
            status.setStyleSheet("color:#fb923c;")
        else:
            status.setText(tr("就绪"))
            status.setStyleSheet("color:#a78bfa;")

    def open_engine_dialog(self):
        dlg = EngineDialog(self)
        dlg.engines_changed.connect(self._refresh_engine_rows)
        dlg.exec()
        self._refresh_engine_rows()

    def _params_by_key(self) -> dict:
        return {
            "wd14": {
                "general_threshold": self.wd14_thr.value() / 100.0,
                "char_threshold": self.wd14_char.value() / 100.0,
                "underscores": self.wd14_us.isChecked(),
            },
            "florence2": {
                "caption": self.fl_caption.isChecked(),
                "objects": self.fl_objects.isChecked(),
                "phrases": self.fl_phrases.text().strip(),
            },
            "yoloworld": {
                "classes": self.yolo_classes.text(),
                "conf": self.yolo_conf.value(),
            },
            "locateanything": {
                "prompt": self.la_prompt.text().strip(),
            },
        }

    # ================= 打标 =================
    def tag_current(self):
        item = self._selected_item()
        if not item:
            QMessageBox.information(self, tr("提示"), tr("请先选择一张图片"))
            return
        self._start_tagging([item.path])

    def tag_all(self):
        paths = [it.path for it in self.store.items]
        if not paths:
            QMessageBox.information(self, tr("提示"), tr("请先打开文件夹"))
            return
        self._start_tagging(paths)

    def _start_tagging(self, paths):
        if self.batch and self.batch.isRunning():
            return
        engines = [e for e in self.engines if self.engine_rows[e.key][0].isChecked()]
        if not engines:
            QMessageBox.information(self, tr("提示"), tr("请先勾选至少一个打标引擎"))
            return
        not_ready = [e for e in engines
                     if not (e.deps_installed() and e.weights_ready())]
        if not_ready:
            ret = QMessageBox.question(
                self, tr("引擎未就绪"),
                tr("以下引擎尚未就绪：{0}\n现在打开引擎管理？").format(
                    "、".join(tr(e.title) for e in not_ready)),
                QMessageBox.Yes | QMessageBox.No)
            if ret == QMessageBox.Yes:
                self.open_engine_dialog()
            return

        self._paths_to_tag = paths
        self._tagging_engines = engines
        to_load = [e for e in engines if not e.loaded]
        self._set_busy_tagging(True, tr("准备模型…"))
        if to_load:
            self._pending_loads = to_load
            self._load_next()
        else:
            self._start_batch()

    def _load_next(self):
        if not self._pending_loads:
            self._start_batch()
            return
        eng = self._pending_loads[0]
        self._log(tr("[{0}] 加载模型…").format(tr(eng.title)))
        self._load_worker = EngineLoadWorker(
            eng, self._params_by_key().get(eng.key, {}))
        self._load_worker.log_line.connect(self._log)
        self._load_worker.load_done.connect(self._on_load_done)
        self._load_worker.start()

    def _on_load_done(self, ok, msg):
        if self._pending_loads:
            self._pending_loads.pop(0)
        if not ok:
            self._log(tr("加载失败: {0}").format(msg))
            self.status_msg.setText(tr("加载失败: {0}").format(msg))
            self._set_busy_tagging(False)
            return
        self._refresh_engine_rows()
        self._load_next()

    def _start_batch(self):
        params = self._params_by_key()
        merge = "replace" if self.merge_combo.currentIndex() == 0 else "append"
        self.batch = BatchWorker(
            self.store, self._tagging_engines, self._paths_to_tag, params,
            merge, self.trigger_edit.text(), self.settings.get_blacklist())
        self.batch.progress.connect(self._on_batch_progress)
        self.batch.image_done.connect(self._on_image_done)
        self.batch.failed_one.connect(
            lambda p, e: self._log(tr("失败 {0}: {1}").format(os.path.basename(p), e)))
        self.batch.finished_all.connect(self._on_batch_done)
        self.progress.setRange(0, len(self._paths_to_tag))
        self.progress.setValue(0)
        self.batch.start()

    def _on_batch_progress(self, done, total, name):
        self.progress.setValue(done)
        self.status_msg.setText(tr("打标中 {0}/{1}: {2}").format(done, total, name))

    def _on_image_done(self, path, tags, boxes):
        li = self._thumb_items.get(path)
        if li:
            li.setToolTip(" , ".join(tags[:30]))
        cur = self._selected_item()
        if cur and cur.path == path:
            self.editor.refresh_current()
            self._show_preview(cur)
        self._update_count_label()

    def _on_batch_done(self, ok, fail, cancelled):
        self._set_busy_tagging(False)
        self.editor.refresh_completer()
        self._update_count_label()
        msg = tr("已取消") if cancelled else tr("打标完成")
        self._log(tr("{0}: 成功 {1}, 失败 {2}").format(msg, ok, fail))
        self.status_msg.setText(tr("{0}: 成功 {1}, 失败 {2}").format(msg, ok, fail))

    def _cancel_batch(self):
        if self.batch and self.batch.isRunning():
            self.batch.stop()
            self.status_msg.setText(tr("正在取消…"))

    def _set_busy_tagging(self, busy, label=None):
        self.btn_tag_one.setEnabled(not busy)
        self.btn_tag_all.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)
        # 批量打标期间只锁写入路径（编辑器可浏览可选中，但改动不落盘、
        # 画布不可画框）：避免 worker 线程与 UI 线程并发写同一标注文件
        self.editor.set_locked(busy)
        self.preview.locked = busy
        if busy and label:
            self.status_msg.setText(label)

    def _unload_models(self):
        for eng in self.engines:
            if eng.loaded:
                eng.unload()
        self._refresh_engine_rows()
        self._log(tr("已卸载全部模型"))

    def _log(self, msg):
        self.tag_log.appendPlainText(msg)



    def _on_thr_changed(self, v):
        self.wd14_thr_lbl.setText(f"{v / 100:.2f}")
        self.settings.set_wd14_threshold(v / 100.0)

    def _on_char_changed(self, v):
        self.wd14_char_lbl.setText(f"{v / 100:.2f}")
        self.settings.set_wd14_char_threshold(v / 100.0)

    # ================= 其他 =================
    def _export_csv(self):
        if not self.store.items:
            QMessageBox.information(self, tr("提示"), tr("请先打开文件夹"))
            return
        out, _ = QFileDialog.getSaveFileName(self, tr("导出CSV"), "tags.csv",
                                             "CSV (*.csv)")
        if out:
            export_csv(self.store.items, out)
            self.status_msg.setText(tr("已导出 {0}").format(out))

    # 拖拽文件夹
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            for url in e.mimeData().urls():
                p = url.toLocalFile()
                if p and os.path.isdir(p):
                    e.acceptProposedAction()
                    return

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p and os.path.isdir(p):
                self.load_folder(p)
                return

    def closeEvent(self, ev):
        if self.batch and self.batch.isRunning():
            self.batch.stop()
            self.batch.wait(3000)
        QSettings("Dabiao", "dabiao").setValue(
            "main/window_state", self.saveState())
        self._stop_thumb()
        for eng in self.engines:
            if eng.loaded:
                eng.unload()
        ev.accept()
