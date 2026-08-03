import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { BootstrapContext } from "../context/BootstrapContext";
import type { Bootstrap } from "../types";
import { SettingsPage } from "./SettingsPage";

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
