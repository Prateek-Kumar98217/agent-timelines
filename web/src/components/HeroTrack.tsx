function Step({
  x = 20,
  y,
  tag,
  color,
  text,
  answer = false,
}: {
  x?: number;
  y: number;
  tag: string;
  color: string;
  text: string;
  answer?: boolean;
}) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <rect
        width="210"
        height="64"
        rx="4"
        fill={answer ? "var(--text)" : "var(--surface)"}
        stroke={answer ? "var(--text)" : "var(--line)"}
        strokeWidth="1.5"
      />
      <rect x="10" y="9" width={tag.length * 7 + 16} height="20" rx="10" fill={color} />
      <text x="18" y="24" fill={answer ? "var(--text)" : "#000"} fontWeight="700" fontSize="13">
        {tag}
      </text>
      <text x="12" y="51" fill={answer ? "#000" : "var(--text)"} fontSize="14">
        {text}
      </text>
    </g>
  );
}

/** The product in one picture: a run, an edit, and the second outcome it produced. */
export function HeroTrack() {
  return (
    <svg
      className="hero-track"
      viewBox="0 0 490 440"
      role="img"
      aria-label="A run drawn from top to bottom. Editing a tool result starts a second branch with a different answer."
    >
      <path
        d="M 125 74 V 104 M 125 168 V 198 M 125 262 V 356"
        fill="none"
        stroke="var(--text)"
        strokeWidth="2"
      />
      <path d="M 119 96 L 125 105 L 131 96 Z M 119 190 L 125 199 L 131 190 Z M 119 348 L 125 357 L 131 348 Z" fill="var(--text)" />
      <path
        className="hero-fork"
        pathLength={1}
        d="M 125 300 H 365 V 350"
        fill="none"
        stroke="var(--orange)"
        strokeWidth="2.5"
      />
      <Step y={10} tag="Task" color="var(--task)" text="When was Everest climbed?" />
      <Step y={104} tag="Tool call" color="var(--call)" text="web search: Everest" />
      <Step y={198} tag="Tool result" color="var(--result)" text="First climbed in 1953" />
      <Step y={356} tag="Answer" color="#000" text="It was first climbed in 1953" answer />
      <g className="hero-late">
        <path d="M 359 348 L 365 357 L 371 348 Z" fill="var(--orange)" />
        <Step x={260} y={356} tag="Answer" color="#000" text="It was first climbed in 1921" answer />
        <text x="375" y="288" fill="var(--orange)" textAnchor="middle" fontSize="14" fontWeight="700">
          You change the result
        </text>
      </g>
    </svg>
  );
}
