export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number) { super(message); }
}

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(
      "api_unreachable",
      "RipplX API is unavailable. Run finwatch serve locally or configure VITE_API_BASE_URL for this deployment.",
      0,
    );
  }
  if (response.status === 204) return undefined as T;

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ApiError(
      "invalid_api_response",
      "RipplX API returned a non-JSON response. The frontend is deployed, but /api is not connected to finwatch serve.",
      response.status,
    );
  }

  if (!response.ok) {
    const errorBody = body as { error?: { code?: string; message?: string } };
    throw new ApiError(errorBody.error?.code ?? "request_failed", errorBody.error?.message ?? "Request failed.", response.status);
  }
  return body as T;
}
