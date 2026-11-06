"use client";

import { type FormEvent, useId, useState } from "react";
import { api } from "@/lib/api";
import type { Branch } from "@/lib/types";

const EXTRA_TURNS = 4;

interface Props {
  branch: Branch;
  turnLimit: number | null;
  /** Starts as one button, for lists where every row could show this form. */
  collapsed?: boolean;
  onContinued: (branchId: string) => void;
}

function turnsMessage(branch: Branch, turnLimit: number | null): string {
  const left = turnLimit === null ? 0 : turnLimit - branch.turns_used;
  if (left > 0) return left === 1 ? "This branch has 1 turn left." : `This branch has ${left} turns left.`;
  return `This branch has used all its turns. Sending a message gives it ${EXTRA_TURNS} more.`;
}

export function ContinueForm({ branch, turnLimit, collapsed = false, onContinued }: Props) {
  const id = useId();
  const [open, setOpen] = useState(!collapsed);
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
      setOpen(!collapsed);
      onContinued(branch.id);
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button className="link-button small" type="button" onClick={() => setOpen(true)}>
        Continue this branch
      </button>
    );
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
      <p className="quiet small">{turnsMessage(branch, turnLimit)}</p>
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
