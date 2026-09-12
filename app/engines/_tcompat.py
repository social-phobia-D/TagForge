"""transformers 5.x 兼容补丁。

5.x 的 PreTrainedConfig 不再预置生成参数属性，而 Florence-2 / LocateAnything
等 trust_remote_code 的老式模型代码在 config 构造时就会读取它们。
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
    _patched = True
