export interface DiffPart {
  text: string;
  changed: boolean;
}

const MAX_WORDS = 1500;
const tokens = (text: string): string[] => text.match(/\s+|[^\s]+/g) ?? [];

/**
 * Word differences between two answers, as the parts of each. Similarity scores barely move when
 * one date or figure changes, so the compare view marks the words themselves. Very long answers
 * come back unmarked (`null`), because the table below grows with the square of their length.
 */
export function diffWords(a: string, b: string): { a: DiffPart[]; b: DiffPart[] } | null {
  const left = tokens(a);
  const right = tokens(b);
  if (left.length > MAX_WORDS || right.length > MAX_WORDS) return null;

  const width = right.length + 1;
  const table = new Uint16Array((left.length + 1) * width);
  for (let i = left.length - 1; i >= 0; i--) {
    for (let j = right.length - 1; j >= 0; j--) {
      table[i * width + j] =
        left[i] === right[j]
          ? table[(i + 1) * width + j + 1]! + 1
          : Math.max(table[(i + 1) * width + j]!, table[i * width + j + 1]!);
    }
  }

  const partsA: DiffPart[] = [];
  const partsB: DiffPart[] = [];
  const add = (parts: DiffPart[], text: string, changed: boolean) => {
    const last = parts[parts.length - 1];
    if (last && last.changed === changed) last.text += text;
    else parts.push({ text, changed });
  };
  let i = 0;
  let j = 0;
  while (i < left.length || j < right.length) {
    if (i < left.length && j < right.length && left[i] === right[j]) {
      add(partsA, left[i]!, false);
      add(partsB, right[j]!, false);
      i++;
      j++;
    } else if (
      j >= right.length ||
      (i < left.length && table[(i + 1) * width + j]! >= table[i * width + j + 1]!)
    ) {
      add(partsA, left[i]!, true);
      i++;
    } else {
      add(partsB, right[j]!, true);
      j++;
    }
  }
  return { a: partsA, b: partsB };
}
