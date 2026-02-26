"""
Unified LLM client for ApplyPilot.
Auto-detects provider from environment (checked in this order):
  LLM_URL         -> Gateway / OpenAI-compatible endpoint (9router, Ollama, etc.)
  GEMINI_API_KEY   -> Google Gemini (default: gemini-2.0-flash)
  OPENAI_API_KEY  -> OpenAI (default: gpt-4o-mini)
LLM_URL takes precedence: when set, Gemini/OpenAI keys are ignored for
provider selection.  LLM_MODEL env var overrides the model name for any provider.
"""

import logging
import os
import time

import httpx

log = logging.getLogger(__name__)

# Default model constants
DEFAULT_FLASH_MODEL = "gemini-2.0-flash"  # Fast, cheap for most tasks
DEFAULT_PRO_MODEL = "gemini-2.5-pro"      # High quality for creative tasks

# Task-specific model environment variables with their defaults
# Each task can override the generic LLM_MODEL via TASK_MODEL env var
# Priority: TASK_MODEL > LLM_MODEL > provider_default
TASK_MODEL_DEFAULTS = {
    "scoring": DEFAULT_FLASH_MODEL,       # Fast, cheap for job scoring
    "tailoring": DEFAULT_PRO_MODEL,       # High quality for resume writing
    "cover_letter": DEFAULT_FLASH_MODEL,  # Standard for cover letters
    "jd_parse": DEFAULT_FLASH_MODEL,      # Fast for JD extraction
    "resume_match": DEFAULT_FLASH_MODEL,  # Fast for gap analysis
    "validation": DEFAULT_FLASH_MODEL,    # Fast for validation checks
    "enrichment": DEFAULT_FLASH_MODEL,    # Fast for job enrichment
    "smart_extract": DEFAULT_FLASH_MODEL, # Fast for smart extraction
}

# Task to environment variable mapping
TASK_MODEL_ENV_VARS = {
    "scoring": "SCORING_MODEL",
    "tailoring": "TAILORING_MODEL",
    "cover_letter": "COVER_LETTER_MODEL",
    "jd_parse": "JD_PARSE_MODEL",
    "resume_match": "RESUME_MATCH_MODEL",
    "validation": "VALIDATION_MODEL",
    "enrichment": "ENRICHMENT_MODEL",
    "smart_extract": "SMART_EXTRACT_MODEL",
}


def _get_task_model(task: str | None, provider: str) -> str | None:
    """Get model name for a specific task.
    
    Priority order:
    1. TASK_MODEL env var (e.g., TAILORING_MODEL)
    2. LLM_MODEL env var (generic override)
    3. Task default from TASK_MODEL_DEFAULTS
    4. Provider default (handled by _detect_provider)
    
    Args:
        task: Task name (e.g., 'scoring', 'tailoring') or None for generic
        provider: Provider identifier ('gemini', 'openai', 'gateway')
        
    Returns:
        Model name or None to use provider default
    """
    if not task:
        return None
    
    # 1. Check task-specific env var
    env_var = TASK_MODEL_ENV_VARS.get(task)
    if env_var:
        task_model = os.environ.get(env_var)
        if task_model:
            return task_model
    
    # 2. Check generic LLM_MODEL override
    generic_model = os.environ.get("LLM_MODEL")
    if generic_model:
        return generic_model
    
    # 3. Use task default (with provider-specific adjustments)
    default_model = TASK_MODEL_DEFAULTS.get(task)
    if default_model and provider == "openai":
        # Map Gemini defaults to OpenAI equivalents
        model_mapping = {
            "gemini-2.0-flash": "gpt-5-mini",
            "gemini-2.5-pro": "gpt-5",
        }
        return model_mapping.get(default_model, "gpt-5-mini")
    
    return default_model

# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

def _detect_provider(task: str | None = None) -> tuple[str, str, str]:
    """Return (base_url, model, api_key) based on environment variables.
    Priority order (router-first):
      1. LLM_URL  – gateway / OpenAI-compatible endpoint
      2. GEMINI_API_KEY – Google Gemini
      3. OPENAI_API_KEY – OpenAI
    Reads env at call time (not module import time) so that load_env() called
    in _bootstrap() is always visible here.
    
    Args:
        task: Optional task name for task-specific model selection
    """
    llm_url = os.environ.get("LLM_URL", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    model_override = os.environ.get("LLM_MODEL", "")
    
    # 1. Gateway / OpenAI-compatible (highest priority)
    if llm_url:
        task_model = _get_task_model(task, "gateway")
        return (
            llm_url.rstrip("/"),
            task_model or model_override or "local-model",
            os.environ.get("LLM_API_KEY", ""),
        )

    # 2. Gemini fallback
    if gemini_key:
        task_model = _get_task_model(task, "gemini")
        return (
            "https://generativelanguage.googleapis.com/v1beta/openai",
            task_model or model_override or "gemini-2.0-flash",
            gemini_key,
        )

    # 3. OpenAI fallback
    if openai_key:
        return (
            "https://api.openai.com/v1",
            model_override or "gpt-4o-mini",
            openai_key,
        )
    raise RuntimeError(
        "No LLM provider configured. "
        "Set LLM_URL, GEMINI_API_KEY, or OPENAI_API_KEY in your environment."
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_MAX_RETRIES = 5
_TIMEOUT = 120  # seconds

# Base wait on first 429/503 (doubles each retry, caps at 60s).
# Gemini free tier is 15 RPM = 4s minimum between requests; 10s gives headroom.
_RATE_LIMIT_BASE_WAIT = 10


_GEMINI_COMPAT_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
_GEMINI_NATIVE_BASE = "https://generativelanguage.googleapis.com/v1beta"


class LLMClient:
    """Thin LLM client supporting OpenAI-compatible and native Gemini endpoints.

    For Gemini keys, starts on the OpenAI-compat layer. On a 403 (which
    happens with preview/experimental models not exposed via compat), it
    automatically switches to the native generateContent API and stays there
    for the lifetime of the process.
    """

    def __init__(self, base_url: str, model: str, api_key: str) -> None:
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self._client = httpx.Client(timeout=_TIMEOUT)
        # True once we've confirmed the native Gemini API works for this model
        self._use_native_gemini: bool = False
        self._is_gemini: bool = base_url.startswith(_GEMINI_COMPAT_BASE)

    # -- Native Gemini API --------------------------------------------------

    def _chat_native_gemini(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Call the native Gemini generateContent API.

        Used automatically when the OpenAI-compat endpoint returns 403,
        which happens for preview/experimental models not exposed via compat.

        Converts OpenAI-style messages to Gemini's contents/systemInstruction
        format transparently.
        """
        contents: list[dict] = []
        system_parts: list[dict] = []

        for msg in messages:
            role = msg["role"]
            text = msg.get("content", "")
            if role == "system":
                system_parts.append({"text": text})
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": text}]})
            elif role == "assistant":
                # Gemini uses "model" instead of "assistant"
                contents.append({"role": "model", "parts": [{"text": text}]})

        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}

        url = f"{_GEMINI_NATIVE_BASE}/models/{self.model}:generateContent"
        resp = self._client.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            params={"key": self.api_key},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

    # -- OpenAI-compat API --------------------------------------------------

    def _chat_compat(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Call the OpenAI-compatible endpoint."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        resp = self._client.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=headers,
        )

        # 403 on Gemini compat = model not available on compat layer.
        # Raise a specific sentinel so chat() can switch to native API.
        if resp.status_code == 403 and self._is_gemini:
            raise _GeminiCompatForbidden(resp)

        return self._handle_compat_response(resp)

    @staticmethod
    def _handle_compat_response(resp: httpx.Response) -> str:
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    # -- public API ---------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Send a chat completion request and return the assistant message text."""
        # Qwen3 optimization: prepend /no_think to skip chain-of-thought
        # reasoning, saving tokens on structured extraction tasks.
        if "qwen" in self.model.lower() and messages:
            first = messages[0]
            if first.get("role") == "user" and not first["content"].startswith("/no_think"):
                messages = [{"role": first["role"], "content": f"/no_think\n{first['content']}"}] + messages[1:]

        for attempt in range(_MAX_RETRIES):
            try:
                # Route to native Gemini if we've already confirmed it's needed
                if self._use_native_gemini:
                    return self._chat_native_gemini(messages, temperature, max_tokens)

                return self._chat_compat(messages, temperature, max_tokens)

            except _GeminiCompatForbidden as exc:
                # Model not available on OpenAI-compat layer — switch to native.
                log.warning(
                    "Gemini compat endpoint returned 403 for model '%s'. "
                    "Switching to native generateContent API. "
                    "(Preview/experimental models are often compat-only on native.)",
                    self.model,
                )
                self._use_native_gemini = True
                # Retry immediately with native — don't count as a rate-limit wait
                try:
                    return self._chat_native_gemini(messages, temperature, max_tokens)
                except httpx.HTTPStatusError as native_exc:
                    raise RuntimeError(
                        f"Both Gemini endpoints failed. Compat: 403 Forbidden. "
                        f"Native: {native_exc.response.status_code} — "
                        f"{native_exc.response.text[:200]}"
                    ) from native_exc

            except httpx.HTTPStatusError as exc:
                resp = exc.response
                if resp.status_code in (429, 503) and attempt < _MAX_RETRIES - 1:
                    # Respect Retry-After header if provided (Gemini sends this).
                    retry_after = (
                        resp.headers.get("Retry-After")
                        or resp.headers.get("X-RateLimit-Reset-Requests")
                    )
                    if retry_after:
                        try:
                            wait = float(retry_after)
                        except (ValueError, TypeError):
                            wait = _RATE_LIMIT_BASE_WAIT * (2 ** attempt)
                    else:
                        wait = min(_RATE_LIMIT_BASE_WAIT * (2 ** attempt), 60)

                    log.warning(
                        "LLM rate limited (HTTP %s). Waiting %ds before retry %d/%d. "
                        "Tip: Gemini free tier = 15 RPM. Consider a paid account "
                        "or switching to a local model.",
                        resp.status_code, wait, attempt + 1, _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                raise

            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES - 1:
                    wait = min(_RATE_LIMIT_BASE_WAIT * (2 ** attempt), 60)
                    log.warning(
                        "LLM request timed out, retrying in %ds (attempt %d/%d)",
                        wait, attempt + 1, _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                raise

        raise RuntimeError("LLM request failed after all retries")

    def ask(self, prompt: str, **kwargs) -> str:
        """Convenience: single user prompt -> assistant response."""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    def close(self) -> None:
        self._client.close()


class _GeminiCompatForbidden(Exception):
    """Sentinel: Gemini OpenAI-compat returned 403. Switch to native API."""
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        super().__init__(f"Gemini compat 403: {response.text[:200]}")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: LLMClient | None = None


def get_client() -> LLMClient:
    """Return (or create) the module-level LLMClient singleton."""
    global _instance
    if _instance is None:
        base_url, model, api_key = _detect_provider()
        log.info("LLM provider: %s  model: %s", base_url, model)
        _instance = LLMClient(base_url, model, api_key)
    return _instance


# Task-specific client cache to avoid recreating clients for same task
_task_clients: dict[str, LLMClient] = {}


def get_client_for_task(task: str) -> LLMClient:
    """Return (or create) an LLMClient for a specific task.
    
    This allows different tasks to use different models based on their
    requirements (e.g., fast/cheap for scoring, high-quality for tailoring).
    
    Priority for model selection:
    1. TASK_MODEL env var (e.g., TAILORING_MODEL=gpt-4)
    2. LLM_MODEL env var (generic override)
    3. Task default from TASK_MODEL_DEFAULTS
    4. Provider default
    
    Args:
        task: Task name (e.g., 'scoring', 'tailoring', 'cover_letter')
        
    Returns:
        LLMClient configured for the specific task
        
    Example:
        client = get_client_for_task('tailoring')
        response = client.ask(prompt)
    """
    global _task_clients
    
    # Return cached client for this task if exists
    if task in _task_clients:
        return _task_clients[task]
    
    # Create new client with task-specific model
    base_url, model, api_key = _detect_provider(task)
    log.info("LLM provider for %s: %s  model: %s", task, base_url, model)
    client = LLMClient(base_url, model, api_key)
    
    # Cache it
    _task_clients[task] = client
    return client


def get_client() -> LLMClient:
    """Return (or create) the module-level LLMClient singleton.
    
    Note: This uses the generic model selection (no task-specific overrides).
    For task-specific model selection, use get_client_for_task(task).
    """
    global _instance
    if _instance is None:
        base_url, model, api_key = _detect_provider()
        log.info("LLM provider: %s  model: %s", base_url, model)
        _instance = LLMClient(base_url, model, api_key)
    return _instance
