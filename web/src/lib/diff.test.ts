import { describe, expect, it } from "vitest";
import { diffWords } from "./diff";

const changed = (parts: { text: string; changed: boolean }[]) =>
  parts.filter((part) => part.changed).map((part) => part.text);

describe("diffWords", () => {
  it("marks only the word that changed", () => {
    const result = diffWords("Fermi won in 1938.", "Fermi won in 1939.")!;
    expect(changed(result.a)).toEqual(["1938."]);
    expect(changed(result.b)).toEqual(["1939."]);
  });

  it("marks nothing for identical answers and rebuilds both texts", () => {
    const result = diffWords("same  text\nhere", "same  text\nhere")!;
    expect(changed(result.a)).toEqual([]);
    expect(result.a.map((part) => part.text).join("")).toBe("same  text\nhere");
  });

  it("handles additions, removals and empty text", () => {
    const result = diffWords("", "new words")!;
    expect(result.a).toEqual([]);
    expect(result.b.map((part) => part.text).join("")).toBe("new words");
  });

  it("skips very long answers rather than blocking the page", () => {
    expect(diffWords("word ".repeat(2000), "other ".repeat(2000))).toBeNull();
  });
});
