"use client";

import Link from "next/link";
import { type FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Provider } from "@/lib/types";

const NAMES: Record<string, string> = {
  groq: "Groq",
  gemini: "Google Gemini",
  ollama: "Ollama, on this machine",
  mock: "Scripted test model",
};

function KeyRow({ provider, onChange }: { provider: Provider; onChange: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      setValue("");
      onChange();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const save = (event: FormEvent) => {
    event.preventDefault();
    void run(() => api.saveKey(provider.name, value));
  };

  return (
    <div className="key-row">
      <div className="key-head">
        <b>{NAMES[provider.name] ?? provider.name}</b>
        <span className="quiet small">Default model: {provider.default_model}</span>
      </div>
      {!provider.needs_key && <p className="quiet">Ready. This one doesn't need a key.</p>}
      {provider.needs_key && provider.key_last_four && (
        <div className="key-head">
          <span>Key ending in {provider.key_last_four}</span>
          <button
            className="button"
            disabled={busy}
            type="button"
            onClick={() => void run(() => api.deleteKey(provider.name))}
          >
            Remove key
          </button>
        </div>
      )}
      {provider.needs_key && (
        <form className="key-form" onSubmit={save}>
          <input
            className="field"
            type="password"
            autoComplete="off"
            aria-label={`API key for ${NAMES[provider.name] ?? provider.name}`}
            placeholder={provider.key_last_four ? "Paste a new key to replace it" : "Paste your key"}
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
          <button className="button button-primary" disabled={busy || !value.trim()} type="submit">
            Save key
          </button>
        </form>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export default function KeysPage() {
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    api
      .providers()
      .then(setProviders)
      .catch((caught: Error) => setError(caught.message));
  };
  useEffect(load, []);

  return (
    <main className="keys">
      <nav className="home-nav">
        <Link className="wordmark" href="/">
          agent-timelines
        </Link>
      </nav>
      <h1>API keys</h1>
      <p className="quiet">
        Keys are encrypted and stored on this machine. Once saved, only the last four characters are
        ever shown.
      </p>
      {error && <p className="error">{error}</p>}
      {providers?.map((provider) => (
        <KeyRow key={provider.name} provider={provider} onChange={load} />
      ))}
    </main>
  );
}
