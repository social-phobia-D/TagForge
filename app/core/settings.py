from PySide6.QtCore import QSettings


class AppSettings:
    """QSettings 持久化封装"""

    def __init__(self):
        self.q = QSettings("Dabiao", "dabiao")

    # --- 通用 ---
    def get_last_dir(self) -> str:
        return str(self.q.value("general/last_dir", ""))

    def set_last_dir(self, v: str):
        self.q.setValue("general/last_dir", v)

    def get_recursive(self) -> bool:
        return self.q.value("general/recursive", "false") in (True, "true", "1")

    def set_recursive(self, v: bool):
        self.q.setValue("general/recursive", v)

    def get_trigger_word(self) -> str:
        return str(self.q.value("general/trigger_word", ""))

    def set_trigger_word(self, v: str):
        self.q.setValue("general/trigger_word", v)

    def get_merge_mode(self) -> str:
        return str(self.q.value("general/merge_mode", "replace"))

    def set_merge_mode(self, v: str):
        self.q.setValue("general/merge_mode", v)

    def get_blacklist(self) -> str:
        return str(self.q.value("general/blacklist", ""))

    def set_blacklist(self, v: str):
        self.q.setValue("general/blacklist", v)

    # --- WD14 ---
    def get_wd14_threshold(self) -> float:
        try:
            return float(self.q.value("wd14/threshold", 0.35))
        except (TypeError, ValueError):
            return 0.35

    def set_wd14_threshold(self, v: float):
        self.q.setValue("wd14/threshold", v)

    def get_wd14_char_threshold(self) -> float:
        try:
            return float(self.q.value("wd14/char_threshold", 0.85))
        except (TypeError, ValueError):
            return 0.85

    def set_wd14_char_threshold(self, v: float):
        self.q.setValue("wd14/char_threshold", v)

    def get_wd14_underscores(self) -> bool:
        return self.q.value("wd14/underscores", "false") in (True, "true", "1")

    def set_wd14_underscores(self, v: bool):
        self.q.setValue("wd14/underscores", v)

    # --- Florence-2 ---
    def get_florence_caption(self) -> bool:
        return self.q.value("florence/caption", "true") in (True, "true", "1")

    def set_florence_caption(self, v: bool):
        self.q.setValue("florence/caption", v)

    def get_florence_objects(self) -> bool:
        return self.q.value("florence/objects", "true") in (True, "true", "1")

    def set_florence_objects(self, v: bool):
        self.q.setValue("florence/objects", v)

    def get_florence_phrases(self) -> str:
        return str(self.q.value("florence/phrases", ""))

    def set_florence_phrases(self, v: str):
        self.q.setValue("florence/phrases", v)

    # --- YOLO-World ---
    def get_yolo_classes(self) -> str:
        return str(self.q.value("yolo/classes", "person, car, dog, cat"))

    def set_yolo_classes(self, v: str):
        self.q.setValue("yolo/classes", v)

    def get_yolo_conf(self) -> float:
        try:
            return float(self.q.value("yolo/conf", 0.25))
        except (TypeError, ValueError):
            return 0.25

    def set_yolo_conf(self, v: float):
        self.q.setValue("yolo/conf", v)

    # --- LocateAnything ---
    def get_la_classes(self) -> str:
        return str(self.q.value("locateanything/classes", ""))

    def set_la_classes(self, v: str):
        self.q.setValue("locateanything/classes", v)

    def get_la_generation_mode(self) -> str:
        mode = str(self.q.value("locateanything/generation_mode", "hybrid"))
        return mode if mode in {"fast", "slow", "hybrid"} else "hybrid"

    def set_la_generation_mode(self, v: str):
        self.q.setValue("locateanything/generation_mode", v)

    def get_la_max_new_tokens(self) -> int:
        try:
            return max(128, min(8192, int(self.q.value(
                "locateanything/max_new_tokens", 2048))))
        except (TypeError, ValueError):
            return 2048

    def set_la_max_new_tokens(self, v: int):
        self.q.setValue("locateanything/max_new_tokens", int(v))
