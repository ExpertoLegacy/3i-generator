-- Only anonymous counters. No prompt or response text is ever stored.
CREATE TABLE IF NOT EXISTS usage (
  day TEXT NOT NULL,
  scope TEXT NOT NULL,
  neurons INTEGER NOT NULL DEFAULT 0,
  requests INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, scope)
);
