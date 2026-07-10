export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number) { super(message); }
}

let authToken: string | null = null;

export function readAuthToken(): string | null {
  return authToken;
}

export function storeAuthToken(token: string): void {
  authToken = token.trim() || null;
}

export function clearAuthToken(): void {
  authToken = null;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    const headers = new Headers(init?.headers);
    if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    headers.delete("Authorization");
    const authToken = readAuthToken();
    if (authToken) headers.set("Authorization", `Bearer ${authToken}`);
    // The only supported browser deployment serves UI and API from one origin.
    // Keeping this relative also prevents a build-time base URL from receiving the
    // hosted bearer token or process-memory provider key requests.
    response = await fetch(path, {
      ...init,
      headers,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(
      "api_unreachable",
      "RipplX API is unavailable. Run finwatch serve locally or check the Docker alpha.",
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
