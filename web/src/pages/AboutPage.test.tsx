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
  expect(screen.queryByText("Where this is going")).not.toBeInTheDocument();
});
