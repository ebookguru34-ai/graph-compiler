"""Instruct templates and default samplers, copied from the app (src/templates.ts)."""
TEMPLATE_PRESETS = {
    "Alpaca": dict(name="Alpaca", systemPrefix="", systemSuffix="\n\n", userPrefix="### Instruction:\n", userSuffix="\n\n",
                   assistantPrefix="### Response:\n", assistantSuffix="\n\n", stop=["### Instruction:", "### Response:"]),
    "ChatML": dict(name="ChatML", systemPrefix="<|im_start|>system\n", systemSuffix="<|im_end|>\n", userPrefix="<|im_start|>user\n",
                   userSuffix="<|im_end|>\n", assistantPrefix="<|im_start|>assistant\n", assistantSuffix="<|im_end|>\n",
                   stop=["<|im_end|>", "<|im_start|>"]),
    "Llama 3": dict(name="Llama 3", systemPrefix="<|start_header_id|>system<|end_header_id|>\n\n", systemSuffix="<|eot_id|>",
                    userPrefix="<|start_header_id|>user<|end_header_id|>\n\n", userSuffix="<|eot_id|>",
                    assistantPrefix="<|start_header_id|>assistant<|end_header_id|>\n\n", assistantSuffix="<|eot_id|>",
                    stop=["<|eot_id|>", "<|start_header_id|>"]),
    "Mistral": dict(name="Mistral", systemPrefix="[INST] ", systemSuffix="\n\n", userPrefix="[INST] ", userSuffix=" [/INST]",
                    assistantPrefix="", assistantSuffix="</s>", stop=["[INST]", "</s>"]),
}
DEFAULT_SAMPLERS = dict(temperature=0.7, top_p=0.92, top_k=40, min_p=0.05, rep_pen=1.1,
                        max_length=300, max_context_length=4096, stop_sequence=[])


def get_template(t):
    """Template by preset name, or a dict with the same keys (a custom template)."""
    if isinstance(t, dict):
        return t
    if t not in TEMPLATE_PRESETS:
        raise ValueError(f"Unknown instruct template {t!r}. Choose one of {list(TEMPLATE_PRESETS)} or pass a dict.")
    return TEMPLATE_PRESETS[t]


def render_prompt(messages, t):
    """Render messages with a template; always ends with the assistant prefix (the app's renderPrompt)."""
    out = ""
    for m in messages:
        if m["role"] == "system":
            out += t["systemPrefix"] + m["content"] + t["systemSuffix"]
        elif m["role"] == "user":
            out += t["userPrefix"] + m["content"] + t["userSuffix"]
        else:
            out += t["assistantPrefix"] + m["content"] + t["assistantSuffix"]
    return out + t["assistantPrefix"]
