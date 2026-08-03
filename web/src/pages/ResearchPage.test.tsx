import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation, useParams } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BootstrapContext } from "../context/BootstrapContext";
import type { Bootstrap, Job } from "../types";
import { ResearchPage } from "./ResearchPage";

const bootstrap: Bootstrap = {
  setup_required: false,
  sec_user_agent: "RipplX Test test@example.com",
  account_email: "investor@example.com",
  period: "90d",
  model: "openai/test",
  provider: "openai",
  api_key_configured: true,
  analysis_configured: true,
  billing_configured: false,
  billing_status: "free",
};

const job = (kind: Job["kind"], state: Job["state"]): Job => ({
  id: `${kind === "sync" ? "1" : "2"}`.repeat(32),
  kind,
  state,
  created_at: "2026-08-03T00:00:00Z",
  items: [],
  error: null,
});

function Destination() {
  const { ticker } = useParams();
  const location = useLocation();
  const state = location.state as { initialJob?: Job; autoResearch?: boolean } | null;
  return <div>{ticker} · {state?.initialJob?.kind ?? "none"} · {String(state?.autoResearch)}</div>;
}

function renderResearch(initialEntry = "/research", value = bootstrap) {
  return render(
    <BootstrapContext.Provider value={{ bootstrap: value, refresh: vi.fn() }}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/research" element={<ResearchPage />} />
          <Route path="/companies/:ticker" element={<Destination />} />
        </Routes>
      </MemoryRouter>
    </BootstrapContext.Provider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("before-you-buy launch flow", () => {
  it("adds the ticker, waits for SEC metrics, and hands live analysis to the company page", async () => {
    const fetcher = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      let body: unknown;
      if (url === "/api/companies" && method === "GET") body = { companies: [] };
      else if (url === "/api/companies" && method === "POST") body = { ticker: "MSFT", cik: "0000789019", newest_supported_filing: null, compressed_verified_read: null };
      else if (url === "/api/jobs/sync" && method === "POST") body = job("sync", "queued");
      else if (url === `/api/jobs/${"1".repeat(32)}`) body = job("sync", "completed");
      else if (url === "/api/jobs/analyze" && method === "POST") body = job("analysis", "queued");
      else throw new Error(`Unexpected request: ${method} ${url}`);
      return Promise.resolve(new Response(JSON.stringify(body), { status: method === "POST" ? 202 : 200, headers: { "Content-Type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetcher);
    renderResearch();

    fireEvent.change(await screen.findByLabelText("Ticker symbol"), { target: { value: "MSFT" } });
    fireEvent.click(screen.getByRole("button", { name: "Research company" }));

    expect(await screen.findByText("MSFT · analysis · true", {}, { timeout: 3000 })).toBeInTheDocument();
    const requests = fetcher.mock.calls.map(([url, init]) => `${(init as RequestInit | undefined)?.method ?? "GET"} ${String(url)}`);
    expect(requests).toEqual([
      "GET /api/companies",
      "POST /api/companies",
      "POST /api/jobs/sync",
      `GET /api/jobs/${"1".repeat(32)}`,
      "POST /api/jobs/analyze",
    ]);
  });

  it("opens a known public showcase company without attempting a mutation", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      companies: [{ ticker: "MSFT", cik: "0000789019", newest_supported_filing: "2026-07-30", compressed_verified_read: "6/6 verified" }],
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetcher);
    renderResearch("/research?demo=1", { ...bootstrap, account_email: null, analysis_configured: false });

    fireEvent.change(await screen.findByLabelText("Ticker symbol"), { target: { value: "MSFT" } });
    fireEvent.click(screen.getByRole("button", { name: "Research company" }));

    expect(await screen.findByText("MSFT · none · undefined")).toBeInTheDocument();
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    expect(String(fetcher.mock.calls[0]?.[0] ?? "")).toBe("/api/public/sample/companies");
  });

  it("still opens verified SEC numbers when qualitative analysis is unavailable", async () => {
    const fetcher = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      let body: unknown;
      if (url === "/api/companies" && method === "GET") body = { companies: [{ ticker: "MSFT", cik: "0000789019", newest_supported_filing: null, compressed_verified_read: null }] };
      else if (url === "/api/jobs/sync" && method === "POST") body = job("sync", "queued");
      else if (url === `/api/jobs/${"1".repeat(32)}`) body = job("sync", "completed");
      else throw new Error(`Unexpected request: ${method} ${url}`);
      return Promise.resolve(new Response(JSON.stringify(body), { status: method === "POST" ? 202 : 200, headers: { "Content-Type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetcher);
    renderResearch("/research", { ...bootstrap, analysis_configured: false });

    fireEvent.change(await screen.findByLabelText("Ticker symbol"), { target: { value: "MSFT" } });
    fireEvent.click(screen.getByRole("button", { name: "Research company" }));

    expect(await screen.findByText("MSFT · sync · false", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(fetcher.mock.calls.some(([url]) => String(url) === "/api/jobs/analyze")).toBe(false);
  });
});
