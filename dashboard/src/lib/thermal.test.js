import test from "node:test";
import assert from "node:assert/strict";
import { isMeasuredSource, thermalAgeS, thermalStats, validateThermal } from "./thermal.js";

const frame = () => {
  const px = new Array(768).fill(24);
  px[5 * 32 + 20] = 61.5;
  return { width: 32, height: 24, pixels_c: px, age_s: 0.4, source: "jetson" };
};

test("valid MLX90640 frame passes, broken ones do not", () => {
  assert.deepEqual(validateThermal(frame()), []);
  assert.ok(validateThermal({ ...frame(), pixels_c: new Array(767).fill(24) }).length);
  assert.ok(validateThermal({ ...frame(), pixels_c: [...new Array(767).fill(24), "hot"] }).length);
  assert.ok(validateThermal(null).length);
});

test("stats find the hottest pixel", () => {
  const s = thermalStats(frame());
  assert.equal(s.min, 24);
  assert.equal(s.max, 61.5);
  assert.deepEqual(s.hotspot, { x: 20, y: 5, temp_c: 61.5 });
});

test("age adds the time since the fetch to the server age", () => {
  assert.equal(thermalAgeS(frame(), 1000, 3000), 2.4);
  assert.equal(thermalAgeS(null, 1000, 3000), null);
});

test("only jetson frames count as measured", () => {
  assert.ok(isMeasuredSource("jetson"));
  assert.ok(!isMeasuredSource("simulator"));
});
