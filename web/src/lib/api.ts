import type { Branch, Comparison, Provider, Tree, TreeSummary } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryAfterSeconds: number | null = null,
  ) {
    super(message);
  }
}

/** FastAPI sends `detail` as a string, or as a list of field errors. */
export function errorMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const first = detail[0] as { msg?: unknown } | undefined;
    if (typeof first?.msg === "string") return first.msg;
  }
  return `The server returned an error (${status}).`;
}

const send = (path: string, init?: RequestInit): Promise<Response> =>
  fetch(path, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await send(path, init);
    // The proxy can reuse a connection the API just closed after a live stream, which fails with a
    // 500 before reaching the API. A read is safe to repeat. A write isn't, so it reports the error.
    if (response.status >= 500 && (init?.method ?? "GET") === "GET") {
      response = await send(path, init);
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Can't reach the API. Check that the stack is running.", 0);
  }
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const retryAfter = Number(response.headers.get("Retry-After"));
    throw new ApiError(
      errorMessage(body, response.status),
      response.status,
      Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null,
    );
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

const post = (body?: unknown): RequestInit => ({
  method: "POST",
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  providers: () => request<Provider[]>("/api/providers"),
  saveKey: (provider: string, apiKey: string) =>
    request<Provider>(`/api/keys/${provider}`, {
      method: "PUT",
      body: JSON.stringify({ api_key: apiKey }),
    }),
  deleteKey: (provider: string) => request<void>(`/api/keys/${provider}`, { method: "DELETE" }),
  trees: () => request<TreeSummary[]>("/api/trees"),
  tree: (id: string, signal?: AbortSignal) => request<Tree>(`/api/trees/${id}`, { signal }),
  createTree: (task: string, provider: string | null, model: string | null) =>
    request<{ tree_id: string; branch_id: string }>(
      "/api/trees",
      post({ task, provider, model }),
    ),
  fork: (nodeId: string, edit: Record<string, unknown>) =>
    request<Branch>(`/api/nodes/${nodeId}/fork`, post({ edit })),
  cancel: (branchId: string) => request<Branch>(`/api/branches/${branchId}/cancel`, post()),
  compare: (a: string, b: string) =>
    request<Comparison>(`/api/compare?a=${a}&b=${b}`),
};
