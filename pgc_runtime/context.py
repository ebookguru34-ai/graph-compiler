"""Per-turn context and results for compiled graphs."""
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .kv import KV
from .templates import DEFAULT_SAMPLERS, get_template
from . import character as _char


@dataclass
class Ctx:
    """Everything a turn needs besides the history. Mirrors the app's PipelineContext/settings."""
    kobold_url: str = "http://localhost:5001"
    template: object = "ChatML"                 # preset name or a template dict
    system_prompt: str = "You are a helpful assistant."
    user_name: str = "User"
    samplers: dict = field(default_factory=lambda: dict(DEFAULT_SAMPLERS))
    streaming: bool = True
    use_token_count_api: bool = False           # True = ask KoboldCPP to count tokens (else chars/4)
    data_dir: str = "pgc_data"                  # memory/profile/embedding cache (JSON files)
    character: Optional[dict] = None            # from pgc_runtime.character.load(...)
    armed: Dict[str, bool] = field(default_factory=dict)   # Manual Trigger node ids armed for this turn
    on_token: Optional[Callable[[str], None]] = None        # streamed tokens of nodes with stream on
    dry_run: bool = False                       # no LLM calls, no writes (preview)
    kv: Optional[KV] = None                     # storage backend (default: KV(data_dir))

    def __post_init__(self):
        if self.kv is None:
            self.kv = KV(self.data_dir)
        self.tpl = get_template(self.template)
        self.samplers = {**DEFAULT_SAMPLERS, **(self.samplers or {})}

    # values the app derives from the active character
    @property
    def effective_system(self):
        return _char.system_prompt(self.character, self.user_name) if self.character else self.system_prompt

    @property
    def char_name(self):
        return self.character["name"] if self.character else ""

    @property
    def greeting(self):
        return _char.subst_char(self.character.get("greeting", ""), self.character["name"], self.user_name) if self.character else ""

    @property
    def mem_ns(self):
        return self.character["id"] if self.character else None


@dataclass
class Env:
    ctx: Ctx
    history: List[dict]
    turn_id: Optional[str]
    fired: List[str] = field(default_factory=list)
    last_prompt: str = ""
    prompts: List[str] = field(default_factory=list)   # every prompt sent to KoboldCPP this turn

    @property
    def dry(self):
        return self.ctx.dry_run


@dataclass
class TurnResult:
    reply: str                      # text that reached Chat Output ('' if none)
    trace: List[dict]               # one entry per node: id, label, skipped, outputs
    turn_id: Optional[str] = None   # tag used for memory/profile writes (use it for rollback)
    reached_output: bool = False
    prompt: str = ""                # last prompt built by a prompt node
    fired_triggers: List[str] = field(default_factory=list)
    history: List[dict] = field(default_factory=list)  # input history + user msg + this reply (tagged with turn_id)
