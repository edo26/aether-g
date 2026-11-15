import test from "node:test";
import assert from "node:assert/strict";
import { advisoryReducer, initialAdvisory, isLlm, parseStreaming, plain } from "./advisory.js";

const CID = "AVIONICS_BAY_B2";
const adv = (id, extra = {}) => ({ id, compartment_id: CID, trigger: "status_change", status: "SMOLDERING_ANOMALY",
  warning: "w", steps: ["a", "b", "c"], citations: [], model: "gemma-4-E4B-it-MLX-4bit", fallback: false, ...extra });
const run = (s, a) => a.reduce(advisoryReducer, s);
const start = (s) => advisoryReducer(s, { type: "reset", compartmentId: CID });

test("history picks the newest non-ask advisory for this compartment", () => {
  const s = run(start(initialAdvisory), [{ type: "history", disclaimer: "d",
    advisories: [adv(9, { trigger: "ask" }), adv(8, { compartment_id: "OTHER" }), adv(7), adv(6)] }]);
  assert.equal(s.current.id, 7);
  assert.equal(s.disclaimer, "d");
});

test("start shows the preliminary, tokens append, done replaces with the final advisory", () => {
  let s = run(start(initialAdvisory), [
    { type: "advisory_start", data: { run_id: "r1", compartment_id: CID, trigger: "status_change", llm: true,
      preliminary: { warning: "rule text", steps: ["x", "y", "z"], model: "deterministic" } } },
    { type: "advisory_token", data: { run_id: "r1", token: "WARN" } },
    { type: "advisory_token", data: { run_id: "other", token: "ignored" } },
    { type: "advisory_token", data: { run_id: "r1", token: "ING:" } },
  ]);
  assert.equal(s.run.text, "WARNING:");
  assert.equal(s.run.preliminary.warning, "rule text");
  s = advisoryReducer(s, { type: "advisory_done", data: { run_id: "r1", advisory: adv(10, { disclaimer: "dd" }) } });
  assert.equal(s.run, null);
  assert.equal(s.current.id, 10);
  assert.equal(s.disclaimer, "dd");
});

test("cancelled runs and crew questions never replace the alarm advisory", () => {
  let s = run(start(initialAdvisory), [{ type: "history", advisories: [adv(5)] },
    { type: "advisory_start", data: { run_id: "q", compartment_id: CID, trigger: "ask" } },
    { type: "advisory_done", data: { run_id: "q", advisory: adv(6, { trigger: "ask" }) } },
    { type: "advisory_start", data: { run_id: "c", compartment_id: CID, trigger: "periodic" } },
    { type: "advisory_done", data: { run_id: "c", compartment_id: CID, cancelled: true } }]);
  assert.equal(s.current.id, 5);
  assert.equal(s.run, null);
});

test("a late history fetch does not roll back a newer streamed advisory", () => {
  const s = run(start(initialAdvisory), [
    { type: "advisory_done", data: { run_id: "r", advisory: adv(12) } },
    { type: "history", advisories: [adv(11)] }]);
  assert.equal(s.current.id, 12);
});

test("helpers", () => {
  assert.equal(plain("AVIONICS\\_BAY\\_B2"), "AVIONICS_BAY_B2");
  assert.ok(isLlm(adv(1)));
  assert.ok(!isLlm(adv(1, { model: "deterministic", fallback: true })));
});

test("streaming text fills warning and steps in place, matching the final layout", () => {
  assert.deepEqual(parseStreaming("WARN"), { warning: "", steps: [] });
  assert.deepEqual(parseStreaming("WARNING: CO is 27 ppm in AVIONICS\\_BAY"), { warning: "CO is 27 ppm in AVIONICS_BAY", steps: [] });
  const mid = parseStreaming("WARNING: CO is 27 ppm.\nWHY: Saffire VI saw CO rise first [F1].\n1. Alert the crew.\n2. Conf");
  assert.equal(mid.warning, "CO is 27 ppm. Saffire VI saw CO rise first.");
  assert.deepEqual(mid.steps, ["Alert the crew.", "Conf"]);
  const done = parseStreaming("WARNING: x.\n1. a\n2. b\n3. c\nSOURCES: F1");
  assert.deepEqual(done.steps, ["a", "b", "c"]);
});
