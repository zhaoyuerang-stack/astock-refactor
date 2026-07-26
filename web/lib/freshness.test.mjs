import assert from "node:assert/strict";
import test from "node:test";

import { latestDateFromRange } from "./freshness.ts";

test("latestDateFromRange extracts the max date from DuckDB date_range", () => {
  assert.equal(latestDateFromRange("2010-01-04 00:00:00~2026-06-18 00:00:00"), "2026-06-18");
  assert.equal(latestDateFromRange("2010-01-04~2026-06-18"), "2026-06-18");
});

test("latestDateFromRange falls back when date_range is missing", () => {
  assert.equal(latestDateFromRange(""), "—");
  assert.equal(latestDateFromRange(undefined), "—");
});
