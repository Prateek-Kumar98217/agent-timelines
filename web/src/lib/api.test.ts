import { afterEach, describe, expect, it, vi } from "vitest";
import { api, errorMessage } from "./api";

const respond = (status: number, body: unknown = []) =>
  new Response(JSON.stringify(body), { status });

describe("request", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("repeats a read once after a server error", async () => {
    const fetchStub = vi.fn().mockResolvedValueOnce(respond(500)).mockResolvedValueOnce(respond(200));
    vi.stubGlobal("fetch", fetchStub);
    await expect(api.trees()).resolves.toEqual([]);
    expect(fetchStub).toHaveBeenCalledTimes(2);
  });

  it("never repeats a write, so an edit can't create two branches", async () => {
    const fetchStub = vi.fn().mockResolvedValue(respond(500));
    vi.stubGlobal("fetch", fetchStub);
    await expect(api.fork("node", { text: "x" })).rejects.toThrow();
    expect(fetchStub).toHaveBeenCalledTimes(1);
  });
});

describe("errorMessage", () => {
  it("reads both shapes FastAPI uses", () => {
    expect(errorMessage({ detail: "tree not found" }, 404)).toBe("tree not found");
    expect(errorMessage({ detail: [{ msg: "field required" }] }, 422)).toBe("field required");
  });

  it("falls back when the body isn't what we expect", () => {
    expect(errorMessage(null, 502)).toBe("The server returned an error (502).");
    expect(errorMessage({ detail: [] }, 500)).toBe("The server returned an error (500).");
  });
});
