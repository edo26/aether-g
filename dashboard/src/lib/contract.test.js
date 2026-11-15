import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { validateFrame, parseJsonl } from "./contract.js";
import { estimateHeatmap, COLS, ROWS } from "./heatmap.js";

const fx = (n) => readFileSync(new URL(`../../public/fixtures/${n}.jsonl`, import.meta.url), "utf8");

test("all captured fixtures satisfy the contract at 500 ms from t=0", () => {
  for (const s of ["nominal", "dust", "smoldering", "airgapped"]) {
    const frames = parseJsonl(fx(s));
    assert.equal(frames.length, 240);
    frames.forEach((f, i) => assert.equal(f.timestamp_ms, i * 500));
  }
});

test("scenario semantics: dust stays nominal, smoldering trips ~10.5 s, airgapped is NASA_REPLAY", () => {
  const dust = parseJsonl(fx("dust"));
  assert.ok(dust.every((f) => f.stage1_detector.status === "NOMINAL"));
  assert.ok(dust.every((f) => f.stage1_detector.false_alarm_dust_check === "DUST_EVENT_REJECTED_NO_THERMAL_GAS_RISE"));
  const sm = parseJsonl(fx("smoldering"));
  assert.equal(sm.find((f) => f.stage1_detector.status === "SMOLDERING_ANOMALY").timestamp_ms, 10500);
  assert.ok(sm.slice(21).every((f) => f.stage1_detector.status === "SMOLDERING_ANOMALY"));
  assert.ok(parseJsonl(fx("airgapped")).every((f) => f.mode === "NASA_REPLAY"));
});

test("validator rejects extra/missing keys, bad enums, NaN, wrong thermocouple count", () => {
  const good = parseJsonl(fx("nominal"))[0];
  const clone = () => structuredClone(good);
  let f = clone(); f.extra = 1; assert.ok(validateFrame(f).length);
  f = clone(); delete f.telemetry.co_ppm; assert.ok(validateFrame(f).length);
  f = clone(); f.stage1_detector.status = "DUST"; assert.ok(validateFrame(f).length);
  f = clone(); f.telemetry.co_ppm = NaN; assert.ok(validateFrame(f).length);
  f = clone(); f.telemetry.thermocouples_c = [1, 2]; assert.ok(validateFrame(f).length);
  f = clone(); f.stage1_detector.confidence_score = 1.2; assert.ok(validateFrame(f).length);
  f = clone(); f.telemetry.voc_index = 1.5; assert.ok(validateFrame(f).length);
  assert.deepEqual(validateFrame(good), []);
});

test("heatmap is 32x24 and peaks at the IR maximum", () => {
  const h = estimateHeatmap([30, 50, 35], 70);
  assert.equal(h.grid.length, COLS * ROWS);
  assert.ok(Math.abs(Math.max(...h.grid) - 70) < 1.5);
});
