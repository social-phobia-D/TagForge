import os

from PySide6.QtCore import QThread, Signal


class EngineLoadWorker(QThread):
    """引擎下载+加载（后台线程）"""
    log_line = Signal(str)
    progress = Signal(int)          # 0-100（下载/加载百分比）
    load_done = Signal(bool, str)

    def __init__(self, engine, params: dict, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.params = params

    def run(self):
        try:
            # progress_cb 可能从 sidecar 读取线程调用，必须是信号 emit（跨线程排队）
            if not self.engine.weights_ready():
                self.engine.download(log_cb=self.log_line.emit,
                                     progress_cb=self.progress.emit)
            self.engine.load(self.params, log_cb=self.log_line.emit,
                             progress_cb=self.progress.emit)
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
            n_eng = max(1, len(self.engines))
            # 进度以「引擎」为最小单位（每步再按引擎自己上报的 0-100 细分），
            # 这样单张图跑多个引擎时进度条不会整张图都停在同一个值。
            total_units = total * n_eng * 100
            for idx, path in enumerate(self.paths):
                if self._stop:
                    break
                item = self.store.find(path)
                if item is None:
                    continue
                path_ok = False
                for ei, eng in enumerate(self.engines):
                    if self._stop:  # 取消后别再进入下一个引擎（会重新拉起 sidecar）
                        break
                    base = (idx * n_eng + ei) * 100

                    def on_prog(v, _b=base, _n=os.path.basename(path)):
                        self.progress.emit(
                            min(_b + int(v), total_units), total_units, _n)

                    try:
                        res = eng.tag_image(
                            path, self.params_by_key.get(eng.key, {}), on_prog)
                        self._merge(item, res, eng.key)
                        path_ok = True
                    except Exception as e:
                        if not self._stop:  # 取消导致的报错不算失败，别刷日志
                            self.failed_one.emit(path, f"{eng.title}: {e}")
                    self.progress.emit(base + 100, total_units,
                                       os.path.basename(path))
                if self._stop:
                    break
                if path_ok:
                    self.ok += 1
                else:
                    self.fail += 1
                self.image_done.emit(path, list(item.tags), list(item.boxes))
                self.progress.emit((idx + 1) * n_eng * 100, total_units,
                                   os.path.basename(path))
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
