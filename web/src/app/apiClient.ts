const STOIC_API_KEY = import.meta.env.VITE_STOIC_API_KEY || "";

export async function apiClient(input: string | URL | Request, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers || {});
  if (STOIC_API_KEY) {
    headers.set("X-API-Key", STOIC_API_KEY);
  }

  return fetch(input, {
    ...init,
    headers,
  });
}
