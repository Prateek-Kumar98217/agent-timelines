"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { Provider } from "@/lib/types";

const MAX_TASK_CHARS = 2000;

export function Composer() {
  const router = useRouter();
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [task, setTask] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .providers()
      .then((all) => setProviders(all.filter((option) => option.available)))
      .catch((caught: Error) => {
        setProviders([]);
        setError(caught.message);
      });
  }, []);

  const chosen = providers?.find((option) => option.name === provider) ?? providers?.[0];

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.createTree(task, chosen?.name ?? null, model.trim() || null);
      router.push(`/tree?id=${created.tree_id}`);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The task didn't start.");
      setBusy(false);
    }
  }

  const noProvider = providers !== null && providers.length === 0;

  return (
    <form className="composer" onSubmit={submit}>
      <label htmlFor="task" className="composer-title">
        What should the agent work out?
      </label>
      <textarea
        id="task"
        className="field"
        required
        maxLength={MAX_TASK_CHARS}
        value={task}
        onChange={(event) => setTask(event.target.value)}
        placeholder="How many years passed between the Apollo 11 landing and the launch of the Hubble Space Telescope?"
      />
      <p className="quiet small">
        Short research, fact checking and planning tasks work best. The agent can search the web and
        a knowledge base, open pages, calculate and keep notes.
      </p>
      {noProvider ? (
        <p className="error">
          No model is available yet. <Link href="/keys">Add an API key</Link> or start the stack with
          Ollama.
        </p>
      ) : (
        <div className="composer-row">
          <label>
            <span className="field-label">Provider</span>
            <select
              className="field"
              value={chosen?.name ?? ""}
              onChange={(event) => setProvider(event.target.value)}
            >
              {providers?.map((option) => (
                <option key={option.name} value={option.name}>
                  {option.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="field-label">Model</span>
            <input
              className="field"
              value={model}
              maxLength={120}
              onChange={(event) => setModel(event.target.value)}
              placeholder={chosen?.default_model ?? ""}
            />
          </label>
          <button
            className="button button-primary"
            disabled={busy || !task.trim() || !chosen}
            type="submit"
          >
            {busy ? "Starting task" : "Start task"}
          </button>
        </div>
      )}
      {error && !noProvider && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
