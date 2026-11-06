"use client";

import { type FormEvent, useId, useState } from "react";
import { api } from "@/lib/api";
import type { Branch } from "@/lib/types";

const EXTRA_TURNS = 4;

interface Props {
  branch: Branch;
  turnLimit: number | null;
  onContinued: (branchId: string) => void;
}

export function ContinueForm({ branch, turnLimit, onContinued }: Props) {
  const id = useId();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.continueBranch(branch.id, text);
      setText("");
      onContinued(branch.id);
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="continue-form" onSubmit={submit}>
      <label className="field-label small" htmlFor={id}>
        Your next message
      </label>
      <textarea
        id={id}
        className="field"
        required
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      <p className="quiet small">
        This branch used all {turnLimit ?? branch.turns_used} of its turns. Sending a message gives
        it {EXTRA_TURNS} more.
      </p>
      <div>
        <button className="button button-primary" disabled={busy || !text.trim()} type="submit">
          {busy ? "Continuing branch" : "Continue branch"}
        </button>
      </div>
      {error && (
        <p className="error small" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
