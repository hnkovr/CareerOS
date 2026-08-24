import { describe, expect, it } from "vitest";
import { diffLines, formatDate, recommendationLabel, scoreColor, truncate } from "./format";

describe("format helpers", () => {
  it("formats dates and tolerates junk", () => {
    expect(formatDate("2026-08-24")).toMatch(/2026/);
    expect(formatDate(null)).toBe("—");
    expect(formatDate("not-a-date")).toBe("not-a-date");
  });

  it("maps scores to colors", () => {
    expect(scoreColor(85)).toBe("text-good");
    expect(scoreColor(65)).toBe("text-warn");
    expect(scoreColor(10)).toBe("text-bad");
    expect(scoreColor(null)).toBe("text-ink-dim");
  });

  it("labels recommendations", () => {
    expect(recommendationLabel("reply_now")).toBe("REPLY NOW");
    expect(recommendationLabel(null)).toBe("—");
  });

  it("truncates", () => {
    expect(truncate("abc", 5)).toBe("abc");
    expect(truncate("abcdefghij", 5)).toBe("abcd…");
  });

  it("classifies diff lines", () => {
    const lines = diffLines("--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n ctx");
    expect(lines.map((l) => l.kind)).toEqual(["ctx", "ctx", "hunk", "del", "add", "ctx"]);
  });
});
