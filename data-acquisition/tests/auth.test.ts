import { describe, expect, it } from "vitest";

import { loginStateFromSignals } from "../src/auth.js";


describe("loginStateFromSignals", () => {
  it("reports a visible login control as authentication required", () => {
    const result = loginStateFromSignals(
      "https://www.chuhaijiang.com/app/discover/tiktok/products",
      true,
    );

    expect(result.loggedIn).toBe(false);
    expect(result.reason).toBe("LOGIN_CONTROL_VISIBLE");
    expect(result.userMessage).toContain("登录");
  });

  it("recognizes an authenticated product page", () => {
    const result = loginStateFromSignals(
      "https://www.chuhaijiang.com/app/discover/tiktok/products?country=US",
      false,
    );

    expect(result.loggedIn).toBe(true);
    expect(result.reason).toBe("AUTHENTICATED_HEADER");
    expect(result.userMessage).toBeNull();
  });

  it("treats a login URL as logged out", () => {
    const result = loginStateFromSignals(
      "https://www.chuhaijiang.com/login?redirect=/app/discover",
      false,
    );

    expect(result.loggedIn).toBe(false);
    expect(result.reason).toBe("LOGIN_URL");
  });
});
