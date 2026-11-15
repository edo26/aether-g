import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { parseJsonl } from "./contract.js";
import { prettyCompartment, summarize } from "./summary.js";

const fx = (n) => parseJsonl(readFileSync(new URL(`../../public/fixtures/${n}.jsonl`, import.meta.url), "utf8"));

test("compartment ids read as names", () => {
  assert.equal(prettyCompartment("AVIONICS_BAY_B2"), "Avionics Bay B2");
  assert.equal(prettyCompartment(undefined), "this compartment");
});

test("no frames: waiting", () => {
  const s = summarize([]);
  assert.equal(s.tone, "idle");
  assert.match(s.headline, /Waiting/);
});

test("smoldering after the trip: alarm summary with CO, trigger, hottest probe and checklist", () => {
  const frames = fx("smoldering").slice(0, 45); // trips at frame 21 (10.5 s)
  const s = summarize(frames);
  assert.equal(s.tone, "alarm");
  assert.equal(s.headline, "Possible smoldering fire in Avionics Bay B2.");
  const text = s.details.join(" ");
  assert.match(text, /CO is [\d.]+ ppm and rising [\d.]+ ppm\/s, above the 25 ppm demo trigger for 12 s\./);
  assert.match(text, /probe P\d at [\d.]+ °C/);
  assert.match(text, /approved checklist/);
});

test("smoldering before the trip still reads as no fire", () => {
  const s = summarize(fx("smoldering").slice(0, 10));
  assert.equal(s.tone, "ok");
  assert.match(s.headline, /No fire detected/);
});

test("dust stays no-fire and explains the rejection", () => {
  const s = summarize(fx("dust").slice(0, 30));
  assert.equal(s.tone, "ok");
  assert.match(s.headline, /No fire detected/);
  assert.match(s.details.join(" "), /rejected as dust/);
});

test("stale data is called out first", () => {
  const s = summarize(fx("nominal").slice(0, 20), { stale: true, ageS: 7.4 });
  assert.match(s.details[0], /No new data for 7 s/);
});
