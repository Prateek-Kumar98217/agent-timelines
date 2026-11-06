"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { Workspace } from "@/components/Workspace";

function TreePage() {
  const id = useSearchParams().get("id");
  if (!id) {
    return (
      <main className="centered-state">
        <h1>No task selected</h1>
        <Link className="button" href="/">
          Back to your tasks
        </Link>
      </main>
    );
  }
  return <Workspace key={id} id={id} />;
}

export default function Page() {
  return (
    <Suspense>
      <TreePage />
    </Suspense>
  );
}
