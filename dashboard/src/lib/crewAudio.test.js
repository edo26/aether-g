import test from "node:test";
import assert from "node:assert/strict";
import { advisorySpeech } from "./crewAudio.js";

test("advisorySpeech reads the warning then numbered steps, without Markdown escapes", () => {
  const text = advisorySpeech({ warning: "Smoldering in AVIONICS\\_BAY\\_B2: CO 28 ppm.", steps: ["Alert the crew.", "Isolate power."] });
  assert.equal(text, "Smoldering in AVIONICS BAY B2: CO 28 p p m. Step 1. Alert the crew. Step 2. Isolate power.");
});

test("advisorySpeech is empty without an advisory", () => {
  assert.equal(advisorySpeech(null), "");
});
