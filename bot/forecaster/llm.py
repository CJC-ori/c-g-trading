"""LLMClient abstraction — three interchangeable backends.

The pipeline never talks to a model directly; it builds an :class:`LLMRequest`
and hands it to a client. Backends:

- :class:`AnthropicAPIClient` — real Anthropic SDK calls. Fully written for
  future use; NOT exercised in this container (no ANTHROPIC_API_KEY is
  available to scripts here).
- :class:`ReplayCacheClient` — deterministic replay. Responses are keyed by
  ``(prompt_hash, model, params)`` in ``data/forecaster-cache/`` (gitignored
  via ``/data/``). A re-run of the same window costs $0 and produces
  byte-identical decisions (research/cost-architecture.md §7 item 5). Raises
  :class:`CacheMiss` on a missing key — a replay run must never silently call
  a paid oracle.
- :class:`SubagentClient` — executes calls through the local ``claude`` CLI
  in headless mode (``claude -p --model X --system-prompt ... --tools ""``),
  which is the only model access available in this container (harness OAuth;
  no API key for scripts). Every response is written into the replay cache,
  so any SubagentClient run is replayable afterwards. Usage blocks are
  reported exactly as the CLI returns them (input/cache/output token counts)
  and are what the pipeline charges to :class:`bot.backtest.costs.CostLedger`.

Model aliases (probed empirically 2026-08-11 from this container; see
reports/forecaster/model-probe.md):

    'haiku'  -> claude-haiku-4-5-20251001   (knowledge cutoff Feb 2025)
    'sonnet' -> Claude Sonnet 5             (knowledge cutoff Jan 2026)
    'opus'   -> claude-opus-5               (knowledge cutoff May 2026)

Hence Sonnet 5 is the honest judge for markets resolving Feb–May 2026 and
Opus 5 only for June 2026+ (research/cost-architecture.md §8).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = REPO / "data" / "forecaster-cache"

#: alias -> (CLI model arg, canonical price key for CostLedger)
MODELS: dict[str, tuple[str, str]] = {
    "haiku": ("haiku", "claude-haiku-4-5"),
    "sonnet": ("sonnet", "claude-sonnet-5"),
    "opus": ("opus", "claude-opus-5"),
}

#: Empirically probed CLI-alias -> exact model id + cutoff (2026-08-11).
PROBED_MODEL_IDS: dict[str, dict[str, str]] = {
    "haiku": {"id": "claude-haiku-4-5-20251001", "cutoff": "2025-02"},
    "sonnet": {"id": "claude-sonnet-5", "cutoff": "2026-01"},
    "opus": {"id": "claude-opus-5", "cutoff": "2026-05"},
}


class CacheMiss(KeyError):
    """Replay cache has no entry for this request."""


class LLMError(RuntimeError):
    """The backend failed to produce a response."""


@dataclass(frozen=True)
class LLMRequest:
    """One model call. ``params`` must be JSON-serializable and is part of
    the cache key, so any knob that changes the output belongs in it."""

    model: str                # 'haiku' | 'sonnet' | 'opus'
    system: str
    prompt: str
    params: Mapping[str, Any] = field(default_factory=dict)  # e.g. {"effort": "medium"}

    def cache_key(self) -> str:
        blob = json.dumps(
            {
                "model": self.model,
                "system": self.system,
                "prompt": self.prompt,
                "params": dict(sorted(self.params.items())),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str                 # alias used
    model_id: str              # exact id if known
    usage: dict[str, Any]      # Anthropic-shaped usage block (for CostLedger)
    cost_usd_reported: float | None = None  # CLI-reported, cross-check only
    from_cache: bool = False
    cache_key: str = ""

    @property
    def price_model(self) -> str:
        return MODELS[self.model][1]


class ReplayCache:
    """Content-addressed store: one JSON file per cache key, sharded 2-hex."""

    def __init__(self, root: Path | str = DEFAULT_CACHE_DIR):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        p = self._path(key)
        if not p.exists():
            return None
        with open(p) as fh:
            return json.load(fh)

    def put(self, key: str, entry: dict[str, Any]) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w") as fh:
            json.dump(entry, fh, ensure_ascii=False)
        os.replace(tmp, p)

    def export_jsonl(self, keys: list[str], out_path: Path | str) -> int:
        """Export a compact JSONL of the entries behind ``keys`` (for
        reports/ reproducibility — the cache dir itself is gitignored)."""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with open(out_path, "w") as fh:
            for k in keys:
                e = self.get(k)
                if e is None:
                    continue
                fh.write(json.dumps({"key": k, **e}, ensure_ascii=False) + "\n")
                n += 1
        return n


class LLMClient:
    """Interface: complete(request) -> LLMResponse."""

    def complete(self, request: LLMRequest) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError


class ReplayCacheClient(LLMClient):
    """Deterministic: serves only what is already cached."""

    def __init__(self, cache: ReplayCache | None = None):
        self.cache = cache or ReplayCache()

    def complete(self, request: LLMRequest) -> LLMResponse:
        key = request.cache_key()
        entry = self.cache.get(key)
        if entry is None:
            raise CacheMiss(
                f"no cache entry for model={request.model} key={key[:16]}… — "
                "a replay run must not fall through to a paid backend"
            )
        return LLMResponse(
            text=entry["text"],
            model=request.model,
            model_id=entry.get("model_id", ""),
            usage=entry.get("usage", {}),
            cost_usd_reported=entry.get("cost_usd_reported"),
            from_cache=True,
            cache_key=key,
        )


class SubagentClient(LLMClient):
    """Headless ``claude -p`` execution + replay-cache write-through.

    Tool use is disabled (``--tools ""``) and the system prompt instructs
    answer-from-provided-material-only, so the model cannot reach the live
    web — mandatory for point-in-time discipline
    (research/point-in-time-retrieval.md §5.3).
    """

    def __init__(
        self,
        cache: ReplayCache | None = None,
        timeout_s: int = 600,
        max_retries: int = 2,
    ):
        self.cache = cache or ReplayCache()
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._lock = threading.Lock()
        self.calls = 0
        self.cache_hits = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        key = request.cache_key()
        entry = self.cache.get(key)
        if entry is not None:
            with self._lock:
                self.cache_hits += 1
            return LLMResponse(
                text=entry["text"], model=request.model,
                model_id=entry.get("model_id", ""), usage=entry.get("usage", {}),
                cost_usd_reported=entry.get("cost_usd_reported"),
                from_cache=True, cache_key=key,
            )
        if request.model not in MODELS:
            raise LLMError(f"unknown model alias {request.model!r}")
        cli_model = MODELS[request.model][0]
        cmd = [
            "claude", "-p", request.prompt,
            "--model", cli_model,
            "--system-prompt", request.system,
            "--tools", "",
            "--max-turns", "1",
            "--no-session-persistence",
            "--output-format", "json",
        ]
        effort = request.params.get("effort")
        if effort:
            cmd += ["--effort", str(effort)]
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=self.timeout_s,
                    cwd=str(REPO),
                )
                if proc.returncode != 0:
                    raise LLMError(
                        f"claude CLI exit {proc.returncode}: {proc.stderr[-500:]}"
                    )
                data = json.loads(proc.stdout)
                if data.get("is_error"):
                    raise LLMError(f"CLI error result: {str(data)[:500]}")
                text = data.get("result", "")
                usage = data.get("usage", {}) or {}
                entry = {
                    "text": text,
                    "model": request.model,
                    "model_id": PROBED_MODEL_IDS.get(request.model, {}).get("id", ""),
                    "usage": _slim_usage(usage),
                    "cost_usd_reported": data.get("total_cost_usd"),
                    "wall_ms": data.get("duration_ms"),
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                self.cache.put(key, entry)
                with self._lock:
                    self.calls += 1
                return LLMResponse(
                    text=text, model=request.model, model_id=entry["model_id"],
                    usage=entry["usage"], cost_usd_reported=entry["cost_usd_reported"],
                    from_cache=False, cache_key=key,
                )
            except (subprocess.TimeoutExpired, json.JSONDecodeError, LLMError) as e:
                last_err = e
                time.sleep(5 * (attempt + 1))
        raise LLMError(f"claude CLI failed after retries: {last_err}")


def _slim_usage(usage: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only the fields CostLedger prices (drop iteration detail)."""
    out: dict[str, Any] = {}
    for k in (
        "input_tokens", "output_tokens",
        "cache_creation_input_tokens", "cache_read_input_tokens",
    ):
        if k in usage:
            out[k] = usage[k]
    cc = usage.get("cache_creation")
    if isinstance(cc, Mapping):
        out["cache_creation"] = {
            k: cc.get(k, 0)
            for k in ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
        }
    stu = usage.get("server_tool_use")
    if isinstance(stu, Mapping):
        out["server_tool_use"] = {
            "web_search_requests": stu.get("web_search_requests", 0)
        }
    return out


class AnthropicAPIClient(LLMClient):
    """Real SDK backend — written for future use, unused in this container
    (no ANTHROPIC_API_KEY is available to scripts here).

    Uses the Messages API with the exact dated model ids probed for the CLI
    aliases so replay-cache keys stay comparable across backends.
    """

    API_MODEL_IDS = {
        "haiku": "claude-haiku-4-5-20251001",
        "sonnet": "claude-sonnet-5",
        "opus": "claude-opus-5",
    }

    def __init__(self, cache: ReplayCache | None = None, max_tokens: int = 4096):
        self.cache = cache or ReplayCache()
        self.max_tokens = max_tokens
        self._client = None  # lazy: import fails cleanly when SDK absent

    def _sdk(self):
        if self._client is None:
            import anthropic  # noqa: PLC0415 — deliberate lazy import

            self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        return self._client

    def complete(self, request: LLMRequest) -> LLMResponse:
        key = request.cache_key()
        entry = self.cache.get(key)
        if entry is not None:
            return LLMResponse(
                text=entry["text"], model=request.model,
                model_id=entry.get("model_id", ""), usage=entry.get("usage", {}),
                from_cache=True, cache_key=key,
            )
        model_id = self.API_MODEL_IDS[request.model]
        kwargs: dict[str, Any] = dict(
            model=model_id,
            max_tokens=int(request.params.get("max_tokens", self.max_tokens)),
            system=[{
                "type": "text",
                "text": request.system,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": request.prompt}],
        )
        effort = request.params.get("effort")
        if effort:
            kwargs["output_config"] = {"effort": effort}
        msg = self._sdk().messages.create(**kwargs)
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        usage = {
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
            "cache_creation_input_tokens": getattr(
                msg.usage, "cache_creation_input_tokens", 0
            ),
            "cache_read_input_tokens": getattr(
                msg.usage, "cache_read_input_tokens", 0
            ),
        }
        entry = {
            "text": text, "model": request.model, "model_id": model_id,
            "usage": usage, "cost_usd_reported": None,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.cache.put(key, entry)
        return LLMResponse(
            text=text, model=request.model, model_id=model_id, usage=usage,
            from_cache=False, cache_key=key,
        )
