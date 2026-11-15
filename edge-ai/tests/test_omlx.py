import asyncio
import json

import httpx
import pytest

from app.advisor import prompts
from app.advisor.omlx import LlmError, OmlxClient
from app.advisor.rag import Fact
from app.stream import Broker

FACTS = [
    Fact("NTRS-20240002981", "Saffire VI", "CO and CO2 rose more than temperature.", "manual", "NTRS", "https://ntrs.nasa.gov/x"),
    Fact("PSI-98", "SAFFIRE-I", "txt", "investigation", "PSI", "https://psi.nasa.gov/x", "10.1/x", "CC0"),
]
GOOD = ("WARNING: Smoldering anomaly in BAY: CO is 42 ppm and rising.\n1. Alert the crew.\n"
        "2. Follow the approved checklist.\n3. Track CO and temperature.\nSOURCES: F1, F9, PSI-98")


def sse(chunks, model="gemma-test"):
    body = ""
    for c in chunks:
        body += "data: " + json.dumps({"model": model, "choices": [{"delta": c}]}) + "\n\n"
    return body + "data: [DONE]\n\n"


def tokens(text, n=8):
    return [{"content": text[i:i + n]} for i in range(0, len(text), n)]


def client(handler, **kw):
    defaults = dict(first_token_timeout_s=0.3, total_timeout_s=1.0, connect_timeout_s=0.5)
    return OmlxClient("http://omlx:8000/", "gemma-test", "k", transport=httpx.MockTransport(handler), **{**defaults, **kw})


def ok_handler(text=GOOD):
    seen = {}

    def h(request: httpx.Request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "gemma-test"}]})
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=sse([{"role": "assistant"}] + tokens(text)))
    h.seen = seen
    return h


def run_advise(c, **kw):
    args = dict(cid="BAY", trigger="status_change", run_id="r1", status="SMOLDERING_ANOMALY", confidence=0.6,
                dust_check="NOMINAL_NO_EVENT", summary=None, facts=FACTS, question=None, broker=Broker(), t0=0.0)
    args.update(kw)
    return c.advise(**args)


# ---- parsing ------------------------------------------------------------------------------
def test_parse_advisory_maps_tags_and_drops_invented_sources():
    warning, steps, cited = prompts.parse_advisory(GOOD, FACTS)
    assert warning.startswith("Smoldering anomaly") and len(steps) == 3
    assert [f.id for f in cited] == ["NTRS-20240002981", "PSI-98"]  # F9 does not exist -> dropped


@pytest.mark.parametrize("text", ["", "no format at all", "WARNING: x\n1. a\n2. b\nSOURCES: none",
                                  "WARNING: x\n1. a\n2. b\n3. c\n4. d\nSOURCES: none", "1. a\n2. b\n3. c"])
def test_parse_rejects_malformed(text):
    with pytest.raises(prompts.ParseError):
        prompts.parse_advisory(text, FACTS)


def test_parse_sources_none_and_answer():
    _, _, cited = prompts.parse_advisory("WARNING: x\n1. a\n2. b\n3. c\nSOURCES: none", FACTS)
    assert cited == []
    ans, cited = prompts.parse_answer("ANSWER: CO is rising.\nIt crossed 25 ppm.\nSOURCES: F2", FACTS)
    assert ans == "CO is rising. It crossed 25 ppm." and [f.id for f in cited] == ["PSI-98"]
    with pytest.raises(prompts.ParseError):
        prompts.parse_answer("nothing", FACTS)


def test_prompt_contains_the_rules_authoritative_result_and_tags():
    summary = {"window_s": 60, "samples": 3, "channels": {"co_ppm": {"min": 5, "max": 26, "last": 26, "slope_per_s": 2.0}}}
    p = prompts.build_prompt(compartment_id="BAY", status="SMOLDERING_ANOMALY", confidence=0.604,
                             dust_check="NOMINAL_NO_EVENT", summary=summary, facts=FACTS)
    for needle in ("Never invent numbers", "demo thresholds", "not an official NASA checklist", "authoritative",
                   "co_ppm: last 26", "[F1] Saffire VI", "[F2] SAFFIRE-I", "WARNING:", "SOURCES:"):
        assert needle in p
    q = prompts.build_prompt(compartment_id="BAY", status="NOMINAL", confidence=0.95, dust_check="NOMINAL_NO_EVENT",
                             summary=None, facts=[], question="  is it safe?  ")
    assert "ANSWER:" in q and "is it safe?" in q and "(no data available)" in q and "(no facts retrieved)" in q


# ---- client -------------------------------------------------------------------------------
async def test_streams_tokens_parses_and_records_first_token():
    h = ok_handler()
    c = client(h)
    broker = Broker()
    q = broker.subscribe()
    adv = await run_advise(c, broker=broker)
    assert adv.fallback is False and adv.model == "gemma-test" and len(adv.steps) == 3
    assert [x["id"] for x in adv.citations] == ["NTRS-20240002981", "PSI-98"]
    assert adv.first_token_ms is not None and adv.raw_text == GOOD
    body = h.seen["body"]
    assert body["temperature"] == 0.0 and body["max_tokens"] == 256 and body["stream"] is True
    assert body["model"] == "gemma-test" and [m["role"] for m in body["messages"]] == ["user"]
    assert h.seen["auth"] == "Bearer k"
    toks = []
    while not q.empty():
        e, d = q.get_nowait()
        assert e == "advisory_token" and d["run_id"] == "r1"
        toks.append(d["token"])
    assert "".join(toks) == GOOD
    assert c.health()["status"] == "up"
    await c.stop()


async def test_probe_reports_model_availability():
    c = client(ok_handler())
    await c.probe()
    h = c.health()
    assert h["status"] == "up" and h["model_served"] is True and h["served_models"] == ["gemma-test"]
    assert c.base_url == "http://omlx:8000"
    await c.stop()


async def test_base_url_with_v1_suffix_is_normalised():
    assert OmlxClient("http://x:1/v1/", "m").base_url == "http://x:1"


async def test_connect_error_raises_and_marks_down():
    events = []

    def h(request):
        raise httpx.ConnectError("refused")

    c = client(h)
    c.on_transition.append(lambda k, d: events.append(k))
    with pytest.raises(LlmError) as e:
        await run_advise(c)
    assert e.value.kind == "connect" and c.health()["status"] == "down" and events == ["llm_down"]
    with pytest.raises(LlmError):
        await run_advise(c)
    assert events == ["llm_down"]  # transition reported once
    await c.stop()


async def test_http_error_status():
    c = client(lambda r: httpx.Response(404, json={"error": "model not found"}))
    with pytest.raises(LlmError) as e:
        await run_advise(c)
    assert e.value.kind == "http" and "404" in str(e.value)
    await c.stop()


class Stalling(httpx.AsyncByteStream):
    def __init__(self, first=b"", delay=5.0, then_error=False):
        self.first, self.delay, self.then_error = first, delay, then_error

    async def __aiter__(self):
        if self.first:
            yield self.first
        await asyncio.sleep(self.delay)
        if self.then_error:
            raise httpx.ReadError("connection reset")


async def test_first_token_timeout_falls_back_quickly():
    c = client(lambda r: httpx.Response(200, stream=Stalling()), first_token_timeout_s=0.2, total_timeout_s=5)
    loop = asyncio.get_running_loop()
    t = loop.time()
    with pytest.raises(LlmError) as e:
        await run_advise(c)
    assert e.value.kind == "first_token_timeout" and loop.time() - t < 1.0
    await c.stop()


async def test_role_only_chunk_does_not_count_as_first_token():
    first = ("data: " + json.dumps({"choices": [{"delta": {"role": "assistant"}}]}) + "\n\n").encode()
    c = client(lambda r: httpx.Response(200, stream=Stalling(first)), first_token_timeout_s=0.2, total_timeout_s=5)
    with pytest.raises(LlmError) as e:
        await run_advise(c)
    assert e.value.kind == "first_token_timeout"
    await c.stop()


async def test_total_timeout():
    chunk = ("data: " + json.dumps({"choices": [{"delta": {"content": "WARNING: x"}}]}) + "\n\n").encode()
    c = client(lambda r: httpx.Response(200, stream=Stalling(chunk)), first_token_timeout_s=0.5, total_timeout_s=0.4)
    with pytest.raises(LlmError) as e:
        await run_advise(c)
    assert e.value.kind == "timeout"
    await c.stop()


async def test_connection_dies_mid_generation():
    chunk = ("data: " + json.dumps({"choices": [{"delta": {"content": "WARNING: Smold"}}]}) + "\n\n").encode()
    c = client(lambda r: httpx.Response(200, stream=Stalling(chunk, delay=0.01, then_error=True)))
    broker = Broker()
    q = broker.subscribe()
    with pytest.raises(LlmError) as e:
        await run_advise(c, broker=broker)
    assert e.value.kind == "stream" and q.qsize() == 1  # one partial token had been published
    assert c.health()["status"] == "down"
    await c.stop()


async def test_malformed_output_and_empty_stream():
    c = client(ok_handler("I cannot comply."))
    with pytest.raises(LlmError) as e:
        await run_advise(c)
    assert e.value.kind == "malformed" and "I cannot comply" in str(e.value)
    c2 = client(lambda r: httpx.Response(200, content="data: [DONE]\n\n"))
    with pytest.raises(LlmError) as e2:
        await run_advise(c2)
    assert e2.value.kind == "empty"
    await c.stop(); await c2.stop()


async def test_ask_mode_returns_answer_without_steps():
    c = client(ok_handler("ANSWER: CO crossed the 25 ppm demo threshold.\nSOURCES: F1"))
    adv = await run_advise(c, trigger="ask", question="why?")
    assert adv.warning == "CO crossed the 25 ppm demo threshold." and adv.steps == []
    assert adv.facts_used == ["NTRS-20240002981"]
    await c.stop()
