import os
import re

from app.engines.base import EngineBase, EngineResult
from app.engines.base import Box

MODEL_ID = "microsoft/Florence-2-base"


class Florence2Engine(EngineBase):
    key = "florence2"
    title = "Florence-2 轻量全能 (微软)"
    description = "0.23B 小模型，三合一：自然语言描述 + 自动物体检测 + 短语定位（填短语即检测该目标）。MIT 协议，显存占用极低。"
    vram_note = "约 0.9-1.5GB 显存；无显卡可用 CPU（较慢，约 10-30 秒/张）"
    pip_deps = ["torch", "transformers", "accelerate", "einops", "safetensors"]
    weights_size = "约 1 GB"
    need_torch = True
    has_boxes = True
    weight_urls = [f"https://hf-mirror.com/{MODEL_ID}", f"https://huggingface.co/{MODEL_ID}"]
    custom_hint = "选 Florence-2-base 模型文件夹（含 config.json）"

    def _cache_dir(self) -> str:
        return os.path.join(self.models_dir(), "florence2")

    def weights_ready(self) -> bool:
        cw = self.custom_weights()
        if cw:
            return os.path.isfile(os.path.join(cw, "config.json"))
        d = self._cache_dir()
        return os.path.isdir(d) and any(
            x.startswith("models--microsoft--Florence-2-base") for x in os.listdir(d))

    def _download_impl(self, models_dir: str, log_cb=None):
        import huggingface_hub
        if log_cb:
            log_cb(f"Florence-2: 下载 {MODEL_ID}（{self.weights_size}）...")
        huggingface_hub.snapshot_download(MODEL_ID, cache_dir=self._cache_dir())

    def _load_impl(self, models_dir: str, params: dict, log_cb=None):
        from app.engines._tcompat import patch_legacy_generation_attrs
        patch_legacy_generation_attrs()
        import importlib
        if log_cb:
            log_cb("Florence-2: 导入 torch/transformers（首次约 10-20 秒）...")
        torch = importlib.import_module("torch")
        transformers = importlib.import_module("transformers")
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        cw = (params or {}).get("custom_weights") or self.custom_weights()
        src = cw or MODEL_ID
        kw = {} if cw else {"cache_dir": self._cache_dir()}
        if log_cb:
            log_cb(f"Florence-2: 加载到 {self.device} ...")
        self.processor = transformers.AutoProcessor.from_pretrained(
            src, trust_remote_code=True, **kw)
        self.model = transformers.AutoModelForCausalLM.from_pretrained(
            src, trust_remote_code=True, torch_dtype=dtype,
            **kw).to(self.device).eval()
        self._patch_config()

    def _patch_config(self):
        """transformers 5.x 严格属性访问下，Florence-2 远程 config 类缺失
        GenerationConfig 常规属性，generate 时直接 AttributeError。类级补默认值。"""
        import transformers
        attrs = [
            "forced_bos_token_id", "forced_eos_token_id", "task_specific_params",
            "eos_token_id", "bos_token_id", "pad_token_id", "decoder_start_token_id",
            "max_length", "min_length", "early_stopping", "do_sample", "num_beams",
            "top_k", "top_p", "repetition_penalty", "length_penalty",
            "no_repeat_ngram_size", "encoder_no_repeat_ngram_size",
            "bad_words_ids", "num_return_sequences", "output_scores",
            "return_dict_in_generate", "output_attentions", "output_hidden_states",
            "diversity_penalty", "remove_invalid_values",
            "exponential_decay_length_penalty", "suppress_tokens",
            "begin_suppress_tokens", "renormalize_logits", "typical_p",
            "temperature", "max_new_tokens", "min_new_tokens", "top_p_min",
            "_from_model_config", "_commit_hash", "transformers_version",
        ]
        classes = {transformers.PretrainedConfig}
        for cfg in [getattr(self.model, "config", None),
                    getattr(getattr(self.model, "config", None), "text_config", None),
                    getattr(getattr(self.model, "config", None), "language_config", None),
                    getattr(getattr(self.model, "language_model", None), "config", None),
                    getattr(self.model, "generation_config", None)]:
            if cfg is not None:
                classes.add(type(cfg))
        for c in classes:
            for a in attrs:
                if not hasattr(c, a):
                    try:
                        setattr(c, a, None)
                    except Exception:
                        pass

    def _unload_impl(self):
        self.model = None
        self.processor = None
        try:
            self.torch.cuda.empty_cache()
        except Exception:
            pass

    def _generate(self, task: str, text, pil_image):
        prompt = task if text is None else task + text
        inputs = self.processor(text=prompt, images=pil_image, return_tensors="pt")
        if self.device == "cuda":
            inputs = inputs.to(self.device)
            inputs["pixel_values"] = inputs["pixel_values"].to(self.torch.float16)
        with self.torch.no_grad():
            gen = self.model.generate(
                input_ids=inputs["input_ids"], pixel_values=inputs["pixel_values"],
                max_new_tokens=1024, num_beams=3, do_sample=False)
        decoded = self.processor.batch_decode(gen, skip_special_tokens=False)[0]
        return self.processor.post_process_generation(
            decoded, task=task, image_size=pil_image.size)

    def _tag_impl(self, path: str, params: dict) -> EngineResult:
        from PIL import Image
        result = EngineResult()
        img = Image.open(path).convert("RGB")
        if params.get("caption", True):
            r = self._generate("<MORE_DETAILED_CAPTION>", None, img)
            cap = str(r.get("<MORE_DETAILED_CAPTION>", "")).strip()
            if cap:
                result.tags.append(cap)
        if params.get("objects", True):
            r = self._generate("<OD>", None, img)
            od = r.get("<OD>", {})
            for bbox, label in zip(od.get("bboxes", []), od.get("labels", [])):
                label = str(label).strip()
                if label and label not in result.tags:
                    result.tags.append(label)
                result.boxes.append(Box(
                    label=label, conf=0.0,
                    x1=float(bbox[0]), y1=float(bbox[1]),
                    x2=float(bbox[2]), y2=float(bbox[3])))

        # 短语定位：每个短语单独跑一次 grounding，命中则出框。
        # Florence 对裸名词定位不稳（易出全图框），自动补上下文后缀提高命中率
        phrases_raw = str(params.get("phrases") or "").strip()
        if phrases_raw:
            for ph in re.split(r"[,，、;\n]+", phrases_raw):
                ph = ph.strip()
                if not ph:
                    continue
                q = ph if " in the image" in ph.lower() else ph + " in the image"
                r = self._generate("<CAPTION_TO_PHRASE_GROUNDING>", q, img)
                pg = r.get("<CAPTION_TO_PHRASE_GROUNDING>", {})
                for bbox, label in zip(pg.get("bboxes", []), pg.get("labels", [])):
                    label = str(label).strip()
                    for suf in (" in the image", " in the picture"):
                        if label.lower().endswith(suf):
                            label = label[:-len(suf)].strip()
                    label = label or ph
                    if label not in result.tags:
                        result.tags.append(label)
                    result.boxes.append(Box(
                        label=label, conf=0.0,
                        x1=float(bbox[0]), y1=float(bbox[1]),
                        x2=float(bbox[2]), y2=float(bbox[3])))
        return result
