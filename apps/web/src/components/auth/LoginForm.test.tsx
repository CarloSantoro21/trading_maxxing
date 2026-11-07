import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { createFakeSupabase } from "@/test/fakeSupabase";
import { authErrorMessage, LoginForm } from "./LoginForm";

function setup() {
  const fake = createFakeSupabase();
  const onSuccess = vi.fn();
  const user = userEvent.setup();
  render(<LoginForm supabase={fake.client} onSuccess={onSuccess} />);
  return { fake, onSuccess, user };
}

describe("LoginForm", () => {
  it("muestra errores de validación y no llama a Supabase", async () => {
    const { fake, user } = setup();
    await user.type(screen.getByLabelText("Correo electrónico"), "no-es-correo");
    await user.type(screen.getByLabelText("Contraseña"), "123");
    await user.click(screen.getByRole("button", { name: "Iniciar sesión" }));

    expect(screen.getByText("Escribe un correo válido.")).toBeInTheDocument();
    expect(screen.getByText("La contraseña debe tener al menos 6 caracteres.")).toBeInTheDocument();
    expect(screen.getByLabelText("Correo electrónico")).toHaveAttribute("aria-invalid", "true");
    expect(fake.auth.signInWithPassword).not.toHaveBeenCalled();
  });

  it("inicia sesión con el usuario demo y avisa con su id", async () => {
    const { fake, onSuccess, user } = setup();
    await user.type(screen.getByLabelText("Correo electrónico"), "  demo@trading-maxxing.local ");
    await user.type(screen.getByLabelText("Contraseña"), "demo1234");
    await user.click(screen.getByRole("button", { name: "Iniciar sesión" }));

    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith("user-1"));
    expect(fake.auth.signInWithPassword).toHaveBeenCalledWith({
      email: "demo@trading-maxxing.local",
      password: "demo1234",
    });
  });

  it("muestra el error de credenciales en español", async () => {
    const { fake, onSuccess, user } = setup();
    fake.auth.signInWithPassword.mockResolvedValueOnce({
      data: { user: null, session: null },
      error: { message: "Invalid login credentials" },
    } as never);
    await user.type(screen.getByLabelText("Correo electrónico"), "demo@trading-maxxing.local");
    await user.type(screen.getByLabelText("Contraseña"), "incorrecta");
    await user.click(screen.getByRole("button", { name: "Iniciar sesión" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Correo o contraseña incorrectos.");
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it("muestra un error de red si Supabase no responde", async () => {
    const { fake, user } = setup();
    fake.auth.signInWithPassword.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await user.type(screen.getByLabelText("Correo electrónico"), "demo@trading-maxxing.local");
    await user.type(screen.getByLabelText("Contraseña"), "demo1234");
    await user.click(screen.getByRole("button", { name: "Iniciar sesión" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo conectar con Supabase");
  });
});

describe("authErrorMessage", () => {
  it("traduce los mensajes conocidos", () => {
    expect(authErrorMessage("Email not confirmed")).toBe("Confirma tu correo antes de iniciar sesión.");
    expect(authErrorMessage("network error")).toMatch(/No se pudo conectar/);
    expect(authErrorMessage(undefined)).toBe("No se pudo iniciar sesión. Intenta de nuevo.");
  });
});
