// Runs the real Worker code with a real SQL engine. Only the AI model is simulated.
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";

export function fakeD1() {
  const db = new DatabaseSync(":memory:");
  db.exec(readFileSync(new URL("../migrations/0001_usage.sql", import.meta.url), "utf8"));
  const statement = (sql, params = []) => ({
    bind: (...values) => statement(sql, values),
    run: async () => ({ results: db.prepare(sql).all(...params) }),
    all: async () => ({ results: db.prepare(sql).all(...params) }),
    runNow: () => ({ results: db.prepare(sql).all(...params) }),
  });
  return {
    prepare: (sql) => statement(sql),
    batch: async (items) => items.map((item) => item.runNow()),
    raw: db,
  };
}

export function makeEnv(ai, overrides = {}) {
  return {
    AI: { run: ai },
    DB: fakeD1(),
    TOKEN_SECRET: "test-secret",
    TEXT_MODEL: "@cf/google/gemma-4-26b-a4b-it",
    IMAGE_MODEL: "@cf/black-forest-labs/flux-1-schnell",
    IMAGE_NEURONS: "60",
    INSTALL_DAILY_NEURONS: "2500",
    OVERALL_DAILY_NEURONS: "9500",
    NEURONS_PER_MILLION_INPUT: "9091",
    NEURONS_PER_MILLION_OUTPUT: "27273",
    IP_REQUESTS_PER_MINUTE: "60",
    IP_REGISTRATIONS_PER_DAY: "300",
    ...overrides,
  };
}
