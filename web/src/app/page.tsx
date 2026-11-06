import Link from "next/link";
import { Composer } from "@/components/Composer";
import { HeroTrack } from "@/components/HeroTrack";
import { LimitsBar } from "@/components/LimitsBar";
import { TreeList } from "@/components/TreeList";

export default function HomePage() {
  return (
    <main className="home">
      <nav className="home-nav">
        <Link className="wordmark" href="/">
          agent-timelines
        </Link>
        <span className="home-links">
          {/* A plain link, because the report is a static file rather than a page of this app. */}
          <a href="/report.html">Test report</a>
          <Link href="/keys">API keys</Link>
        </span>
      </nav>
      <LimitsBar />
      <div className="home-top">
        <Composer />
        <HeroTrack />
      </div>
      <TreeList />
    </main>
  );
}
