import os
import re

from app.engines.base import (EngineBase, EngineResult, hf_snapshot_path,
                               hf_snapshot_ready)
from app.engines.base import Box

MODEL_ID = "nvidia/LocateAnything-3B"


class LocateAnythingEngine(EngineBase):
    key = "locateanything"
    title = "LocateAnything-3B 全自动物体标签 (NVIDIA)"
    description = "英伟达 3B 开放词表检测模型（并行出框解码）：可填类名做检测，也可免提示词自动找物体，输出物体标签+检测框。实测 int4 峰值显存约 5.5GB、单张约 3 秒。"
    vram_note = "int4 量化峰值约 5.5GB 显存；12GB+ 显卡可跑原精度更快"
    pip_deps = ["torch", "transformers==4.57.1", "accelerate", "bitsandbytes", "einops", "safetensors", "peft", "eva-decord", "lmdb"]
    import_deps = ["torch", "transformers", "accelerate", "bitsandbytes", "einops", "safetensors", "peft", "decord", "lmdb"]
    weights_size = "约 7 GB"
    need_torch = True
    has_boxes = True
    weight_urls = [f"https://hf-mirror.com/{MODEL_ID}", f"https://huggingface.co/{MODEL_ID}"]
    custom_hint = "选 LocateAnything-3B 模型文件夹（含 config.json）"

    def _cache_dir(self) -> str:
        return os.path.join(self.models_dir(), "locateanything")

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
            log_cb(f"LocateAnything-3B: 下载 {MODEL_ID}（{self.weights_size}，需要较长时间）...")
        from app.engines.base import hf_snapshot_download
        hf_snapshot_download(MODEL_ID, self._cache_dir(), log_cb, progress_cb)

    def _load_impl(self, models_dir: str, params: dict, log_cb=None,
                   progress_cb=None):
        def prog(v):
            if progress_cb:
                progress_cb(v)

        prog(3)
        from app.engines._tcompat import (patch_legacy_generation_attrs,
                                          patch_attn_impl_kwargs,
                                          patch_legacy_cache_indexing,
                                          patch_legacy_rope_theta,
                                          patch_legacy_tied_weights)
        patch_legacy_generation_attrs()
        patch_attn_impl_kwargs()
        patch_legacy_tied_weights()
        patch_legacy_cache_indexing()
        patch_legacy_rope_theta()
        import importlib
        if log_cb:
            log_cb("LocateAnything-3B: 导入 torch/transformers（首次约 10-20 秒）...")
        torch = importlib.import_module("torch")
        transformers = importlib.import_module("transformers")
        prog(30)
        self.torch = torch

        cw = (params or {}).get("custom_weights") or self.custom_weights()
        cached = "" if cw else hf_snapshot_path(MODEL_ID, self._cache_dir())
        src = cw or cached or MODEL_ID
        # 自定义目录和已存在的本地快照必须完全离线加载，避免
        # Transformers 在网络受限时仍向 Hugging Face 发 HEAD 请求。
        kw = ({"local_files_only": True} if (cw or cached)
              else {"cache_dir": self._cache_dir()})

        from app.core.gpu_check import gpu_info, pick_precision
        _name, vram = gpu_info()
        cuda = torch.cuda.is_available()
        preferred = pick_precision(vram) if cuda else "cpu"
        order = [preferred] + [p for p in ("fp16", "int4", "cpu") if p != preferred]

        last_err = None
        self.device = "cpu"
        for prec in order:
            if prec != "cpu" and not cuda:
                continue
            try:
                if log_cb:
                    log_cb(f"LocateAnything-3B: 尝试以 {prec} 加载 ...")
                if prec == "int4":
                    from transformers import BitsAndBytesConfig
                    self.model = transformers.AutoModel.from_pretrained(
                        src, trust_remote_code=True, **kw,
                        attn_implementation="sdpa",
                        quantization_config=BitsAndBytesConfig(
                            load_in_4bit=True,
                            bnb_4bit_compute_dtype=torch.bfloat16,
                            bnb_4bit_quant_type="nf4"),
                        device_map={"": 0})
                    self.device = "cuda"
                    self.dtype = torch.bfloat16
                elif prec == "fp16":
                    self.model = transformers.AutoModel.from_pretrained(
                        src, trust_remote_code=True, **kw,
                        attn_implementation="sdpa",
                        torch_dtype=torch.bfloat16).to("cuda").eval()
                    self.device = "cuda"
                    self.dtype = torch.bfloat16
                else:
                    self.model = transformers.AutoModel.from_pretrained(
                        src, trust_remote_code=True, **kw,
                        attn_implementation="eager",
                        torch_dtype=torch.float32).to("cpu").eval()
                    self.device = "cpu"
                    self.dtype = torch.float32
                # 远程代码内部 Qwen2 fork 只实现了 sdpa/flash，
                # attn_implementation 参数不会传到内部子模块，需手动补齐
                for m in self.model.modules():
                    cfg = getattr(m, "config", None)
                    if cfg is not None and hasattr(cfg, "_attn_implementation"):
                        cfg._attn_implementation = "sdpa"
                    if hasattr(m, "_attn_implementation"):
                        m._attn_implementation = "sdpa"
                self._patch_legacy_runtime()
                self.precision = prec
                prog(80)
                break
            except Exception as e:
                last_err = e
                if log_cb:
                    log_cb(f"LocateAnything-3B: {prec} 加载失败（{e}），尝试降级 ...")
                self.model = None
        else:
            raise RuntimeError(f"LocateAnything-3B 所有加载方式均失败: {last_err}")

        prog(85)
        # Transformers 5 warns that this Qwen tokenizer has the legacy Mistral
        # regex; without the fix, chat-template tokenization can emit immediate
        # EOS or unreadable text. Reuse the corrected tokenizer in the processor.
        tokenizer_kw = dict(kw)
        tokenizer_kw["fix_mistral_regex"] = True
        try:
            self.tokenizer = transformers.AutoTokenizer.from_pretrained(
                src, trust_remote_code=True, **tokenizer_kw)
        except TypeError:
            self.tokenizer = transformers.AutoTokenizer.from_pretrained(
                src, trust_remote_code=True, **kw)
        self.processor = transformers.AutoProcessor.from_pretrained(
            src, trust_remote_code=True, **kw)
        if hasattr(self.processor, "tokenizer"):
            self.processor.tokenizer = self.tokenizer
        if log_cb:
            log_cb(f"LocateAnything-3B: 加载完成（{self.precision}）")

    def _patch_legacy_runtime(self):
        # LocateAnything 的 checkpoint 只保存 Qwen2 输入嵌入，旧远程代码
        # 依靠 tied weights 让输出头共享同一参数；显式绑定避免随机 head。
        lm = getattr(self.model, "language_model", None)
        if lm is None:
            return
        try:
            inp = lm.get_input_embeddings()
            out = lm.get_output_embeddings()
        except AttributeError:
            return
        if (inp is not None and out is not None and
                hasattr(inp, "weight") and hasattr(out, "weight") and
                inp.weight.shape == out.weight.shape):
            out.weight = inp.weight

    def _unload_impl(self):
        self.model = None
        self.processor = None
        self.tokenizer = None
        try:
            self.torch.cuda.empty_cache()
        except Exception:
            pass

    def _predict(self, img, prompt: str, generation_mode: str = "hybrid",
                 max_new_tokens: int = 512) -> str:
        messages = [
            {"role": "user", "content": [
                {"type": "image", "image": img},
                {"type": "text", "text": prompt},
            ]}
        ]
        text = self.processor.py_apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True)
        images, videos = self.processor.process_vision_info(messages)
        inputs = self.processor(
            text=[text], images=images, videos=videos,
            return_tensors="pt").to(self.device)
        pixel_values = inputs["pixel_values"].to(self.dtype)
        try:
            response = self.model.generate(
                pixel_values=pixel_values,
                input_ids=inputs["input_ids"],
                attention_mask=inputs["attention_mask"],
                image_grid_hws=inputs.get("image_grid_hws", None),
                tokenizer=self.tokenizer,
                max_new_tokens=max_new_tokens,
                use_cache=True,
                generation_mode=generation_mode,
                do_sample=True,
                temperature=0.7,
                top_p=0.9,
                repetition_penalty=1.1,
            )
        except (TypeError, KeyError):
            # 远程代码签名可能不同，退回最小参数
            response = self.model.generate(
                pixel_values=pixel_values,
                input_ids=inputs["input_ids"],
                attention_mask=inputs["attention_mask"],
                tokenizer=self.tokenizer,
                max_new_tokens=max_new_tokens,
                use_cache=True,
            )
        answer = response[0] if isinstance(response, tuple) else response
        if isinstance(answer, list):
            answer = answer[0]
        if hasattr(answer, "strip"):
            return answer
        return self.tokenizer.batch_decode(
            [answer], skip_special_tokens=True)[0]

    def _tag_impl(self, path: str, params: dict, progress_cb=None) -> EngineResult:
        from PIL import Image
        img = Image.open(path).convert("RGB")
        w, h = img.size

        cats = [c.strip() for c in str(params.get("classes", "")).split(",") if c.strip()]
        custom = str(params.get("prompt") or "").strip()
        if custom:
            prompt = custom
        elif cats:
            prompt = ("Locate all the instances that matches the following "
                      f"description: {'</c>'.join(cats)}.")
        else:
            prompt = "Detect all the objects in box format."
        mode = str(params.get("generation_mode") or "hybrid")
        mnt = int(params.get("max_new_tokens") or 512)

        answer = self._predict(img, prompt, generation_mode=mode,
                               max_new_tokens=mnt)
        result = EngineResult()

        # 官方格式: <ref>标签</ref><box><x1><y1><x2><y2></box>，坐标 0-1000 归一化，
        # 一个 ref 后可跟多个 box；无物体时输出 <none>
        token_re = re.compile(
            r"<ref>(.*?)</ref>|<box><(\d+)><(\d+)><(\d+)><(\d+)></box>")
        current = ""
        for m in token_re.finditer(answer):
            if m.group(1) is not None:
                current = m.group(1).strip()
                continue
            x1, y1, x2, y2 = [max(0, min(1000, int(m.group(i))))
                              for i in range(2, 6)]
            x1, x2 = sorted((x1, x2))
            y1, y2 = sorted((y1, y2))
            label = current or (cats[0] if cats else "object")
            if label and label not in result.tags:
                result.tags.append(label)
            result.boxes.append(Box(
                label=label, conf=1.0,
                x1=x1 / 1000.0 * w, y1=y1 / 1000.0 * h,
                x2=x2 / 1000.0 * w, y2=y2 / 1000.0 * h))

        if not result.boxes:
            if "<none>" in answer:
                return result  # 模型明确说没找到物体
            # 兜底：无结构化输出时把文本当标签
            text = re.sub(r"<[^>]*>", " ", answer).strip()
            if text:
                result.tags.append(text[:200])
        return result
