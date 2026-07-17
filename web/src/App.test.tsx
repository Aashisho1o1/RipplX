import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";

afterEach(() => vi.unstubAllGlobals());

it("replaces the shared unlock token with public email-code sign in", async () => {
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
