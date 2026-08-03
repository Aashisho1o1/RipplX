import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";

afterEach(() => {
  cleanup();               // without this the previous test's DOM leaks into the next
  vi.unstubAllGlobals();
  window.history.pushState({}, "", "/");
});

it("replaces the shared unlock token with public email-code sign in", async () => {
  // Sign-in is no longer the landing screen; it is reached deliberately.
  window.history.pushState({}, "", "/signin");
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({
      error: { code: "authentication_required", message: "Sign in with your email to continue." },
    }), { status: 401, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      challenge_id: "challenge_identifier_1234567890",
      expires_in: 600,
    }), { status: 202, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  expect(await screen.findByRole("heading", { name: "Sign in to RipplX" })).toBeInTheDocument();
  expect(screen.queryByText(/operator access token/i)).not.toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "person@example.com" } });
  fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));

  expect(await screen.findByRole("heading", { name: "Check your email" })).toBeInTheDocument();
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  const [, init] = fetchMock.mock.calls[1] as [string, RequestInit];
  expect(JSON.parse(String(init.body))).toEqual({ email: "person@example.com" });
});

it("leaves sign-in after the first successful one-time-code submission", async () => {
  window.history.pushState({}, "", "/signin");
  let verified = false;
  const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/auth/request-code") {
      return Promise.resolve(new Response(JSON.stringify({
        challenge_id: "challenge_identifier_1234567890", expires_in: 600,
      }), { status: 202, headers: { "Content-Type": "application/json" } }));
    }
    if (url === "/api/auth/verify-code") {
      verified = true;
      return Promise.resolve(new Response(null, { status: 204 }));
    }
    if (url === "/api/bootstrap" && !verified) {
      return Promise.resolve(new Response(JSON.stringify({
        error: { code: "authentication_required", message: "Sign in with your email to continue." },
      }), { status: 401, headers: { "Content-Type": "application/json" } }));
    }
    if (url === "/api/bootstrap") {
      return Promise.resolve(new Response(JSON.stringify({
        setup_required: false, sec_user_agent: "", account_email: "person@example.com",
        period: "90d", model: "openai/test", provider: "openai",
        api_key_configured: true, analysis_configured: true,
      }), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    if (url === "/api/brief") {
      return Promise.resolve(new Response(JSON.stringify({
        period: { covered_label: "", filings_in_window: 0, analyzed_filings: 0, published_filings: 0, withheld_filings: 0, filings_tracked_total: 0, outside_window: null },
        tracked_tickers: [], answer: "Signed-in brief", filings: [], gate_removed_filings: [],
        verified_numbers: [], open_questions: [], reviewed_filings: [], withheld_filings: [],
        tracked_but_unanalyzed: false, filings_synced: 0, disclaimer: "d", sample_data: false,
      }), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  fireEvent.change(await screen.findByLabelText("Email"), { target: { value: "person@example.com" } });
  fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));
  fireEvent.change(await screen.findByLabelText("Sign-in code"), { target: { value: "123456" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

  expect(await screen.findByText("Signed-in brief")).toBeInTheDocument();
  expect(window.location.pathname).toBe("/today");
  expect(fetchMock.mock.calls.filter(([url]) => String(url) === "/api/auth/verify-code")).toHaveLength(1);
});

it("lands a signed-out visitor on the sample instead of a sign-in form", async () => {
  // Asking a stranger for an email address before showing anything is friction with no
  // payoff: they have not seen the product yet. The private bootstrap 401s, and the app
  // sends them to the public sample rather than a login card.
  const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/public/sample/bootstrap")) {
      return Promise.resolve(new Response(JSON.stringify({
        setup_required: false, sec_user_agent: "", account_email: null, period: "90d",
        model: "", provider: null, api_key_configured: false, analysis_configured: false,
      }), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    if (url.includes("/api/public/sample/brief")) {
      return Promise.resolve(new Response(JSON.stringify({
        period: { covered_label: "", filings_in_window: 0, analyzed_filings: 0, published_filings: 0, withheld_filings: 0, filings_tracked_total: 0, outside_window: null },
        tracked_tickers: [], answer: "Sample answer", filings: [], gate_removed_filings: [],
        verified_numbers: [], open_questions: [], reviewed_filings: [], withheld_filings: [],
        tracked_but_unanalyzed: false, filings_synced: 0, disclaimer: "d", sample_data: true,
      }), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    return Promise.resolve(new Response(JSON.stringify({
      error: { code: "authentication_required", message: "Sign in with your email to continue." },
    }), { status: 401, headers: { "Content-Type": "application/json" } }));
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  // The sample renders, and sign-in is offered as an action rather than demanded.
  expect(await screen.findByText("Sample answer")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Sign in to RipplX" })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/signin");
});
