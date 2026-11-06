# Known limitations

Local development comes first. "Deferred" means the item needs an account and waits until deployment
work resumes.

Open gaps, each with the milestone that closes it. An item moves to **Closed** once it's fixed and
tested, along with what closed it.

## Open

| Limitation | Risk until closed | Closes in |
|---|---|---|
| Groq, Gemini and OpenRouter are tested against a local stub, never a live key | A provider quirk could surface on first use, and their default model names are unverified | When a key is added |
| SearXNG depends on upstream engines that can rate-limit or block it, and their terms may restrict automated use | `web_search` can fail or get worse without warning; the agent sees a tool error | Accepted for local use, revisit before deployment |
| The task gate is a text check, so it has gaps and can wrongly refuse | Out-of-scope tasks can get through, though turn and token limits still bound their cost | Accepted |
| Guest and signed-in limits are defined, but nothing uses them yet: no demo task, guest sessions, IP limits or fork expiry | These only matter for the public demo | Deferred |
| A DNS answer can change between the browser's URL check and its request | A page could reach a private address, so the browser container gets no secrets and no route to the database, broker or API. It can reach the gateway, whose tools each require a signed token | Accepted |
| Traces stop at the gateway, with no spans inside the browser service or SearXNG | Slow page fetches show up as one long tool span | Accepted |
| The web app proxies `/api` through a Next.js server and has no sign-in, guest or demo screens | The deployed build needs a static export, a configurable API address and these flows, and its live updates can't use this event stream as is, because `EventSource` can't send a token | Deferred |
| The Next.js proxy can occasionally return a 500 when it reuses a connection the API closed after a live stream | Reads and the live stream recover by themselves, but a write such as creating a branch shows the error and needs another click | Accepted |
| The load test now forks from tool results, and hasn't been rerun since | A mistake in it would show on the next run, and the published numbers used the older message edits | 11 |
| Live updates in the browser tests fall back to the stream's 5-second poll | The tests prove updates arrive, not how quickly | Accepted |
| A small model may answer from a fetched page without ever calling `task_search` | The store is there but goes unused on short tasks. In the live check `qwen3:4b` skipped it even when the task asked for it | Accepted |
| `docker compose --profile ollama up --wait` exits with an error once `ollama-pull` finishes, though the stack is healthy | A script relying on the exit code would stop. CI doesn't use this profile | Accepted |
| Pages fetched before this change have no entries in the store | Older branches can't search their pages, because history isn't rewritten | Accepted |
| Similarity can't tell a changed fact from the same fact | Edits that contradict a source changed the facts in 62% of runs and were flagged as diverged in 7%, so compare marks the changed words too | Accepted |
| The convergence threshold comes from one model on 8 tasks | It may not suit other models, so it's a setting | Accepted |
| The judge hasn't scored the experiment yet | Task success so far comes from deterministic fact checks only | 8 |
| External prices and free-tier limits come mostly from third-party pages | Costs or limits may differ from the plan | Deferred |
| The `deployed` profile has no adapters | Only the local and test builds run | Deferred |

## Closed

| Limitation | Closed by | Milestone |
|---|---|---|
| CI had never run on GitHub | The repo now has a remote. That first run exposed two setup errors, a setup-uv version that doesn't exist and no Python pin, both fixed | 12 |
| Hidden model thinking counts toward the token budget | Raised the branch budget to 40,000 tokens. None of the 136 experiment branches ran out | 8 |
| Short tasks could finish under the 4 to 12 turn target | Tasks ask for each step explicitly. All 8 main runs took 4 to 6 turns | 8 |
| Compare had no semantic score | Added similarity with a threshold calibrated from no-op forks | 8 |
| Concurrency tests only simulated two workers | Real-thread tests against Postgres, which found and fixed a worker and fork deadlock | 3 |
| LLM errors other than rate limits failed the branch straight away | Transient errors retry with backoff, then fail at the attempt limit | 5 |
| Tool results were stored at full size | Results are truncated to a per-tree limit with a visible marker | 5 |
| The local build ran only a mock LLM, mock tools and hash embeddings | Real tools through the gateway, real embeddings, and a local model on the GPU, verified end to end including a real fork | 5 |
| Forks and tree reads didn't check ownership | Every tree, branch and fork route now requires the owner, and others get the same 404 as a missing tree | 6 |
| Per-user branch, daily token and queue limits didn't exist | Admission checks under a per-user database lock, tested with real threads and on the running stack | 6 |
| DuckDuckGo refused automated requests, even from a home IP | `web_search` uses a private SearXNG instance with DuckDuckGo disabled; refusals were not bypassed | 5 |
| A crash between saving a step and queuing the next stranded the branch | Transactional outbox and sweeper, checked by killing a worker and stopping the broker on the real stack | 3 |
