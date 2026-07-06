import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./client";

describe("api", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reports an unconnected API instead of leaking a JSON parse error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<!doctype html>", {
      headers: { "Content-Type": "text/html" },
      status: 200,
    })));

    await expect(api("/api/bootstrap")).rejects.toMatchObject({
      code: "invalid_api_response",
      message: expect.stringContaining("/api is not connected"),
    });
  });
});
