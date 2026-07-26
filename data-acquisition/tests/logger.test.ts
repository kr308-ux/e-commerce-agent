import { describe, expect, it } from "vitest";

import { redactSensitive } from "../src/logger.js";


describe("redactSensitive", () => {
  it("removes keys and bearer-style values from nested log summaries", () => {
    const result = redactSensitive({
      taskId: "task-1",
      apiKey: "sensitive-value",
      nested: {
        Authorization: "Bearer abcdefghijklmnopqrstuvwxyz",
        message: "use sk-1234567890abcdef for this request",
      },
    });

    expect(result).toEqual({
      taskId: "task-1",
      apiKey: "[REDACTED]",
      nested: {
        Authorization: "[REDACTED]",
        message: "use [REDACTED] for this request",
      },
    });
  });
});
