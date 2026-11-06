import { describe, expect, it } from "vitest";
import { errorMessage } from "./api";

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
