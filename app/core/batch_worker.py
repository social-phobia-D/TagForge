import os

from PySide6.QtCore import QThread, Signal


class EngineLoadWorker(QThread):
    """引擎下载+加载（后台线程）"""
    log_line = Signal(str)
    load_done = Signal(bool, str)

    def __init__(self, engine, params: dict, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.params = params

    def run(self):
        try:
            if not self.engine.weights_ready():
                self.engine.download(log_cb=self.log_line.emit)
            self.engine.load(self.params, log_cb=self.log_line.emit)
            self.load_done.emit(True, "加载完成")
        except Exception as e:
            self.load_done.emit(False, str(e))


class BatchWorker(QThread):
    """批量打标：多引擎顺序执行，边打边存"""
    progress = Signal(int, int, str)          # done, total, filename
    image_done = Signal(str, list, list)      # path, tags, boxes
    failed_one = Signal(str, str)             # path, error
    finished_all = Signal(int, int, bool)     # ok, fail, cancelled

    def __init__(self, store, engines, paths, params_by_key: dict,
                 merge_mode: str, trigger: str, blacklist: str, parent=None):
        super().__init__(parent)
        self.store = store
        self.engines = engines
        self.paths = paths
        self.params_by_key = params_by_key
        self.merge_mode = merge_mode
        self.trigger = [t.strip() for t in trigger.split(",") if t.strip()]
        self.blacklist = {b.strip().lower() for b in blacklist.split(",") if b.strip()}
        self._stop = False
        self.ok = 0
        self.fail = 0

    def stop(self):
        self._stop = True
        # 只置标志位的话，取消要等当前这张跑完才生效；sidecar 引擎可能正卡在
        # 一次长推理/首次下载里（数分钟），表现就是"点了取消没反应"。
        for eng in self.engines:
            try:
                eng.abort()
            except Exception:
                pass

    def run(self):
        try:
            total = len(self.paths)
            for idx, path in enumerate(self.paths):
                if self._stop:
                    break
                item = self.store.find(path)
                if item is None:
                    continue
                path_ok = False
                for eng in self.engines:
                    try:
                        res = eng.tag_image(path, self.params_by_key.get(eng.key, {}))
                        self._merge(item, res, eng.key)
                        path_ok = True
                    except Exception as e:
                        if not self._stop:  # 取消导致的报错不算失败，别刷日志
                            self.failed_one.emit(path, f"{eng.title}: {e}")
                if self._stop:
                    break
                if path_ok:
                    self.ok += 1
                else:
                    self.fail += 1
                self.image_done.emit(path, list(item.tags), list(item.boxes))
                self.progress.emit(idx + 1, total, os.path.basename(path))
        finally:
            # 即使 run() 意外崩溃也必须发 finished_all，否则 UI 永久锁死
            # （编辑器锁定不解除 → 删标签不落盘、画布不能选框）
            self.finished_all.emit(self.ok, self.fail, self._stop)

    def _merge(self, item, res, eng_key):
        """引擎结果只写自己的来源文件（<图名>.<来源>.txt），不与其他引擎混合"""
        black = self.blacklist
        new_tags = [t for t in res.tags if t.strip().lower() not in black]
        if self.merge_mode == "replace":
            tags = list(self.trigger)
            for t in new_tags:
                if t not in tags:
                    tags.append(t)
        else:  # append：在该来源已有标签基础上追加
            tags = list(item.tags_by_src.get(eng_key, []))
            for t in self.trigger + new_tags:
                if t not in tags:
                    tags.append(t)
        from app.core.image_store import write_src_tags
        item.tags_by_src[eng_key] = tags
        try:
            write_src_tags(item.path, eng_key, tags)
        except Exception as e:
            self.failed_one.emit(item.path, f"{eng_key}: {e}")
            return
        item.rebuild_merged()
        if res.boxes:
            item.boxes = res.boxes
            from app.core.tag_writer import write_yolo_labels
            try:
                write_yolo_labels(item.path, res.boxes)
            except Exception:
                pass
