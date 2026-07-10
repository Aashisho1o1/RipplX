import { afterEach, describe, expect, it, vi } from "vitest";
import { AUTH_TOKEN_STORAGE_KEY, api } from "./client";

describe("api", () => {
  afterEach(() => { window.sessionStorage.clear(); vi.unstubAllGlobals(); });

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

  it("sends the hosted access token only through the authorization header", async () => {
    window.sessionStorage.setItem(AUTH_TOKEN_STORAGE_KEY, "alpha-secret");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), {
      headers: { "Content-Type": "application/json" },
      status: 200,
    }));
    vi.stubGlobal("fetch", fetchMock);

    await api("/api/bootstrap");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/bootstrap");
    expect(url).not.toContain("alpha-secret");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer alpha-secret");
  });

  it("preserves the structured authentication-required error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: { code: "authentication_required", message: "Authentication required." },
    }), {
      headers: { "Content-Type": "application/json" },
      status: 401,
    })));

    await expect(api("/api/bootstrap")).rejects.toMatchObject({
      code: "authentication_required",
      status: 401,
    });
  });
});
