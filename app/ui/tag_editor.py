import re

from PySide6.QtCore import Qt, Signal, QStringListModel
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QCompleter, QDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.core.image_store import write_src_tags
from app.core.i18n import tr
from app.engines.base import get_engines


class TagEditor(QWidget):
    """右侧标签编辑器：按来源（引擎）独立管理标签，增删改、排序、补全、批量工具"""
    tags_changed = Signal(str, list)  # path, tags
    tag_selected = Signal(int, str)   # 编辑器选中一行 → (行号, 标签文本)

    def __init__(self, settings, store, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.store = store
        self.item = None
        self._loading = False
        self._locked = False  # 批量打标时只锁写入，浏览/选中不受限

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        title_row = QHBoxLayout()
        self.title_lbl = QLabel(tr("标签编辑器"))
        self.title_lbl.setObjectName("TitleLabel")
        self.count_label = QLabel("")
        self.count_label.setObjectName("HintLabel")
        title_row.addWidget(self.title_lbl)
        title_row.addStretch()
        title_row.addWidget(self.count_label)
        lay.addLayout(title_row)

        # 打标来源：标签按来源独立存文件（<图名>.<来源>.txt），编辑只作用于当前来源
        # 「手动框选」是特殊来源 = 画布检测框的标签（只读，在画布上画/改/删）
        filter_row = QHBoxLayout()
        self.src_lbl = QLabel(tr("打标来源"))
        self.src_filter = QComboBox()
        self._src_keys = ["manual"] + [e.key for e in get_engines()] + ["main"]
        self.src_filter.addItems(
            [tr("手动框选")] + [tr(e.title) for e in get_engines()]
            + [tr("旧版 txt")])
        self.src_filter.setToolTip(
            tr("选择当前打标来源：下方标签列表只显示并编辑该来源的标签，"
               "各来源互不混合；「手动框选」= 画布检测框的标签，在画布上画/"
               "改/删框；「旧版 txt」兼容升级前的合并标签文件"))
        filter_row.addWidget(self.src_lbl)
        filter_row.addWidget(self.src_filter, 1)
        lay.addLayout(filter_row)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        lay.addWidget(self.list, 1)

        add_row = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(tr("输入标签后回车添加…"))
        self.completer = QCompleter()
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.edit.setCompleter(self.completer)
        self.btn_add = QPushButton(tr("添加"))
        self.btn_add.setObjectName("AccentBtn")
        add_row.addWidget(self.edit, 1)
        add_row.addWidget(self.btn_add)
        lay.addLayout(add_row)

        row2 = QHBoxLayout()
        self.btn_del = QPushButton(tr("删除选中"))
        self.btn_del.setObjectName("DangerBtn")
        self.btn_clear = QPushButton(tr("清空"))
        self.btn_clear.setObjectName("DangerBtn")
        row2.addWidget(self.btn_del)
        row2.addWidget(self.btn_clear)
        row2.addStretch()
        lay.addLayout(row2)

        row3 = QHBoxLayout()
        self.btn_replace = QPushButton(tr("批量替换…"))
        self.btn_stats = QPushButton(tr("标签统计…"))
        self.btn_black = QPushButton(tr("黑名单…"))
        row3.addWidget(self.btn_replace)
        row3.addWidget(self.btn_stats)
        row3.addWidget(self.btn_black)
        lay.addLayout(row3)

        self.btn_add.clicked.connect(self._add_tag)
        self.edit.returnPressed.connect(self._add_tag)
        self.btn_del.clicked.connect(self._remove_selected)
        self.btn_clear.clicked.connect(self._clear_tags)
        self.btn_replace.clicked.connect(self._open_replace)
        self.btn_stats.clicked.connect(self._open_stats)
        self.btn_black.clicked.connect(self._open_blacklist)

        self.list.model().rowsMoved.connect(self._on_reorder)
        self.list.itemChanged.connect(self._on_item_changed)
        self.list.itemSelectionChanged.connect(self._on_list_sel)
        self.src_filter.currentIndexChanged.connect(
            lambda _i: self.set_item(self.item))

    # ---------- 来源 ----------
    def _cur_src(self) -> str:
        return self._src_keys[self.src_filter.currentIndex()]

    def set_locked(self, locked: bool):
        """批量打标期间：只锁写入路径，浏览与选中保持可用"""
        self._locked = bool(locked)
        self._apply_write_enabled()

    def _apply_write_enabled(self):
        """写入控件可用性：锁定时全禁；手动来源=框的增删改，仅禁用无意义的
        （添加文本标签/跨来源替换统计黑名单），删除/清空/改名可用"""
        from PySide6.QtWidgets import QAbstractItemView
        manual = self._cur_src() == "manual"
        write_btns = (self.edit, self.btn_add, self.btn_del, self.btn_clear,
                      self.btn_replace, self.btn_stats, self.btn_black)
        if self._locked:
            for b in write_btns:
                b.setEnabled(False)
        elif manual:
            for b in write_btns:
                b.setEnabled(b in (self.edit, self.btn_del, self.btn_clear))
        else:
            for b in write_btns:
                b.setEnabled(True)
        self.list.setEditTriggers(
            QAbstractItemView.NoEditTriggers if self._locked
            else QAbstractItemView.DoubleClicked)
        # 手动来源行=框，拖拽重排无意义
        self.list.setDragDropMode(
            QAbstractItemView.InternalMove
            if not self._locked and not manual
            else QAbstractItemView.NoDragDrop)

    # ---------- 数据 ----------
    def set_item(self, item):
        self._loading = True
        self.item = item
        self.list.clear()
        if item:
            if self._cur_src() == "manual":  # 每行 = 一个检测框，一一对应
                for b in item.boxes:
                    it = QListWidgetItem(b.label)
                    it.setFlags(it.flags() | Qt.ItemIsEditable)
                    self.list.addItem(it)
            else:
                for t in item.tags_by_src.get(self._cur_src(), []):
                    it = QListWidgetItem(t)
                    it.setFlags(it.flags() | Qt.ItemIsEditable)
                    self.list.addItem(it)
        self._apply_write_enabled()
        self._update_count()
        self.refresh_completer()
        self._loading = False

    def show_boxes(self) -> bool:
        """当前来源是否伴随检测框：手动框选=框本身，其余看引擎能力"""
        k = self._cur_src()
        if k == "manual":
            return True
        for e in get_engines():
            if e.key == k:
                return getattr(e, "has_boxes", False)
        return False

    def refresh_current(self):
        if self.item:
            self.set_item(self.item)

    def refresh_completer(self):
        model = QStringListModel(self.store.all_tags())
        self.completer.setModel(model)

    def _update_count(self):
        n = self.list.count()
        self.count_label.setText(
            tr("{0} 个标签").format(n) if self.item else tr("未选择图片"))

    def retranslate(self):
        """语言切换后即时重译静态文本"""
        self.title_lbl.setText(tr("标签编辑器"))
        self.src_lbl.setText(tr("打标来源"))
        engines = get_engines()
        for i, title in enumerate(
                [tr("手动框选")] + [tr(e.title) for e in engines]
                + [tr("旧版 txt")]):
            self.src_filter.setItemText(i, title)
        self.src_filter.setToolTip(
            tr("选择当前打标来源：下方标签列表只显示并编辑该来源的标签，"
               "各来源互不混合；「手动框选」= 画布检测框的标签，在画布上画/"
               "改/删框；「旧版 txt」兼容升级前的合并标签文件"))
        self.edit.setPlaceholderText(tr("输入标签后回车添加…"))
        self.btn_add.setText(tr("添加"))
        self.btn_del.setText(tr("删除选中"))
        self.btn_clear.setText(tr("清空"))
        self.btn_replace.setText(tr("批量替换…"))
        self.btn_stats.setText(tr("标签统计…"))
        self.btn_black.setText(tr("黑名单…"))
        self._update_count()

    # ---------- 编辑操作 ----------
    def _add_tag(self):
        if self._cur_src() == "manual":  # 框只能在画布上画
            return
        text = self.edit.text().strip().strip(",")
        if not text or self.item is None or self._locked:
            return
        for part in [p.strip() for p in text.split(",") if p.strip()]:
            it = QListWidgetItem(part)
            it.setFlags(it.flags() | Qt.ItemIsEditable)
            self.list.addItem(it)
        self.edit.clear()
        self._emit()

    def _remove_selected(self):
        if self._cur_src() == "manual" and self.item:
            rows = sorted({self.list.row(i) for i in self.list.selectedItems()},
                          reverse=True)
            for r in rows:
                if 0 <= r < len(self.item.boxes):
                    del self.item.boxes[r]
            if rows:
                self._save_boxes()
            return
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))
        self._emit()

    def _clear_tags(self):
        if self.item is None:
            return
        if QMessageBox.question(self, tr("清空"), tr("清空 {0} 的全部标签？").format(
                self.item.name)) == QMessageBox.Yes:
            if self._cur_src() == "manual":
                self.item.boxes.clear()
                self._save_boxes()
                return
            self.list.clear()
            self._emit()

    def _save_boxes(self):
        """手动来源改动落盘：写 YOLO labels + 重建列表 + 通知画布重绘"""
        from app.core.tag_writer import write_yolo_labels
        try:
            write_yolo_labels(self.item.path, self.item.boxes)
        except Exception:
            pass
        self._loading = True
        self.list.clear()
        for b in self.item.boxes:
            it = QListWidgetItem(b.label)
            it.setFlags(it.flags() | Qt.ItemIsEditable)
            self.list.addItem(it)
        self._loading = False
        self._update_count()
        self.tags_changed.emit(self.item.path, [b.label for b in self.item.boxes])

    def _on_reorder(self, *args):
        if not self._loading:
            self._emit()

    def _on_list_sel(self):
        if self._loading:
            return
        its = self.list.selectedItems()
        if its:
            self.tag_selected.emit(self.list.row(its[0]), its[0].text())
        else:
            self.tag_selected.emit(-1, "")

    def _on_item_changed(self, it):
        if self._loading:
            return
        if self._cur_src() == "manual" and self.item:
            r = self.list.row(it)
            text = it.text().strip()
            if not text:  # 清空文字 = 删除该框
                self._loading = True
                self.list.takeItem(r)
                self._loading = False
                if 0 <= r < len(self.item.boxes):
                    del self.item.boxes[r]
                    self._save_boxes()
                return
            if 0 <= r < len(self.item.boxes):
                self.item.boxes[r].label = text
                self._save_boxes()
            return
        if not it.text().strip():
            self._loading = True
            self.list.takeItem(self.list.row(it))
            self._loading = False
        self._emit()

    def _emit(self):
        """把列表写回当前来源的独立标签文件，并重建合并视图"""
        if self.item is None or self._loading or self._locked:
            return
        tags = [self.list.item(i).text().strip()
                for i in range(self.list.count())]
        tags = [t for t in tags if t]
        key = self._cur_src()
        if key == "manual":  # 框标签由画布维护，不写 txt
            return
        self.item.tags_by_src[key] = tags
        write_src_tags(self.item.path, key, tags)
        self.item.rebuild_merged()
        self._update_count()
        self.tags_changed.emit(self.item.path, tags)

    # ---------- 批量工具 ----------
    def _open_replace(self):
        ReplaceDialog(self.store, self).exec()

    def _open_stats(self):
        StatsDialog(self.store, self).exec()

    def _open_blacklist(self):
        BlacklistDialog(self.settings, self.store, self).exec()


class ReplaceDialog(QDialog):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.parent_editor = parent
        self.setWindowTitle(tr("批量替换标签"))
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.find_edit = QLineEdit()
        self.repl_edit = QLineEdit()
        self.regex_chk = QCheckBox(tr("正则表达式"))
        self.case_chk = QCheckBox(tr("忽略大小写"))
        self.scope = QComboBox()
        self.scope.addItems([tr("全部图片"), tr("当前图片")])
        form.addRow(tr("查找:"), self.find_edit)
        form.addRow(tr("替换为:"), self.repl_edit)
        form.addRow("", self.regex_chk)
        form.addRow("", self.case_chk)
        form.addRow(tr("范围:"), self.scope)
        lay.addLayout(form)
        hint = QLabel(tr("替换为空 = 删除该标签"))
        hint.setObjectName("HintLabel")
        lay.addWidget(hint)
        btn = QPushButton(tr("应用"))
        btn.setObjectName("AccentBtn")
        btn.clicked.connect(self._apply)
        lay.addWidget(btn)

    def _apply(self):
        find = self.find_edit.text()
        if not find:
            return
        repl = self.repl_edit.text()
        flags = re.IGNORECASE if self.case_chk.isChecked() else 0
        try:
            if self.regex_chk.isChecked():
                pat = re.compile(find, flags)
            else:
                pat = re.compile(re.escape(find), flags)
        except re.error as e:
            QMessageBox.warning(self, tr("错误"), tr("正则无效: {0}").format(e))
            return

        def apply(tags):
            out = []
            for t in tags:
                nt = pat.sub(repl, t).strip()
                if nt and nt not in out:
                    out.append(nt)
            return out

        items = self.store.items
        if self.scope.currentIndex() == 1 and self.parent_editor and \
                self.parent_editor.item:
            items = [self.parent_editor.item]
        before = {id(it): list(it.tags) for it in items}
        self.store.rewrite_all(apply, items)
        changed = sum(1 for it in items if it.tags != before[id(it)])
        if self.parent_editor:
            self.parent_editor.refresh_current()
            self.parent_editor.refresh_completer()
        QMessageBox.information(self, tr("完成"), tr("已更新 {0} 张图片").format(changed))


class StatsDialog(QDialog):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.parent_editor = parent
        self.setWindowTitle(tr("标签频率统计"))
        self.setMinimumSize(420, 480)
        lay = QVBoxLayout(self)
        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels([tr("标签"), tr("出现次数")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        lay.addWidget(self.table)
        btn = QPushButton(tr("删除所选标签（所有图片）"))
        btn.setObjectName("DangerBtn")
        btn.clicked.connect(self._delete_selected)
        lay.addWidget(btn)
        self._fill()

    def _fill(self):
        freq = self.store.tag_frequency()
        self.table.setRowCount(len(freq))
        for i, (tag, cnt) in enumerate(freq):
            self.table.setItem(i, 0, QTableWidgetItem(tag))
            self.table.setItem(i, 1, QTableWidgetItem(str(cnt)))

    def _delete_selected(self):
        tags = {self.table.item(r.row(), 0).text()
                for r in self.table.selectionModel().selectedRows()}
        if not tags:
            return
        if QMessageBox.question(
                self, tr("确认"), tr("从所有图片删除 {0} 个标签？").format(len(tags))) != \
                QMessageBox.Yes:
            return
        self.store.rewrite_all(lambda lst: [t for t in lst if t not in tags])
        self._fill()
        if self.parent_editor:
            self.parent_editor.refresh_current()
            self.parent_editor.refresh_completer()


class BlacklistDialog(QDialog):
    def __init__(self, settings, store, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.store = store
        self.parent_editor = parent
        self.setWindowTitle(tr("标签黑名单"))
        self.setMinimumSize(420, 380)
        lay = QVBoxLayout(self)
        hint = QLabel(tr("打标时自动过滤这些标签（逗号或换行分隔）："))
        hint.setObjectName("HintLabel")
        lay.addWidget(hint)
        self.text = QPlainTextEdit()
        self.text.setPlainText(settings.get_blacklist())
        lay.addWidget(self.text, 1)
        self.apply_now = QCheckBox(tr("立即应用到全部图片（删除已有匹配标签）"))
        lay.addWidget(self.apply_now)
        btn = QPushButton(tr("保存"))
        btn.setObjectName("AccentBtn")
        btn.clicked.connect(self._save)
        lay.addWidget(btn)

    def _save(self):
        raw = self.text.toPlainText()
        self.settings.set_blacklist(raw)
        if self.apply_now.isChecked():
            black = {b.strip().lower() for b in
                     raw.replace("\n", ",").split(",") if b.strip()}
            self.store.rewrite_all(
                lambda lst: [t for t in lst if t.lower() not in black])
            if self.parent_editor:
                self.parent_editor.refresh_current()
                self.parent_editor.refresh_completer()
        self.accept()
