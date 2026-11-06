/** The product in one picture: a run, an edit, and the second outcome it produced. */
export function HeroTrack() {
  return (
    <svg
      className="hero-track"
      viewBox="0 0 1000 190"
      role="img"
      aria-label="A run drawn as a track. Editing a tool result splits it into a second track with a different answer."
    >
      <path
        className="hero-line"
        pathLength={1}
        d="M 20 50 H 960"
        fill="none"
        stroke="var(--track)"
        strokeWidth="5"
      />
      <path
        className="hero-line hero-fork"
        pathLength={1}
        d="M 400 50 C 470 50, 470 135, 540 135 H 960"
        fill="none"
        stroke="var(--edit)"
        strokeWidth="5"
      />
      <g fill="var(--track)">
        <circle cx="20" cy="50" r="8" />
        <circle cx="210" cy="50" r="8" />
        <rect x="392" y="42" width="16" height="16" />
        <circle cx="640" cy="50" r="8" />
        <rect x="953" y="32" width="7" height="36" />
      </g>
      <text x="20" y="30">Your task</text>
      <text x="210" y="30" textAnchor="middle">The model picks a tool</text>
      <text x="400" y="30" textAnchor="middle">The tool answers</text>
      <text x="940" y="30" textAnchor="end">Everest was climbed in 1953</text>
      <g className="hero-late">
        <rect x="532" y="127" width="16" height="16" fill="var(--edit)" />
        <rect x="953" y="117" width="7" height="36" fill="var(--edit)" />
        <text x="548" y="168" fill="var(--edit)" textAnchor="middle">
          You change the result
        </text>
        <text x="940" y="115" textAnchor="end">Everest was climbed in 1921</text>
      </g>
    </svg>
  );
}
