import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { BootstrapContext } from "../context/BootstrapContext";
import type { Bootstrap } from "../types";
import { SettingsPage } from "./SettingsPage";

afterEach(cleanup);

const bootstrap: Bootstrap = {
  setup_required: false,
  sec_user_agent: "",
  account_email: "early@example.com",
  period: "90d",
  model: "z-ai/glm-5.2",
  provider: "z.ai",
  api_key_configured: true,
  analysis_configured: true,
  billing_configured: false,
  billing_status: "free",
};

it("presents the operator-managed model without an API-key field", () => {
  render(<BootstrapContext.Provider value={{ bootstrap, refresh: vi.fn() }}><MemoryRouter><SettingsPage /></MemoryRouter></BootstrapContext.Provider>);

  expect(screen.getByText(/Included with RipplX/)).toBeInTheDocument();
  expect(screen.getByText(/RipplX provides the model connection/)).toBeInTheDocument();
  expect(screen.queryByLabelText(/API key/i)).not.toBeInTheDocument();
  expect(screen.getByDisplayValue("z-ai/glm-5.2")).toBeInTheDocument();
});

function renderSettings(overrides: Partial<Bootstrap>) {
  render(<BootstrapContext.Provider value={{ bootstrap: { ...bootstrap, ...overrides }, refresh: vi.fn() }}><MemoryRouter><SettingsPage /></MemoryRouter></BootstrapContext.Provider>);
}

it("shows a paying member the brokerage entitlement and the read-only boundary", () => {
  renderSettings({ billing_configured: true, billing_status: "active" });

  expect(screen.getByText("Included in your plan")).toBeInTheDocument();
  expect(screen.getByText(/cannot place or cancel an order/)).toBeInTheDocument();
  expect(screen.getByText(/credentials never reach RipplX/)).toBeInTheDocument();
  expect(screen.getByText(/not switched on in this build yet/)).toBeInTheDocument();
});

it("gates the brokerage connection behind the founding plan for a free account", () => {
  renderSettings({ billing_configured: true, billing_status: "free" });

  expect(screen.getByText("Founding plan")).toBeInTheDocument();
  expect(screen.getByText(/part of the founding plan\./)).toBeInTheDocument();
  expect(screen.queryByText(/not switched on in this build yet/)).not.toBeInTheDocument();
});

// The connect control must never look actionable while there is no endpoint behind it.
it("keeps the connect control inert until the backend exists", () => {
  renderSettings({ billing_configured: true, billing_status: "active" });

  expect(screen.getByRole("button", { name: "Connect a brokerage" })).toBeDisabled();
});

it("keeps the brokerage section out of key-free local mode", () => {
  renderSettings({ account_email: null });

  expect(screen.queryByText("Brokerage")).not.toBeInTheDocument();
});
