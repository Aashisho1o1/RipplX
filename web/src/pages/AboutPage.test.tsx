import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it } from "vitest";
import { AboutPage } from "./AboutPage";

afterEach(cleanup);

it("describes the shipped product and the evidence-versus-interpretation boundary", () => {
  render(<MemoryRouter><AboutPage /></MemoryRouter>);

  expect(screen.getByText("What works today")).toBeInTheDocument();
  expect(screen.getByText(/interface labels it as conditional/)).toBeInTheDocument();
  expect(screen.getByText("Monitor new filings")).toBeInTheDocument();
  expect(screen.getByText("Read the deep research report")).toBeInTheDocument();
  expect(screen.queryByText("Where this is going")).not.toBeInTheDocument();
});

it("describes the brokerage connection as a read-only paid feature", () => {
  render(<MemoryRouter><AboutPage /></MemoryRouter>);

  expect(screen.getByText("Connect your brokerage")).toBeInTheDocument();
  expect(screen.getByText(/Paying members can link a brokerage/)).toBeInTheDocument();
  expect(screen.getByText(/cannot place or cancel an order/)).toBeInTheDocument();
  expect(screen.getByText("No custody, disconnect any time")).toBeInTheDocument();
});
