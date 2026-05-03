"""LLM provider registry."""

# Adding a new provider:
#   1. Add a ProviderSpec to PROVIDERS below.
#   2. Add a field to ProvidersConfig in config/schema.py.
# Order in PROVIDERS controls match priority and fallback (gateways first).

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic.alias_generators import to_snake

AuthMethodKind = Literal["browser-login", "paste-token", "api-key"]


@dataclass(frozen=True)
class AuthMethod:
    """One authentication method for a provider.

    Used by the wizard to present method options to the user.
    """

    id: str  # machine identifier, e.g. "browser-login"
    display: str  # user-facing label
    hint: str = ""  # optional helper text
    kind: AuthMethodKind = "browser-login"


@dataclass(frozen=True)
class ProviderSpec:
    """One LLM provider's metadata. See PROVIDERS below for real examples.

    Placeholders in env_extras values:
      {api_key}  — the user's API key
      {api_base} — api_base from config, or this spec's default_api_base
    """

    # identity
    name: str  # config field name, e.g. "dashscope"
    keywords: tuple[str, ...]  # model-name keywords for matching (lowercase)
    env_key: str  # env var for API key, e.g. "DASHSCOPE_API_KEY"
    display_name: str = ""  # shown in `pythinker status`

    # which provider implementation to use
    # "openai_compat" | "anthropic" | "azure_openai" | "openai_codex" | "github_copilot"
    backend: str = "openai_compat"

    # extra env vars, e.g. (("ZHIPUAI_API_KEY", "{api_key}"),)
    env_extras: tuple[tuple[str, str], ...] = ()

    # gateway / local detection
    is_gateway: bool = False  # routes any model (OpenRouter, AiHubMix)
    is_local: bool = False  # local deployment (vLLM, Ollama)
    detect_by_key_prefix: str = ""  # match api_key prefix, e.g. "sk-or-"
    detect_by_base_keyword: str = ""  # match substring in api_base URL
    default_api_base: str = ""  # OpenAI-compatible base URL for this provider

    # gateway behavior
    strip_model_prefix: bool = False  # strip "provider/" before sending to gateway
    supports_max_completion_tokens: bool = False

    # per-model param overrides, e.g. (("kimi-k2.5", {"temperature": 1.0}),)
    model_overrides: tuple[tuple[str, dict[str, Any]], ...] = ()

    # OAuth-based providers (e.g., OpenAI Codex) don't use API keys
    is_oauth: bool = False

    # OAuth token file location (only meaningful when is_oauth=True).
    # Empty values mean "use oauth_cli_kit's default storage" (oauth.json under
    # platformdirs user_data_dir("oauth-cli-kit")). Set both for providers that
    # persist to their own file, e.g. GitHub Copilot.
    token_filename: str = ""
    token_app_name: str = ""

    # Direct providers skip API-key validation (user supplies everything)
    is_direct: bool = False

    # Provider supports cache_control on content blocks (e.g. Anthropic prompt caching)
    supports_prompt_caching: bool = False

    # Browser URL where the user obtains/manages their API key. Backfilled
    # for non-OAuth, non-local, non-direct, non-gateway providers — see
    # signup_url_required() below. Onboarding's auto-open hook reads this
    # to offer to open the page during provider configuration.
    signup_url: str = ""

    # Optional canonical setup-doc URL. Surfaced as a "Learn more:" line
    # next to signup_url. Most providers leave this blank.
    docs_url: str = ""

    # Authentication methods available for this provider. Used by the
    # wizard's auth-method-picker step. Empty list means fall through to
    # generic API-key prompt (for non-OAuth providers).
    auth_methods: list[AuthMethod] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.display_name or self.name.title()


# ---------------------------------------------------------------------------
# PROVIDERS — the registry. Order = priority. Copy any entry as template.
# ---------------------------------------------------------------------------

PROVIDERS: tuple[ProviderSpec, ...] = (
    # === Custom (direct OpenAI-compatible endpoint) ========================
    ProviderSpec(
        name="custom",
        keywords=(),
        env_key="",
        display_name="Custom",
        backend="openai_compat",
        is_direct=True,
    ),
    # === Azure OpenAI (direct API calls with API version 2024-10-21) =====
    ProviderSpec(
        name="azure_openai",
        keywords=("azure", "azure-openai"),
        env_key="",
        display_name="Azure OpenAI",
        backend="azure_openai",
        is_direct=True,
    ),
    # === Gateways (detected by api_key / api_base, not model name) =========
    # Gateways can route any model, so they win in fallback.
    # OpenRouter: global gateway, keys start with "sk-or-"
    ProviderSpec(
        name="openrouter",
        keywords=("openrouter",),
        env_key="OPENROUTER_API_KEY",
        display_name="OpenRouter",
        backend="openai_compat",
        is_gateway=True,
        detect_by_key_prefix="sk-or-",
        detect_by_base_keyword="openrouter",
        default_api_base="https://openrouter.ai/api/v1",
        supports_prompt_caching=True,
    ),
    # Hugging Face Inference Providers: OpenAI-compatible router for chat models.
    ProviderSpec(
        name="huggingface",
        keywords=("huggingface", "hugging-face"),
        env_key="HF_TOKEN",
        display_name="Hugging Face",
        backend="openai_compat",
        is_gateway=True,
        detect_by_key_prefix="hf_",
        detect_by_base_keyword="huggingface",
        default_api_base="https://router.huggingface.co/v1",
        signup_url="https://huggingface.co/settings/tokens",
    ),
    # AiHubMix: global gateway, OpenAI-compatible interface.
    # strip_model_prefix=True: doesn't understand "anthropic/claude-3",
    # strips to bare "claude-3".
    ProviderSpec(
        name="aihubmix",
        keywords=("aihubmix",),
        env_key="OPENAI_API_KEY",
        display_name="AiHubMix",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="aihubmix",
        default_api_base="https://aihubmix.com/v1",
        strip_model_prefix=True,
    ),
    # SiliconFlow: OpenAI-compatible gateway, model names keep org prefix
    ProviderSpec(
        name="siliconflow",
        keywords=("siliconflow",),
        env_key="OPENAI_API_KEY",
        display_name="SiliconFlow",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="siliconflow",
        default_api_base="https://api.siliconflow.cn/v1",
    ),
    # VolcEngine: OpenAI-compatible gateway, pay-per-use models
    ProviderSpec(
        name="volcengine",
        keywords=("volcengine", "volces", "ark"),
        env_key="OPENAI_API_KEY",
        display_name="VolcEngine",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="volces",
        default_api_base="https://ark.cn-beijing.volces.com/api/v3",
    ),
    # VolcEngine Coding Plan: same key as volcengine
    ProviderSpec(
        name="volcengine_coding_plan",
        keywords=("volcengine-plan",),
        env_key="OPENAI_API_KEY",
        display_name="VolcEngine Coding Plan",
        backend="openai_compat",
        is_gateway=True,
        default_api_base="https://ark.cn-beijing.volces.com/api/coding/v3",
        strip_model_prefix=True,
    ),
    # BytePlus: VolcEngine international, pay-per-use models
    ProviderSpec(
        name="byteplus",
        keywords=("byteplus",),
        env_key="OPENAI_API_KEY",
        display_name="BytePlus",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="bytepluses",
        default_api_base="https://ark.ap-southeast.bytepluses.com/api/v3",
        strip_model_prefix=True,
    ),
    # BytePlus Coding Plan: same key as byteplus
    ProviderSpec(
        name="byteplus_coding_plan",
        keywords=("byteplus-plan",),
        env_key="OPENAI_API_KEY",
        display_name="BytePlus Coding Plan",
        backend="openai_compat",
        is_gateway=True,
        default_api_base="https://ark.ap-southeast.bytepluses.com/api/coding/v3",
        strip_model_prefix=True,
    ),
    # === Standard providers (matched by model-name keywords) ===============
    # Anthropic: native Anthropic SDK
    ProviderSpec(
        name="anthropic",
        keywords=("anthropic", "claude"),
        env_key="ANTHROPIC_API_KEY",
        display_name="Anthropic",
        backend="anthropic",
        supports_prompt_caching=True,
        signup_url="https://console.anthropic.com/settings/keys",
    ),
    # OpenAI: SDK default base URL (no override needed)
    ProviderSpec(
        name="openai",
        keywords=("openai", "gpt"),
        env_key="OPENAI_API_KEY",
        display_name="OpenAI",
        backend="openai_compat",
        supports_max_completion_tokens=True,
        signup_url="https://platform.openai.com/api-keys",
    ),
    # OpenAI Codex: OAuth-based, dedicated provider
    ProviderSpec(
        name="openai_codex",
        keywords=("openai-codex",),
        env_key="",
        display_name="OpenAI Codex",
        backend="openai_codex",
        detect_by_base_keyword="codex",
        default_api_base="https://chatgpt.com/backend-api",
        is_oauth=True,
        auth_methods=[
            AuthMethod(
                id="browser-login",
                display="Browser login",
                hint="Sign in with ChatGPT in your browser.",
                kind="browser-login",
            ),
        ],
    ),
    # GitHub Copilot: OAuth-based
    ProviderSpec(
        name="github_copilot",
        keywords=("github_copilot", "copilot"),
        env_key="",
        display_name="Github Copilot",
        backend="github_copilot",
        default_api_base="https://api.githubcopilot.com",
        strip_model_prefix=True,
        is_oauth=True,
        supports_max_completion_tokens=True,
        token_filename="github-copilot.json",
        token_app_name="pythinker",
        auth_methods=[
            AuthMethod(
                id="browser-login",
                display="Browser login",
                hint="Sign in with GitHub in your browser.",
                kind="browser-login",
            ),
        ],
    ),
    # DeepSeek: OpenAI-compatible at api.deepseek.com
    ProviderSpec(
        name="deepseek",
        keywords=("deepseek",),
        env_key="DEEPSEEK_API_KEY",
        display_name="DeepSeek",
        backend="openai_compat",
        default_api_base="https://api.deepseek.com",
        signup_url="https://platform.deepseek.com/api_keys",
    ),
    # Gemini: Google's OpenAI-compatible endpoint
    ProviderSpec(
        name="gemini",
        keywords=("gemini",),
        env_key="GEMINI_API_KEY",
        display_name="Gemini",
        backend="openai_compat",
        default_api_base="https://generativelanguage.googleapis.com/v1beta/openai/",
        signup_url="https://aistudio.google.com/app/apikey",
    ),
    # Zhipu: OpenAI-compatible at open.bigmodel.cn
    ProviderSpec(
        name="zhipu",
        keywords=("zhipu", "glm", "zai"),
        env_key="ZAI_API_KEY",
        display_name="Zhipu AI",
        backend="openai_compat",
        env_extras=(("ZHIPUAI_API_KEY", "{api_key}"),),
        default_api_base="https://open.bigmodel.cn/api/paas/v4",
        signup_url="https://open.bigmodel.cn/usercenter/apikeys",
    ),
    # DashScope: Qwen models, OpenAI-compatible endpoint
    ProviderSpec(
        name="dashscope",
        keywords=("qwen", "dashscope"),
        env_key="DASHSCOPE_API_KEY",
        display_name="DashScope",
        backend="openai_compat",
        default_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        signup_url="https://bailian.console.aliyun.com/?apiKey=1",
    ),
    # Moonshot: Kimi K2.5 / K2.6 enforce temperature >= 1.0.
    ProviderSpec(
        name="moonshot",
        keywords=("moonshot", "kimi"),
        env_key="MOONSHOT_API_KEY",
        display_name="Moonshot",
        backend="openai_compat",
        default_api_base="https://api.moonshot.ai/v1",
        model_overrides=(
            ("kimi-k2.5", {"temperature": 1.0}),
            ("kimi-k2.6", {"temperature": 1.0}),
        ),
        signup_url="https://platform.moonshot.ai/console/api-keys",
    ),
    # MiniMax: OpenAI-compatible API
    ProviderSpec(
        name="minimax",
        keywords=("minimax",),
        env_key="MINIMAX_API_KEY",
        display_name="MiniMax",
        backend="openai_compat",
        default_api_base="https://api.minimax.io/v1",
        signup_url="https://platform.minimax.io/user-center/payment/token-plan",
        docs_url="https://platform.minimax.io/docs/token-plan/other-tools",
        auth_methods=[
            AuthMethod(
                id="api-key-cn",
                display="MiniMax API key (CN)",
                hint="cn endpoint — api.minimax.chat",
                kind="api-key",
            ),
            AuthMethod(
                id="api-key-global",
                display="MiniMax API key (Global)",
                hint="global endpoint — api.minimax.io",
                kind="api-key",
            ),
            AuthMethod(
                id="oauth-cn",
                display="MiniMax OAuth (CN)",
                hint="(coming soon)",
                kind="browser-login",
            ),
            AuthMethod(
                id="oauth-global",
                display="MiniMax OAuth (Global)",
                hint="(coming soon)",
                kind="browser-login",
            ),
        ],
    ),
    # MiniMax Anthropic-compatible endpoint: supports thinking mode
    ProviderSpec(
        name="minimax_anthropic",
        keywords=("minimax_anthropic",),
        env_key="MINIMAX_API_KEY",
        display_name="MiniMax (Anthropic)",
        backend="anthropic",
        default_api_base="https://api.minimax.io/anthropic",
        signup_url="https://platform.minimax.io/user-center/payment/token-plan",
        docs_url="https://platform.minimax.io/docs/token-plan/other-tools",
    ),
    # Mistral AI: OpenAI-compatible API
    ProviderSpec(
        name="mistral",
        keywords=("mistral",),
        env_key="MISTRAL_API_KEY",
        display_name="Mistral",
        backend="openai_compat",
        default_api_base="https://api.mistral.ai/v1",
        signup_url="https://console.mistral.ai/api-keys/",
    ),
    # Step Fun: OpenAI-compatible API
    ProviderSpec(
        name="stepfun",
        keywords=("stepfun", "step"),
        env_key="STEPFUN_API_KEY",
        display_name="Step Fun",
        backend="openai_compat",
        default_api_base="https://api.stepfun.com/v1",
        signup_url="https://platform.stepfun.com/interface-key",
    ),
    # Xiaomi MIMO: OpenAI-compatible API
    ProviderSpec(
        name="xiaomi_mimo",
        keywords=("xiaomi_mimo", "mimo"),
        env_key="XIAOMIMIMO_API_KEY",
        display_name="Xiaomi MIMO",
        backend="openai_compat",
        default_api_base="https://api.xiaomimimo.com/v1",
        # TODO: revisit when Xiaomi MIMO ships a public API key portal —
        # currently a model landing page, used as a best-effort placeholder.
        signup_url="https://mimo.xiaomi.com/",
    ),
    # === Local deployment (matched by config key, NOT by api_base) =========
    # vLLM / any OpenAI-compatible local server
    ProviderSpec(
        name="vllm",
        keywords=("vllm",),
        env_key="HOSTED_VLLM_API_KEY",
        display_name="vLLM/Local",
        backend="openai_compat",
        is_local=True,
    ),
    # Ollama (local, OpenAI-compatible)
    ProviderSpec(
        name="ollama",
        keywords=("ollama", "nemotron"),
        env_key="OLLAMA_API_KEY",
        display_name="Ollama",
        backend="openai_compat",
        is_local=True,
        detect_by_base_keyword="11434",
        default_api_base="http://localhost:11434/v1",
    ),
    # LM Studio (local, OpenAI-compatible)
    ProviderSpec(
        name="lm_studio",
        keywords=("lm-studio", "lmstudio", "lm_studio"),
        env_key="LM_STUDIO_API_KEY",
        display_name="LM Studio",
        backend="openai_compat",
        is_local=True,
        detect_by_base_keyword="1234",
        default_api_base="http://localhost:1234/v1",
    ),
    # === OpenVINO Model Server (direct, local, OpenAI-compatible at /v3) ===
    ProviderSpec(
        name="ovms",
        keywords=("openvino", "ovms"),
        env_key="",
        display_name="OpenVINO Model Server",
        backend="openai_compat",
        is_direct=True,
        is_local=True,
        default_api_base="http://localhost:8000/v3",
    ),
    # === Auxiliary (not a primary LLM provider) ============================
    # Groq: mainly used for Whisper voice transcription, also usable for LLM
    ProviderSpec(
        name="groq",
        keywords=("groq",),
        env_key="GROQ_API_KEY",
        display_name="Groq",
        backend="openai_compat",
        default_api_base="https://api.groq.com/openai/v1",
        signup_url="https://console.groq.com/keys",
    ),
    # Qianfan (Baidu): OpenAI-compatible API
    ProviderSpec(
        name="qianfan",
        keywords=("qianfan", "ernie"),
        env_key="QIANFAN_API_KEY",
        display_name="Qianfan",
        backend="openai_compat",
        default_api_base="https://qianfan.baidubce.com/v2",
        signup_url="https://console.bce.baidu.com/qianfan/ais/console/applicationConsole/application",
    ),
    # === Extension providers ================================================
    # Base URL, env-key name, and signup URL are taken from each provider's
    # manifest constants (e.g. XAI_BASE_URL, XAI_API_KEY_ENV_VAR). The
    # openai_compat backend handles all of these — no per-provider subclass
    # needed.
    ProviderSpec(
        name="xai",
        keywords=("xai", "grok"),
        env_key="XAI_API_KEY",
        display_name="xAI",
        backend="openai_compat",
        default_api_base="https://api.x.ai/v1",
        signup_url="https://console.x.ai/",
    ),
    ProviderSpec(
        name="cerebras",
        keywords=("cerebras",),
        env_key="CEREBRAS_API_KEY",
        display_name="Cerebras",
        backend="openai_compat",
        default_api_base="https://api.cerebras.ai/v1",
        signup_url="https://cloud.cerebras.ai/platform",
    ),
    ProviderSpec(
        name="together",
        keywords=("together",),
        env_key="TOGETHER_API_KEY",
        display_name="Together AI",
        backend="openai_compat",
        default_api_base="https://api.together.xyz/v1",
        signup_url="https://api.together.xyz/settings/api-keys",
    ),
    ProviderSpec(
        name="fireworks",
        keywords=("fireworks", "accounts/fireworks"),
        env_key="FIREWORKS_API_KEY",
        display_name="Fireworks",
        backend="openai_compat",
        default_api_base="https://api.fireworks.ai/inference/v1",
        signup_url="https://fireworks.ai/account/api-keys",
    ),
    ProviderSpec(
        name="chutes",
        keywords=("chutes",),
        env_key="CHUTES_API_KEY",
        display_name="Chutes",
        backend="openai_compat",
        default_api_base="https://llm.chutes.ai/v1",
        signup_url="https://chutes.ai/",
    ),
    ProviderSpec(
        name="nvidia",
        keywords=("nvidia", "nim"),
        env_key="NVIDIA_API_KEY",
        display_name="NVIDIA NIM",
        backend="openai_compat",
        default_api_base="https://integrate.api.nvidia.com/v1",
        signup_url="https://build.nvidia.com/",
    ),
    ProviderSpec(
        name="deepinfra",
        keywords=("deepinfra",),
        env_key="DEEPINFRA_API_KEY",
        display_name="DeepInfra",
        backend="openai_compat",
        default_api_base="https://api.deepinfra.com/v1/openai",
        signup_url="https://deepinfra.com/dash/api_keys",
    ),
    ProviderSpec(
        name="venice",
        keywords=("venice",),
        env_key="VENICE_API_KEY",
        display_name="Venice",
        backend="openai_compat",
        default_api_base="https://api.venice.ai/api/v1",
        signup_url="https://venice.ai/settings/api",
    ),
    ProviderSpec(
        name="arcee",
        keywords=("arcee",),
        env_key="ARCEEAI_API_KEY",
        display_name="Arcee",
        backend="openai_compat",
        default_api_base="https://api.arcee.ai/api/v1",
        signup_url="https://www.arcee.ai/",
    ),
    ProviderSpec(
        name="synthetic",
        keywords=("synthetic",),
        env_key="SYNTHETIC_API_KEY",
        display_name="Synthetic",
        backend="openai_compat",
        default_api_base="https://api.synthetic.new/anthropic",
        signup_url="https://synthetic.new/",
    ),
    ProviderSpec(
        name="kimi_coding",
        keywords=("kimi-coding", "kimicode"),
        env_key="KIMI_API_KEY",
        display_name="Kimi (Coding)",
        backend="openai_compat",
        default_api_base="https://api.kimi.com/coding/v1",
        signup_url="https://platform.moonshot.ai/",
    ),
    # Tencent TokenHub: gateway over Tencent Cloud's hosted models
    ProviderSpec(
        name="tencent",
        keywords=("tencent", "hunyuan", "tokenhub"),
        env_key="TOKENHUB_API_KEY",
        display_name="Tencent TokenHub",
        backend="openai_compat",
        is_gateway=True,
        default_api_base="https://tokenhub.tencentmaas.com/v1",
        signup_url="https://tokenhub.tencentmaas.com/",
    ),
    # Vercel AI Gateway: brokered routing across providers, OpenAI-compatible
    ProviderSpec(
        name="vercel_ai_gateway",
        keywords=("vercel-ai-gateway", "vercel"),
        env_key="AI_GATEWAY_API_KEY",
        display_name="Vercel AI Gateway",
        backend="openai_compat",
        is_gateway=True,
        default_api_base="https://ai-gateway.vercel.sh",
    ),
    # Perplexity: direct API (separate from the openrouter-routed default —
    # pick the canonical first-party endpoint).
    ProviderSpec(
        name="perplexity",
        keywords=("perplexity", "sonar"),
        env_key="PERPLEXITY_API_KEY",
        display_name="Perplexity",
        backend="openai_compat",
        default_api_base="https://api.perplexity.ai",
        signup_url="https://www.perplexity.ai/settings/api",
    ),
    # Kilocode: hosted gateway, OpenAI-compatible
    ProviderSpec(
        name="kilocode",
        keywords=("kilocode", "kilo"),
        env_key="KILOCODE_API_KEY",
        display_name="Kilo Code",
        backend="openai_compat",
        is_gateway=True,
        default_api_base="https://api.kilo.ai/api/gateway/",
        signup_url="https://kilo.ai/",
    ),
    # SGLang: local serving framework (treat like vLLM / Ollama).
    ProviderSpec(
        name="sglang",
        keywords=("sglang",),
        env_key="SGLANG_API_KEY",
        display_name="SGLang",
        backend="openai_compat",
        is_local=True,
        default_api_base="http://127.0.0.1:30000/v1",
    ),
    # LiteLLM: self-hosted proxy/gateway, default port 4000. Treat as local.
    ProviderSpec(
        name="litellm",
        keywords=("litellm",),
        env_key="LITELLM_API_KEY",
        display_name="LiteLLM Proxy",
        backend="openai_compat",
        is_local=True,
        default_api_base="http://127.0.0.1:4000",
    ),
)


def signup_url_required(spec: ProviderSpec) -> bool:
    """Whether this provider must carry a non-empty signup_url.

    Excludes gateways (no canonical signup URL — depends on the brokered
    model), local backends (run on user's hardware), direct providers
    (user supplies everything), and OAuth providers (separate login flow).
    """
    return not (spec.is_gateway or spec.is_local or spec.is_direct or spec.is_oauth)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def find_by_name(name: str) -> ProviderSpec | None:
    """Find a provider spec by config field name, e.g. "dashscope"."""
    normalized = to_snake(name.replace("-", "_"))
    for spec in PROVIDERS:
        if spec.name == normalized:
            return spec
    return None
