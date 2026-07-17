import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./client";

describe("api", () => {
  afterEach(() => { vi.unstubAllGlobals(); });

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

  it("uses same-origin cookies and never adds an authorization header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), {
      headers: { "Content-Type": "application/json" },
      status: 200,
    }));
    vi.stubGlobal("fetch", fetchMock);

    await api("/api/bootstrap");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/bootstrap");
    expect(new Headers(init.headers).get("Authorization")).toBeNull();
    expect(init.credentials).toBe("same-origin");
  });

  it("copies the signed CSRF cookie into mutation headers", async () => {
    document.cookie = "__Host-finwatch_csrf=test-csrf-token; Secure; Path=/";
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await api("/api/settings", { method: "PUT", body: "{}" });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("test-csrf-token");
    document.cookie = "__Host-finwatch_csrf=; Secure; Path=/; Max-Age=0";
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
