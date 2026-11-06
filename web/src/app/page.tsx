import Link from "next/link";
import { Composer } from "@/components/Composer";
import { HeroTrack } from "@/components/HeroTrack";
import { TreeList } from "@/components/TreeList";

export default function HomePage() {
  return (
    <main className="home">
      <nav className="home-nav">
        <Link className="wordmark" href="/">
          agent-timelines
        </Link>
        <Link href="/keys">API keys</Link>
      </nav>
      <div className="home-top">
        <Composer />
        <HeroTrack />
      </div>
      <TreeList />
    </main>
  );
}
