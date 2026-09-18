"""transformers 5.x 兼容补丁。

5.x 的 PreTrainedConfig 不再预置生成参数属性，而 Florence-2 / LocateAnything
等 trust_remote_code 的老式模型代码在 config/tokenizer 构造时就会读取它们。
加载前给基类补上 None 默认值即可。
"""

_LEGACY_ATTRS = [
    "forced_bos_token_id", "forced_eos_token_id", "task_specific_params",
    "max_length", "min_length", "do_sample", "early_stopping", "num_beams",
    "temperature", "top_k", "top_p", "typical_p", "diversity_penalty",
    "repetition_penalty", "length_penalty", "no_repeat_ngram_size",
    "encoder_no_repeat_ngram_size", "bad_words_ids", "num_return_sequences",
    "output_scores", "return_dict_in_generate", "output_attentions",
    "output_hidden_states", "output_logits", "remove_invalid_values",
    "exponential_decay_length_penalty", "suppress_tokens",
    "begin_suppress_tokens", "renormalize_logits", "max_new_tokens",
    "min_new_tokens", "eos_token_id", "bos_token_id", "pad_token_id",
    "decoder_start_token_id", "_from_model_config",
]

_patched = False


def patch_legacy_generation_attrs():
    global _patched
    if _patched:
        return
    import transformers
    base = getattr(transformers, "PreTrainedConfig",
                   getattr(transformers, "PretrainedConfig", None))
    if base is None:
        return
    for a in _LEGACY_ATTRS:
        if not hasattr(base, a):
            try:
                setattr(base, a, None)
            except Exception:
                pass
    # transformers 5 把 additional_special_tokens 重命名为
    # extra_special_tokens；Florence-2 的旧 processor 仍直接读取前者。
    tokenizer_base = getattr(transformers, "PreTrainedTokenizerBase", None)
    if (is_transformers_v5() and tokenizer_base is not None and
            not hasattr(tokenizer_base, "additional_special_tokens")):
        try:
            tokenizer_base.additional_special_tokens = property(
                lambda self: getattr(self, "extra_special_tokens", []),
                lambda self, value: setattr(self, "extra_special_tokens", value))
        except Exception:
            pass
    _patched = True


_cache_patched = False


def patch_legacy_cache_indexing():
    """让 transformers 5 的 EncoderDecoderCache 兼容旧 decoder 的下标访问。

    Florence-2 的 decoder 使用 ``past_key_values[0][0]`` 和
    ``past_key_values[layer]``，而 Transformers 5 的 cache 容器只实现迭代，
    不实现旧 tuple 的 ``__getitem__``。保留新 cache 本身可变的优势，只补一个
    按层读取适配，不关闭 KV cache。
    """
    global _cache_patched
    if _cache_patched:
        return
    if not is_transformers_v5():
        _cache_patched = True
        return
    try:
        from transformers.cache_utils import EncoderDecoderCache, DynamicCache
        if not hasattr(DynamicCache, "to_legacy_cache"):
            def to_legacy_cache(self):
                return tuple((layer[0], layer[1]) for layer in self)
            DynamicCache.to_legacy_cache = to_legacy_cache
        if not hasattr(DynamicCache, "from_legacy_cache"):
            @classmethod
            def from_legacy_cache(cls, past_key_values):
                cache = cls()
                if past_key_values is None:
                    return cache
                for layer_idx, layer in enumerate(past_key_values):
                    if len(layer) >= 2 and layer[0] is not None and layer[1] is not None:
                        cache.update(layer[0], layer[1], layer_idx)
                return cache
            DynamicCache.from_legacy_cache = from_legacy_cache
        if "__getitem__" not in EncoderDecoderCache.__dict__:
            def __getitem__(self, index):
                if isinstance(index, slice):
                    return tuple(self)[index]
                if index < 0:
                    index += len(self)
                if index < 0:
                    raise IndexError(index)
                for i, layer in enumerate(self):
                    if i == index:
                        return layer
                raise IndexError(index)
            EncoderDecoderCache.__getitem__ = __getitem__
        _cache_patched = True
    except Exception:
        pass


_tied_patched = False


def patch_legacy_tied_weights():
    """让 transformers 5 接受旧远程模型的 ``list`` tied-weight 声明。"""
    global _tied_patched
    if _tied_patched:
        return
    if not is_transformers_v5():
        _tied_patched = True
        return
    try:
        import transformers
        base = transformers.PreTrainedModel
        original = base.get_expanded_tied_weights_keys
        original_init = base.__init__
    except Exception:
        return

    def compatible(self, all_submodels=False):
        keys = getattr(self, "_tied_weights_keys", None)
        if not isinstance(keys, list):
            return original(self, all_submodels=all_submodels)
        mapping = {}
        for key in keys:
            # transformers 4 的 Qwen2/LocateAnything 声明只有输出头；
            # 输入嵌入的标准路径是 model.embed_tokens.weight。
            mapping[key] = ("model.embed_tokens.weight"
                            if key == "lm_head.weight" else key)
        had_instance_value = "_tied_weights_keys" in self.__dict__
        old_value = self.__dict__.get("_tied_weights_keys")
        self._tied_weights_keys = mapping
        try:
            return original(self, all_submodels=all_submodels)
        finally:
            if had_instance_value:
                self._tied_weights_keys = old_value
            else:
                self.__dict__.pop("_tied_weights_keys", None)

    def init_with_legacy_keys(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        # LocateAnything 的顶层远程类跳过 post_init，但 transformers 5
        # 在最终整理权重时仍会访问该字段。子模型后续会覆盖为空映射。
        if not hasattr(self, "all_tied_weights_keys"):
            self.all_tied_weights_keys = {}

    base.get_expanded_tied_weights_keys = compatible
    base.__init__ = init_with_legacy_keys
    _tied_patched = True

_attn_patched = False


def is_transformers_v5() -> bool:
    """下面的补丁只针对 transformers 5.x（4.x 的签名/属性本来就有）"""
    try:
        import transformers
        return int(transformers.__version__.split(".")[0]) >= 5
    except Exception:
        return False


def patch_attn_impl_kwargs():
    """transformers 5.x 调用 _check_and_adjust_attn_implementation() 时**无条件**
    带 allow_all_kernels=，而 trust_remote_code 的老模型（LocateAnything 及其内部
    Qwen2）override 还是 4.x 的 (attn_implementation, is_init_check) 两参签名
    → TypeError，模型连构造都过不去（PreTrainedModel.__init__ / set_attn_implementation）。

    这里在「子类自己 override 了该方法」且「签名不吃 allow_all_kernels」时，临时把
    它换成容忍 kwargs 的转发函数（丢弃该参数，只影响 5.x 的 hub-kernel 新特性），
    覆盖 __init__ 与 set_attn_implementation 两条调用路径。"""
    global _attn_patched
    if _attn_patched:
        return
    if not is_transformers_v5():
        _attn_patched = True
        return
    import inspect
    try:
        import transformers
        base = transformers.PreTrainedModel
        orig_init = base.__init__
        orig_set = base.set_attn_implementation
    except Exception:
        return

    def _old_override(cls):
        """沿 MRO 找「自己定义了」该方法的类；若其签名吃不下 allow_all_kernels，
        返回 (定义它的类, 原始函数)，否则 None（含只有基类实现的正常情况）"""
        for c in cls.__mro__:
            fn = c.__dict__.get("_check_and_adjust_attn_implementation")
            if fn is None:
                continue
            if c is base:
                return None  # 基类实现：签名本来就带 allow_all_kernels
            try:
                if "allow_all_kernels" in inspect.signature(fn).parameters:
                    return None
            except (TypeError, ValueError):
                return None
            return c, fn
        return None

    def _tolerant(fn):
        def tolerant(_self, attn_implementation, is_init_check=False,
                     allow_all_kernels=False, **_kw):
            return fn(_self, attn_implementation, is_init_check)
        return tolerant

    def _guard(obj):
        """进入时临时替换，返回 (定义类, 原函数) 或 None"""
        hit = _old_override(type(obj))
        if hit is None:
            return None
        c, fn = hit
        c._check_and_adjust_attn_implementation = _tolerant(fn)
        return c, fn

    def _init(self, *args, **kwargs):
        hit = _guard(self)
        try:
            return orig_init(self, *args, **kwargs)
        finally:
            if hit is not None:
                c, fn = hit
                c._check_and_adjust_attn_implementation = fn

    def set_attn_implementation(self, attn_implementation,
                                allow_all_kernels=False):
        hit = _guard(self)
        try:
            return orig_set(self, attn_implementation,
                            allow_all_kernels=allow_all_kernels)
        finally:
            if hit is not None:
                c, fn = hit
                c._check_and_adjust_attn_implementation = fn

    base.__init__ = _init
    base.set_attn_implementation = set_attn_implementation
    _attn_patched = True


_rope_patched = False


def patch_legacy_rope_theta():
    """transformers 5.x 把 rope_theta/rope_scaling 标准化成 config.rope_parameters
    （一个 dict），config 上不再有 rope_theta 属性；而 trust_remote_code 的老建模
    代码仍读 config.rope_theta（LocateAnything 内部 Qwen2）→ AttributeError。

    注意：LocateAnything 的 configuration_locateanything.py 是 `from transformers.
    models.qwen2.configuration_qwen2 import Qwen2Config`（用**库里的** config，
    远程目录里那份 configuration_qwen2.py 并不生效），所以要给库里的 config 类
    补一个映射到 rope_parameters 的 rope_theta 属性。"""
    global _rope_patched
    if _rope_patched:
        return
    if not is_transformers_v5():
        _rope_patched = True
        return

    def _get(self):
        rp = self.__dict__.get("rope_parameters")
        if isinstance(rp, dict) and "rope_theta" in rp:
            return rp["rope_theta"]
        return self.__dict__.get("_legacy_rope_theta", 10000.0)

    def _set(self, value):
        rp = self.__dict__.get("rope_parameters")
        if isinstance(rp, dict):
            rp["rope_theta"] = value
        else:
            self.__dict__["_legacy_rope_theta"] = value

    for mod_name, cls_name in (
            ("transformers.models.qwen2.configuration_qwen2", "Qwen2Config"),
            ("transformers.models.qwen3.configuration_qwen3", "Qwen3Config"),
            ("transformers.configuration_utils", "PretrainedConfig"),
    ):
        try:
            mod = __import__(mod_name, fromlist=[cls_name])
            cls = getattr(mod, cls_name)
        except Exception:
            continue
        if "rope_theta" in cls.__dict__:
            continue
        try:
            cls.rope_theta = property(_get, _set)
        except Exception:
            pass
    _rope_patched = True
