# Known limitations

Local development comes first. "Deferred" means the item needs an account and waits until deployment
work resumes.

Open gaps, each with the milestone that closes it. An item moves to **Closed** once it's fixed and
tested, along with what closed it.

## Open

| Limitation | Risk until closed | Closes in |
|---|---|---|
| The local build runs a mock LLM, mock tools and hash embeddings | Runs show the platform working, not real agent behaviour | 5 |
| LLM errors other than rate limits fail the branch straight away | A brief provider error ends a run | 5 |
| Tool results are stored at full size | Large pages use up the 500 MB database | 5 |
| DuckDuckGo's terms and its behaviour from AWS IPs are unverified | `web_search` may not be usable as designed | 5 (terms), deferred (AWS) |
| Forks don't check who owns a node, and tree access isn't restricted | Any user could fork or read any tree | 6 |
| Per-user branch, daily token and queue depth limits don't exist yet | Only per-branch turn and token limits apply | 6 |
| Compare has no semantic similarity or convergence score | Divergence is only structural | 8 |
| External prices and free-tier limits come mostly from third-party pages | Costs or limits may differ from the plan | Deferred |
| The `deployed` profile has no adapters | Only the local and test builds run | Deferred |
| CI has never run on GitHub because the repo has no remote | A workflow problem could go unnoticed | When a remote is added |

## Closed

| Limitation | Closed by | Milestone |
|---|---|---|
| Concurrency tests only simulated two workers | Real-thread tests against Postgres, which found and fixed a worker and fork deadlock | 3 |
| A crash between saving a step and queuing the next stranded the branch | Transactional outbox and sweeper, checked by killing a worker and stopping the broker on the real stack | 3 |
