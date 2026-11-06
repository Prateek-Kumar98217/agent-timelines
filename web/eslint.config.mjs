import next from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

const config = [
  { ignores: [".next/**", "node_modules/**", "test-results/**", "playwright-report/**"] },
  ...next,
  ...typescript,
  // Apostrophes in sentences are safe in JSX, and escaping them makes the copy harder to read.
  { rules: { "react/no-unescaped-entities": "off" } },
];

export default config;
