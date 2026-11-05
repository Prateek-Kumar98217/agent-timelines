# Known limitations

Local development comes first. "Deferred" means the item needs an account and waits until deployment
work resumes.

Open gaps, each with the milestone that closes it. An item moves to **Closed** once it's fixed and
tested, along with what closed it.

## Open

| Limitation | Risk until closed | Closes in |
|---|---|---|
| The local build runs a mock LLM, mock tools and hash embeddings | Runs show the platform working, not real agent behaviour | 5 |
| SearXNG depends on upstream engines that can rate-limit or block it, and their terms may restrict automated use | `web_search` can fail or get worse without warning; the agent sees a tool error | Accepted for local use, revisit before deployment |
| Forks don't check who owns a node, and tree access isn't restricted | Any user could fork or read any tree | 6 |
| Per-user branch, daily token and queue depth limits don't exist yet | Only per-branch turn and token limits apply | 6 |
| A DNS answer can change between the browser's URL check and its request | A page could reach a private address, so the browser container gets no secrets and no route to the database, broker or API. It can reach the gateway, whose tools each require a signed token | Accepted |
| Compare has no semantic similarity or convergence score | Divergence is only structural | 8 |
| External prices and free-tier limits come mostly from third-party pages | Costs or limits may differ from the plan | Deferred |
| The `deployed` profile has no adapters | Only the local and test builds run | Deferred |
| CI has never run on GitHub because the repo has no remote | A workflow problem could go unnoticed | When a remote is added |

## Closed

| Limitation | Closed by | Milestone |
|---|---|---|
| Concurrency tests only simulated two workers | Real-thread tests against Postgres, which found and fixed a worker and fork deadlock | 3 |
| LLM errors other than rate limits failed the branch straight away | Transient errors retry with backoff, then fail at the attempt limit | 5 |
| Tool results were stored at full size | Results are truncated to a per-tree limit with a visible marker | 5 |
| DuckDuckGo refused automated requests, even from a home IP | `web_search` uses a private SearXNG instance with DuckDuckGo disabled; refusals were not bypassed | 5 |
| A crash between saving a step and queuing the next stranded the branch | Transactional outbox and sweeper, checked by killing a worker and stopping the broker on the real stack | 3 |
