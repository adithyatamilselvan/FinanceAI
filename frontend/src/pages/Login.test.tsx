import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../lib/auth";
import Login from "./Login";

function setup() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter><AuthProvider><Login /></AuthProvider></MemoryRouter>
    </QueryClientProvider>,
  );
}
afterEach(() => vi.restoreAllMocks());

describe("Login page", () => {
  it("validates before calling the server", async () => {
    const spy = vi.spyOn(globalThis, "fetch");
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Log in" }) as HTMLElement);
    expect(await screen.findByText("Enter a valid email")).toBeInTheDocument();
    expect(screen.getByText("Enter your password")).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });

  it("shows the server's message for bad credentials", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ detail: "Incorrect email or password" }), { status: 401, headers: { "Content-Type": "application/json" } }));
    setup();
    await userEvent.type(screen.getByLabelText("Email"), "a@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "wrong-pass1");
    await userEvent.click(screen.getAllByRole("button", { name: "Log in" }).at(-1)!);
    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect email or password");
  });

  it("enforces password rules on the registration form", async () => {
    const spy = vi.spyOn(globalThis, "fetch");
    setup();
    await userEvent.click(screen.getByRole("tab", { name: "Create account" }));
    await userEvent.type(screen.getByLabelText("Name"), "Ann");
    await userEvent.type(screen.getByLabelText("Email"), "ann@example.com");
    await userEvent.type(screen.getByLabelText(/^Password/), "12345678");
    await userEvent.click(screen.getAllByRole("button", { name: "Create account" }).at(-1)!);
    await waitFor(() => expect(screen.getByText("Mix letters with numbers or symbols")).toBeInTheDocument());
    expect(spy).not.toHaveBeenCalled();
  });
});
