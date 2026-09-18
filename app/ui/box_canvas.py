"""手工标注画布：预览图上拖拽画框 / 点选拖动 / 角柄缩放 / Del 删除。
快捷键：1-9 快选标签 / Ctrl+Z 撤销 / 双击框改标签 / 滚轮缩放（以光标为锚点）。
每次修改自动写入 <图片目录>/labels/<stem>.txt（YOLO 格式）。"""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QImage, QKeySequence, QPainter, \
    QPen, QPixmap
from PySide6.QtWidgets import QLabel

from app.core.image_store import Box
from app.core.i18n import tr
from app.core.tag_writer import write_yolo_labels

HANDLE = 5          # 角柄半宽（屏幕像素）
ACCENT = QColor(167, 139, 250)
ACCENT_HI = QColor(196, 181, 253)


class BoxCanvas(QLabel):
    boxes_changed = Signal()
    label_picked = Signal(str)          # 数字键快选了标签
    box_edit_requested = Signal(object)  # 双击框，请求改名（参数 Box）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(300)
        self.setStyleSheet(
            "background:#0a0413; border:1px solid #2a1a45; border-radius:8px;")
        self.setFocusPolicy(Qt.ClickFocus)
        self.label_provider = lambda: "object"  # 主窗口注入：新建框用的标签
        self.quick_labels = []               # 主窗口注入：1-9 快选池
        self._img = None     # QImage 原图
        self._item = None    # ImageItem
        self._pm = None
        self._pm_size = None
        self._sel = -1
        self._drag = None
        self._locked = False  # 批量打标时锁定编辑，防止与 worker 线程并发写标注文件
        self._boxes_visible = True  # 随打标来源切换：分类来源（WD14 等）不显示框
        self._zoom = 1.0     # 滚轮缩放倍率（1 = 适配窗口）
        self._off = None     # QPointF：缩放后图片左上角在控件内的位置；None = 居中
        self._undo = []      # ("add", idx, box) / ("del", idx, box) / ("set", idx, coords)

    # ---------- 数据 ----------
    @property
    def item(self):
        return self._item

    @property
    def locked(self) -> bool:
        return self._locked

    @locked.setter
    def locked(self, v: bool):
        self._locked = bool(v)
        if self._locked:
            self._drag = None
            self._sel = -1
            self.update()

    def set_boxes_visible(self, v: bool):
        """框显示开关：切到 WD14 等分类来源时隐藏检测框"""
        v = bool(v)
        if v == self._boxes_visible:
            return
        self._boxes_visible = v
        self._sel = -1
        self._drag = None
        self.update()

    def set_item(self, it):
        self._item = it
        self._sel = -1
        self._drag = None
        self._img = None
        self._pm = None        # 换图必须清绘制缓存，否则 paintEvent 一直画旧图
        self._pm_size = None
        self._zoom = 1.0
        self._off = None
        if it is not None:
            it.ensure_box_sources()
            img = QImage(it.path)
            if not img.isNull():
                self._img = img
        if self._img is None:
            self._pm = None
            self.clear()
            self.setText(tr("未选择图片") if it is None else tr("图片加载失败"))
        self.update()

    def retranslate(self):
        """语言切换后刷新空态占位文本"""
        if self._img is None:
            self.setText(tr("未选择图片") if self._item is None
                         else tr("图片加载失败"))

    def refresh_boxes(self):
        """编辑器侧改了 item.boxes 后调用：清选中状态并重绘"""
        self._sel = -1
        self.update()

    def editor_pick(self, row: int, label: str):
        """编辑器选中标签 → 画布对应框高亮。
        row>=0（手动来源行=框序号）直接按行选；否则按标签文本匹配第一个框"""
        if self._item is None or not self._boxes_visible:
            return
        if row >= 0:
            manual = self._item.boxes_by_src.get("manual", [])
            target = manual[row] if row < len(manual) else None
            try:
                self._sel = self._item.boxes.index(target)
            except ValueError:
                self._sel = -1
        else:
            self._sel = -1
            for i, b in enumerate(self._item.boxes):
                if b.label == label:
                    self._sel = i
                    break
        self.update()

    def delete_selected(self):
        if self._boxes_visible and self._item and \
                0 <= self._sel < len(self._item.boxes):
            b = self._item.boxes[self._sel]
            source = self._item.source_for_box(b)
            self._undo.append(("del", self._sel, b, source))
            self._item.remove_box(b)
            self._sel = -1
            self._save()
            self.update()

    def undo_last(self):
        """Ctrl+Z：撤销最近一次 画框/删除/移动/缩放"""
        if not self._undo:
            return
        op = self._undo.pop()
        if self._item is None:
            return
        kind, idx, payload = op[:3]
        if kind == "add":
            self._item.remove_box(payload)
        elif kind == "del":
            source = op[3] if len(op) > 3 else "manual"
            self._item.boxes_by_src.setdefault(source, []).append(payload)
            self._item.rebuild_boxes()
            self._sel = self._item.boxes.index(payload)
        elif kind == "set":
            b = self._item.boxes[idx] if 0 <= idx < len(self._item.boxes) else None
            if b is not None:
                b.x1, b.y1, b.x2, b.y2 = payload
        self._save()
        self.update()

    # ---------- 坐标 ----------
    def _map_geo(self):
        """显示图在控件内的偏移与缩放 → (ox, oy, s)"""
        if self._img is None:
            return 0.0, 0.0, 1.0
        s = min(self.width() / self._img.width(),
                self.height() / self._img.height()) * self._zoom
        ox = (self.width() - self._img.width() * s) / 2
        oy = (self.height() - self._img.height() * s) / 2
        if self._off is not None:
            ox, oy = self._off.x(), self._off.y()
        return ox, oy, s

    def _to_img(self, x, y):
        ox, oy, s = self._map_geo()
        return (x - ox) / s, (y - oy) / s

    def _corner_at(self, x, y, s):
        """屏幕坐标命中选中框角柄 → 0:左上 1:右上 2:左下 3:右下，否则 -1"""
        if self._item is None or not (0 <= self._sel < len(self._item.boxes)):
            return -1
        b = self._item.boxes[self._sel]
        corners = [(b.x1, b.y1), (b.x2, b.y1), (b.x1, b.y2), (b.x2, b.y2)]
        ox, oy, _ = self._map_geo()
        for i, (cx, cy) in enumerate(corners):
            if abs(x - (ox + cx * s)) <= HANDLE and \
                    abs(y - (oy + cy * s)) <= HANDLE:
                return i
        return -1

    def _hit_box(self, px, py):
        """屏幕坐标命中检测框（含框上的标签牌区域），返回索引或 -1"""
        if self._item is None or not self._boxes_visible:
            return -1
        ox, oy, s = self._map_geo()
        fm = None
        for i in range(len(self._item.boxes) - 1, -1, -1):  # 顶层优先
            b = self._item.boxes[i]
            r = QRectF(ox + b.x1 * s, oy + b.y1 * s,
                       (b.x2 - b.x1) * s, (b.y2 - b.y1) * s)
            if r.contains(px, py):
                return i
            if b.label:  # 标签牌画在框顶上方，也允许点它选中框
                fm = fm or QFontMetrics(self.font())
                tw = fm.horizontalAdvance(b.label) + 10
                ty = r.top() - 20 if r.top() >= 20 else r.top() + 1
                if QRectF(r.left(), ty, tw, 19).contains(px, py):
                    return i
        return -1

    # ---------- 鼠标 ----------
    def mousePressEvent(self, ev):
        if self._locked or not self._boxes_visible or self._img is None \
                or ev.button() != Qt.LeftButton:
            return
        pos = ev.position()
        ix, iy = self._to_img(pos.x(), pos.y())
        c = self._corner_at(pos.x(), pos.y(), self._map_geo()[2])
        if c >= 0:
            b = self._item.boxes[self._sel]
            self._drag = ("resize", self._sel, c, (b.x1, b.y1, b.x2, b.y2))
        else:
            hit = self._hit_box(pos.x(), pos.y())
            if hit >= 0:
                self._sel = hit
                b = self._item.boxes[hit]
                self._drag = ("move", hit, (ix, iy), (b.x1, b.y1, b.x2, b.y2))
            else:
                self._sel = -1
                self._drag = ("draw", (ix, iy), (ix, iy))
        self.update()

    def mouseMoveEvent(self, ev):
        if self._drag is None:
            return
        ix, iy = self._to_img(ev.position().x(), ev.position().y())
        m = self._drag[0]
        if m == "draw":
            self._drag = ("draw", self._drag[1], (ix, iy))
        elif m == "move":
            _, i, (sx, sy), (ox1, oy1, ox2, oy2) = self._drag
            dx, dy = ix - sx, iy - sy
            b = self._item.boxes[i]
            w, h = ox2 - ox1, oy2 - oy1
            b.x1 = max(0.0, min(dx + ox1, self._img.width() - w))
            b.y1 = max(0.0, min(dy + oy1, self._img.height() - h))
            b.x2, b.y2 = b.x1 + w, b.y1 + h
        elif m == "resize":
            _, i, c, (ox1, oy1, ox2, oy2) = self._drag
            b = self._item.boxes[i]
            b.x1, b.y1, b.x2, b.y2 = ox1, oy1, ox2, oy2
            if c in (0, 2):
                b.x1 = max(0.0, min(ix, self._img.width()))
            else:
                b.x2 = max(0.0, min(ix, self._img.width()))
            if c in (0, 1):
                b.y1 = max(0.0, min(iy, self._img.height()))
            else:
                b.y2 = max(0.0, min(iy, self._img.height()))
        self.update()

    def mouseReleaseEvent(self, ev):
        if self._drag is None:
            return
        m = self._drag[0]
        if m == "draw":
            (ax, ay), (bx, by) = self._drag[1], self._drag[2]
            x1, x2 = sorted((max(0.0, ax), max(0.0, bx)))
            y1, y2 = sorted((max(0.0, ay), max(0.0, by)))
            x2 = min(x2, self._img.width())
            y2 = min(y2, self._img.height())
            if x2 - x1 > 4 and y2 - y1 > 4:  # 太小视为点击取消
                label = (self.label_provider() or "object").strip() or "object"
                box = Box(label=label, conf=1.0, x1=x1, y1=y1, x2=x2, y2=y2)
                self._item.add_box(box, "manual")
                self._sel = len(self._item.boxes) - 1
                self._undo.append(("add", self._sel, box))
                self._save()
        elif m in ("move", "resize"):
            b = self._item.boxes[self._drag[1]]
            if b.x2 < b.x1:
                b.x1, b.x2 = b.x2, b.x1
            if b.y2 < b.y1:
                b.y1, b.y2 = b.y2, b.y1
            self._undo.append(("set", self._drag[1], self._drag[3]))
            self._save()
        self._drag = None
        self.update()

    # ---------- 滚轮缩放 ----------
    def wheelEvent(self, ev):
        if self._img is None or self._locked:
            ev.ignore()
            return
        delta = ev.angleDelta().y()
        if not delta:
            ev.ignore()
            return
        iw, ih = self._img.width(), self._img.height()
        fit = min(self.width() / iw, self.height() / ih)
        # ponytail: 上限按缩放后最长边 ≤ 8192 限制，避免生成超大 pixmap
        max_zoom = max(1.0, 8192.0 / max(iw, ih))
        new_zoom = min(max_zoom, max(1.0, self._zoom * (1.25 if delta > 0 else 0.8)))
        if abs(new_zoom - self._zoom) < 1e-9:
            return
        cx, cy = ev.position().x(), ev.position().y()
        ox, oy, s = self._map_geo()
        ix, iy = (cx - ox) / s, (cy - oy) / s   # 光标下的图像坐标（缩放锚点）
        s2 = fit * new_zoom
        self._zoom = new_zoom
        if new_zoom <= 1.0001:
            self._off = None                     # 缩回 1 倍复位为适配窗口
        else:
            self._off = self._clamp_off(cx - ix * s2, cy - iy * s2, s2)
        self._pm_size = None
        self.update()

    def _clamp_off(self, ox, oy, s):
        """偏移钳制：图像比视口小的维度居中，大的维度不允许拖出边界"""
        iw, ih = self._img.width() * s, self._img.height() * s

        def c(o, view, img):
            if img <= view:
                return (view - img) / 2
            return min(0.0, max(view - img, o))

        return QPointF(c(ox, self.width(), iw), c(oy, self.height(), ih))

    def mouseDoubleClickEvent(self, ev):
        if self._locked or not self._boxes_visible or self._img is None \
                or ev.button() != Qt.LeftButton:
            return
        pos = ev.position()
        hit = self._hit_box(pos.x(), pos.y())
        if hit >= 0:
            b = self._item.boxes[hit]
            self._sel = hit
            self._drag = None
            self.update()
            self.box_edit_requested.emit(b)
            return
        self.mousePressEvent(ev)  # 没点到框则按普通按下处理（画框）

    def keyPressEvent(self, ev):
        if self._locked or not self._boxes_visible:
            return
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
        elif ev.key() == Qt.Key_Escape:
            self._sel = -1
            self.update()
        elif ev.matches(QKeySequence.Undo):
            self.undo_last()
        elif Qt.Key_1 <= ev.key() <= Qt.Key_9:  # 数字快选标签
            idx = ev.key() - Qt.Key_1
            if idx < len(self.quick_labels):
                self.label_picked.emit(self.quick_labels[idx])
        else:
            super().keyPressEvent(ev)

    def _save(self):
        if self._item is None:
            return
        try:
            self._item.ensure_box_sources()
            write_yolo_labels(self._item.path, self._item.boxes,
                              self._item.boxes_by_src)
        except Exception:
            pass
        self.boxes_changed.emit()

    # ---------- 绘制 ----------
    def resizeEvent(self, ev):
        self._pm_size = None  # 触发重绘缓存
        super().resizeEvent(ev)

    def _ensure_pm(self):
        if self._img is None:
            self._pm = None
            return
        s = self._map_geo()[2]
        tw = max(1, round(self._img.width() * s))
        th = max(1, round(self._img.height() * s))
        key = (self.width(), self.height(), tw, th)
        if self._pm_size != key:
            self._pm = QPixmap.fromImage(self._img).scaled(
                tw, th, Qt.KeepAspectRatio,
                Qt.FastTransformation if s >= 1 else Qt.SmoothTransformation)
            self._pm_size = key

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if self._img is None or self._item is None:
            return
        self._ensure_pm()
        ox, oy, s = self._map_geo()
        p = QPainter(self)
        p.drawPixmap(int(ox), int(oy), self._pm)
        if self._boxes_visible:
            pen_w = max(2, round(1.5 * s))
            fm = p.fontMetrics()
            for i, b in enumerate(self._item.boxes):
                r = QRectF(ox + b.x1 * s, oy + b.y1 * s,
                           (b.x2 - b.x1) * s, (b.y2 - b.y1) * s)
                sel = i == self._sel
                pen = QPen(ACCENT_HI if sel else ACCENT, pen_w)
                p.setPen(pen)
                p.drawRect(r)
                if b.label:
                    tw = fm.horizontalAdvance(b.label) + 10
                    ty = r.top() - 20 if r.top() >= 20 else r.top() + 1
                    p.fillRect(QRectF(r.left(), ty, tw, 19),
                               QColor(124, 58, 237, 225))
                    p.setPen(QPen(Qt.white))
                    p.drawText(r.left() + 5, ty + 14, b.label)
                    p.setPen(pen)
                if sel:
                    p.setBrush(QColor(255, 255, 255))
                    p.setPen(QPen(ACCENT, 1))
                    for cx, cy in ((r.left(), r.top()), (r.right(), r.top()),
                                   (r.left(), r.bottom()), (r.right(), r.bottom())):
                        p.drawRect(QRectF(cx - HANDLE, cy - HANDLE,
                                          HANDLE * 2, HANDLE * 2))
                    p.setBrush(Qt.NoBrush)
        if self._drag and self._drag[0] == "draw":
            (ax, ay), (bx, by) = self._drag[1], self._drag[2]
            p.setPen(QPen(ACCENT_HI, 1, Qt.DashLine))
            p.drawRect(QRectF(ox + min(ax, bx) * s, oy + min(ay, by) * s,
                              abs(bx - ax) * s, abs(by - ay) * s))
        p.end()
