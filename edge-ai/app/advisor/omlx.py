"""oMLX client (OpenAI-compatible chat completions, streaming) and the LLM advisor built on it.

Only this module knows the wire format: if oMLX differs from /v1/models + /v1/chat/completions,
change it here. Timeouts: connect 3 s, first token 6 s, total 20 s (all configurable). Any failure
raises LlmError; the worker turns that into a deterministic fallback advisory.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable

import httpx

from app.advisor import fallback, prompts
from app.advisor.fallback import Advisory
from app.advisor.rag import Fact

log = logging.getLogger("aether.omlx")


class LlmError(RuntimeError):
    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind  # connect | http | first_token_timeout | timeout | stream | empty | malformed


class OmlxClient:
    def __init__(
        self, base_url: str, model: str, api_key: str = "", *, first_token_timeout_s: float = 6.0,
        connect_timeout_s: float = 3.0, total_timeout_s: float = 20.0, max_tokens: int = 256,
        temperature: float = 0.0, transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        base = base_url.rstrip("/")
        self.base_url = base[:-3] if base.endswith("/v1") else base
        self.model, self.max_tokens, self.temperature = model, max_tokens, temperature
        self.first_token_timeout_s, self.total_timeout_s = first_token_timeout_s, total_timeout_s
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.AsyncClient(
            transport=transport, headers=self._headers,
            timeout=httpx.Timeout(total_timeout_s, connect=connect_timeout_s))
        self._clock = clock
        self._task: asyncio.Task | None = None
        # reachability, refreshed by the probe loop and by every generation
        self.up: bool | None = None
        self.last_error: str | None = None
        self.last_ok: float | None = None
        self.served_models: list[str] = []
        self.on_transition: list[Callable[[str, str], None]] = []  # ("llm_down"|"llm_recovered", detail)

    # ---- health -----------------------------------------------------------------------------
    def _set(self, up: bool, error: str | None = None) -> None:
        was = self.up
        self.up, self.last_error = up, (None if up else error)
        if up:
            self.last_ok = self._clock()
        if up and was is False:
            self._notify("llm_recovered", "")
        elif not up and was is not False:
            log.error("oMLX unreachable or failing: %s", error)
            self._notify("llm_down", error or "")

    def _notify(self, kind: str, detail: str) -> None:
        for cb in self.on_transition:
            try:
                cb(kind, detail)
            except Exception:  # pragma: no cover
                log.exception("llm transition callback failed")

    async def probe(self) -> None:
        """GET /v1/models: reachability and whether the configured model is served."""
        try:
            r = await self._http.get(f"{self.base_url}/v1/models", timeout=httpx.Timeout(3.0))
            r.raise_for_status()
            self.served_models = [m.get("id", "") for m in r.json().get("data", [])]
            self._set(True)
        except Exception as exc:
            self._set(False, f"{type(exc).__name__}: {exc}"[:300])

    def health(self) -> dict[str, Any]:
        return {
            "status": {True: "up", False: "down", None: "unknown"}[self.up],
            "base_url": self.base_url, "model": self.model,
            "model_served": (self.model in self.served_models) if self.served_models else None,
            "served_models": self.served_models[:10],
            "last_ok_age_s": round(self._clock() - self.last_ok, 1) if self.last_ok is not None else None,
            "last_error": self.last_error,
        }

    async def start(self) -> None:
        async def loop() -> None:
            while True:
                await self.probe()
                await asyncio.sleep(10.0)
        self._task = asyncio.create_task(loop(), name="omlx-probe")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self._http.aclose()

    # ---- generation -------------------------------------------------------------------------
    async def stream(self, prompt: str) -> AsyncIterator[str]:
        """Yield content deltas. Raises LlmError on any failure or timeout."""
        body = {"model": self.model, "messages": [{"role": "user", "content": prompt}],
                "temperature": self.temperature, "max_tokens": self.max_tokens, "stream": True}
        t0 = self._clock()
        got_token = False
        try:
            async with asyncio.timeout(self.total_timeout_s):
                async with self._http.stream("POST", f"{self.base_url}/v1/chat/completions", json=body) as r:
                    if r.status_code != 200:
                        detail = (await r.aread()).decode(errors="replace")[:200]
                        raise LlmError("http", f"{r.status_code} {detail}")
                    lines = r.aiter_lines().__aiter__()
                    while True:
                        try:
                            if got_token:
                                line = await lines.__anext__()
                            else:
                                remaining = self.first_token_timeout_s - (self._clock() - t0)
                                if remaining <= 0:
                                    raise asyncio.TimeoutError
                                line = await asyncio.wait_for(lines.__anext__(), remaining)
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError:
                            raise LlmError("first_token_timeout", f"no token within {self.first_token_timeout_s:g} s") from None
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            delta = json.loads(data)["choices"][0].get("delta", {}).get("content")
                        except (ValueError, KeyError, IndexError, TypeError):
                            raise LlmError("stream", f"bad chunk: {data[:100]}") from None
                        if delta:
                            got_token = True
                            yield delta
        except LlmError as exc:
            if exc.kind in ("http", "stream", "first_token_timeout"):
                self._set(False, str(exc))
            raise
        except asyncio.TimeoutError:
            self._set(False, "timeout")
            raise LlmError("timeout", f"exceeded {self.total_timeout_s:g} s total") from None
        except httpx.ConnectError as exc:
            self._set(False, f"connect: {exc}"[:300])
            raise LlmError("connect", str(exc)) from exc
        except httpx.TimeoutException as exc:
            self._set(False, "connect timeout")
            raise LlmError("connect", f"timeout {type(exc).__name__}") from exc
        except httpx.HTTPError as exc:  # server died or reset mid-generation
            self._set(False, f"{type(exc).__name__}: {exc}"[:300])
            raise LlmError("stream", f"{type(exc).__name__}: {exc}") from exc
        if not got_token:
            raise LlmError("empty", "stream ended without content")
        self._set(True)

    async def advise(
        self, *, cid: str, trigger: str, run_id: str, status: str, confidence: float, dust_check: str,
        summary: dict | None, facts: list[Fact], question: str | None, broker: Any, t0: float,
    ) -> Advisory:
        prompt = prompts.build_prompt(
            compartment_id=cid, status=status, confidence=confidence, dust_check=dust_check,
            summary=summary, facts=facts, question=question)
        text, first_token_ms = "", None
        async for delta in self.stream(prompt):
            if first_token_ms is None:
                first_token_ms = int((self._clock() - t0) * 1000)
            text += delta
            broker.publish("advisory_token", {"run_id": run_id, "compartment_id": cid, "token": delta})
        try:
            if question:
                answer, cited = prompts.parse_answer(text, facts)
                warning, steps = answer, []
            else:
                warning, steps, cited = prompts.parse_advisory(text, facts)
        except prompts.ParseError as exc:
            raise LlmError("malformed", f"{exc}; output: {text[:200]!r}") from None
        return Advisory(
            warning=warning, steps=steps, citations=[f.citation() for f in cited], raw_text=text.strip(),
            model=self.model, fallback=False, facts_used=[f.id for f in cited], first_token_ms=first_token_ms)
