import os
import re

from app.engines.base import (EngineBase, EngineResult, hf_snapshot_path,
                               hf_snapshot_ready)
from app.engines.base import Box

MODEL_ID = "microsoft/Florence-2-base"


class Florence2Engine(EngineBase):
    key = "florence2"
    title = "Florence-2 轻量全能 (微软)"
    description = "0.23B 小模型，三合一：自然语言描述 + 自动物体检测 + 短语定位（填短语即检测该目标）。MIT 协议，显存占用极低。"
    vram_note = "约 0.9-1.5GB 显存；无显卡可用 CPU（较慢，约 10-30 秒/张）"
    pip_deps = ["torch", "transformers==4.57.1", "accelerate", "einops", "safetensors"]
    import_deps = ["torch", "transformers", "accelerate", "einops", "safetensors"]
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
            return (os.path.isdir(cw) and
                    os.path.isfile(os.path.join(cw, "config.json")) and
                    any(os.path.isfile(os.path.join(cw, name)) and
                        name.lower().endswith((".safetensors", ".bin", ".pt"))
                        for name in os.listdir(cw)))
        d = self._cache_dir()
        return hf_snapshot_ready(MODEL_ID, d)

    def _download_impl(self, models_dir: str, log_cb=None, progress_cb=None):
        if log_cb:
            log_cb(f"Florence-2: 下载 {MODEL_ID}（{self.weights_size}）...")
        from app.engines.base import hf_snapshot_download
        hf_snapshot_download(MODEL_ID, self._cache_dir(), log_cb, progress_cb)

    def _load_impl(self, models_dir: str, params: dict, log_cb=None,
                   progress_cb=None):
        def prog(v):
            if progress_cb:
                progress_cb(v)

        prog(3)
        from app.engines._tcompat import (patch_legacy_cache_indexing,
                                          patch_legacy_generation_attrs)
        patch_legacy_generation_attrs()
        patch_legacy_cache_indexing()
        import importlib
        if log_cb:
            log_cb("Florence-2: 导入 torch/transformers（首次约 10-20 秒）...")
        torch = importlib.import_module("torch")
        transformers = importlib.import_module("transformers")
        prog(40)
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        cw = (params or {}).get("custom_weights") or self.custom_weights()
        cached = "" if cw else hf_snapshot_path(MODEL_ID, self._cache_dir())
        src = cw or cached or MODEL_ID
        kw = {} if (cw or cached) else {"cache_dir": self._cache_dir()}
        if log_cb:
            log_cb(f"Florence-2: 加载到 {self.device} ...")
        self.processor = transformers.AutoProcessor.from_pretrained(
            src, trust_remote_code=True, **kw)
        prog(55)
        model_kw = dict(torch_dtype=dtype, **kw)
        # Florence-2 的远程实现声明了旧式 SDPA 属性；在 transformers 4/5
        # 的复合模型初始化中都可能触发不兼容检查，eager 是其兼容路径。
        model_kw["attn_implementation"] = "eager"
        self.model = transformers.AutoModelForCausalLM.from_pretrained(
            src, trust_remote_code=True, **model_kw).to(self.device).eval()
        self._patch_legacy_runtime(transformers)
        self._patch_config()
        prog(95)

    def _patch_legacy_runtime(self, transformers):
        """修复 Florence 旧 checkpoint 在新 Transformers 下的两处兼容差异。"""
        lm = getattr(self.model, "language_model", None)
        if lm is None:
            return

        # checkpoint 只保存 shared.weight；旧实现依靠 tie_weights() 建立三个
        # 引用，但 transformers 5 的旧远程模型没有成功执行这一步。
        shared = getattr(getattr(lm, "model", None), "shared", None)
        if shared is not None:
            for part in ("encoder", "decoder"):
                emb = getattr(getattr(lm, "model", None), part, None)
                if emb is not None and hasattr(emb, "weight"):
                    emb.weight = shared.weight
            head = getattr(lm, "lm_head", None)
            if head is not None and hasattr(head, "weight"):
                head.weight = shared.weight

        # Transformers 4.57+ passes an EncoderDecoderCache object to the
        # legacy Florence generation method. The remote method still reads
        # past_key_values[0][0], which is (None, None) for an empty cache and
        # crashes before the first token is generated.
        self._patch_legacy_generation(lm)

        # 旧 Florence generation 代码按 tuple 访问 past_key_values；让
        # transformers 5 不预先注入 EncoderDecoderCache，保留旧缓存协议。
        try:
            if int(transformers.__version__.split(".")[0]) >= 5:
                lm._supports_default_dynamic_cache = lambda: False
        except (AttributeError, TypeError, ValueError):
            pass

    def _patch_legacy_generation(self, lm):
        if getattr(lm, "_dabiao_generation_patched", False):
            return
        original = getattr(lm, "prepare_inputs_for_generation", None)
        if original is None:
            return

        import types

        def compatible(self, decoder_input_ids, past_key_values=None,
                       attention_mask=None, decoder_attention_mask=None,
                       head_mask=None, decoder_head_mask=None,
                       cross_attn_head_mask=None, use_cache=None,
                       encoder_outputs=None, **kwargs):
            if (past_key_values is None or
                    not hasattr(past_key_values, "get_seq_length")):
                return original(
                    decoder_input_ids=decoder_input_ids,
                    past_key_values=past_key_values,
                    attention_mask=attention_mask,
                    decoder_attention_mask=decoder_attention_mask,
                    head_mask=head_mask,
                    decoder_head_mask=decoder_head_mask,
                    cross_attn_head_mask=cross_attn_head_mask,
                    use_cache=use_cache,
                    encoder_outputs=encoder_outputs,
                    **kwargs)

            past_length = int(past_key_values.get_seq_length())
            if decoder_input_ids.shape[1] > past_length:
                remove_prefix_length = past_length
            else:
                remove_prefix_length = decoder_input_ids.shape[1] - 1
            decoder_input_ids = decoder_input_ids[:, remove_prefix_length:]
            return {
                "input_ids": None,
                "encoder_outputs": encoder_outputs,
                "past_key_values": past_key_values,
                "decoder_input_ids": decoder_input_ids,
                "attention_mask": attention_mask,
                "decoder_attention_mask": decoder_attention_mask,
                "head_mask": head_mask,
                "decoder_head_mask": decoder_head_mask,
                "cross_attn_head_mask": cross_attn_head_mask,
                "use_cache": use_cache,
            }

        lm.prepare_inputs_for_generation = types.MethodType(compatible, lm)
        lm._dabiao_generation_patched = True

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
        generate_kw = dict(
            input_ids=inputs["input_ids"], pixel_values=inputs["pixel_values"],
            max_new_tokens=1024, num_beams=3, do_sample=False,
            # Florence-2 的远程 decoder 仍按旧 tuple 读取 KV cache；关闭
            # cache 可同时兼容 Transformers 4.57/5.x，代价是略慢。
            use_cache=False,
        )
        with self.torch.no_grad():
            gen = self.model.generate(**generate_kw)
        decoded = self.processor.batch_decode(gen, skip_special_tokens=False)[0]
        return self.processor.post_process_generation(
            decoded, task=task, image_size=pil_image.size)

    def _tag_impl(self, path: str, params: dict, progress_cb=None) -> EngineResult:
        from PIL import Image, ImageOps
        result = EngineResult()
        img = Image.open(path).convert("RGB")
        # 当前 Florence-2 远程实现的视觉投影层只接受正方形 feature map。
        # 用补边而不是拉伸，保持物体几何关系；输出框再映射回原图坐标。
        w, h = img.size
        side = max(w, h)
        pad_left = (side - w) // 2
        pad_top = (side - h) // 2
        model_img = ImageOps.expand(
            img,
            border=(pad_left, pad_top, side - w - pad_left,
                    side - h - pad_top),
            fill=(0, 0, 0),
        )

        def to_original_box(bbox):
            x1 = max(0.0, min(float(w), float(bbox[0]) - pad_left))
            y1 = max(0.0, min(float(h), float(bbox[1]) - pad_top))
            x2 = max(0.0, min(float(w), float(bbox[2]) - pad_left))
            y2 = max(0.0, min(float(h), float(bbox[3]) - pad_top))
            return x1, y1, x2, y2

        # 一次推理要跑 caption/OD/每个短语好几轮，CPU 下每轮十几秒。
        # 按轮次分步上报，进度条才不会整张图都停在 0%。
        phrases_raw = str(params.get("phrases") or "").strip()
        phrases = [p.strip() for p in re.split(r"[,，、;\n]+", phrases_raw) if p.strip()]
        do_cap = bool(params.get("caption", True))
        do_obj = bool(params.get("objects", True))
        steps = (1 if do_cap else 0) + (1 if do_obj else 0) + len(phrases)
        done = [0]

        def step():
            done[0] += 1
            if progress_cb and steps:
                progress_cb(min(99, int(done[0] * 100 / steps)))

        if do_cap:
            r = self._generate("<MORE_DETAILED_CAPTION>", None, model_img)
            cap = str(r.get("<MORE_DETAILED_CAPTION>", "")).strip()
            if cap:
                result.tags.append(cap)
            step()
        if do_obj:
            r = self._generate("<OD>", None, model_img)
            od = r.get("<OD>", {})
            for bbox, label in zip(od.get("bboxes", []), od.get("labels", [])):
                label = str(label).strip()
                if label and label not in result.tags:
                    result.tags.append(label)
                x1, y1, x2, y2 = to_original_box(bbox)
                result.boxes.append(Box(
                    label=label, conf=0.0,
                    x1=x1, y1=y1, x2=x2, y2=y2))
            step()

        # 短语定位：每个短语单独跑一次 grounding，命中则出框。
        # Florence 对裸名词定位不稳（易出全图框），自动补上下文后缀提高命中率
        if phrases:
            for ph in phrases:
                if progress_cb and steps:
                    progress_cb(min(99, int(done[0] * 100 / steps)))
                if not ph:
                    continue
                q = ph if " in the image" in ph.lower() else ph + " in the image"
                r = self._generate("<CAPTION_TO_PHRASE_GROUNDING>", q, model_img)
                pg = r.get("<CAPTION_TO_PHRASE_GROUNDING>", {})
                for bbox, label in zip(pg.get("bboxes", []), pg.get("labels", [])):
                    label = str(label).strip()
                    for suf in (" in the image", " in the picture"):
                        if label.lower().endswith(suf):
                            label = label[:-len(suf)].strip()
                    label = label or ph
                    if label not in result.tags:
                        result.tags.append(label)
                    x1, y1, x2, y2 = to_original_box(bbox)
                    result.boxes.append(Box(
                        label=label, conf=0.0,
                        x1=x1, y1=y1, x2=x2, y2=y2))
                step()
        return result
