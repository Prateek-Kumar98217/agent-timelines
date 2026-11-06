import { type Page, expect, test } from "@playwright/test";

const LIVE_TIMEOUT = 30_000;

let counter = 0;

async function startTask(page: Page, marker = ""): Promise<string> {
  counter += 1;
  const task = `Check fact number ${counter} about the Apollo programme ${marker}`.trim();
  await page.goto("/");
  await page.getByLabel("What should the agent work out?").fill(task);
  await expect(page.getByLabel("Provider")).toHaveValue(/.+/);
  await page.getByLabel("Provider").selectOption("mock");
  await page.getByRole("button", { name: "Start task" }).click();
  await expect(page).toHaveURL(/\/tree\?id=/);
  await expect(page.getByRole("heading", { name: task })).toBeVisible();
  await expect(page.getByRole("status")).toHaveText("All branches finished", {
    timeout: LIVE_TIMEOUT,
  });
  await showWholeMap(page);
  return task;
}

/** The map follows a live run to its end, so show the whole tree before picking early steps. */
async function showWholeMap(page: Page) {
  await page.locator(".react-flow__controls-fitview").click();
  await page.waitForTimeout(400);
}

const stop = (page: Page, name: string) => page.getByRole("button", { name }).first();

const branchRow = (page: Page, name: string) =>
  page.locator(".branch-row").filter({ hasText: name });

async function openBranches(page: Page) {
  const toggle = page.getByRole("button", { name: /^Branches \(/ });
  if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
}

async function createBranchAndWait(page: Page, branchName: string) {
  await page.getByRole("button", { name: "Create branch" }).click();
  await openBranches(page);
  const row = branchRow(page, branchName);
  await expect(row).toContainText("Finished", { timeout: LIVE_TIMEOUT });
  return row;
}

test("a task runs live and appears in the task list", async ({ page }) => {
  const task = await startTask(page);
  await openBranches(page);
  await expect(branchRow(page, "Original run")).toContainText("Finished");
  await page.getByRole("link", { name: "agent-timelines" }).click();
  await page.getByLabel(/Search \d+ task/).fill(task);
  await expect(page.getByRole("link", { name: new RegExp(task) })).toBeVisible();
});

test("a sidebar can't drag the page sideways", async ({ page }) => {
  await startTask(page);
  await stop(page, "Tool call: KB search, turn 1").click();
  await page.getByRole("button", { name: "Create branch" }).focus();
  await page.waitForTimeout(400);
  const stage = await page.locator(".stage").evaluate((element) => element.scrollLeft);
  expect(stage).toBe(0);
  await expect(page.getByRole("link", { name: "agent-timelines" })).toBeInViewport();
});

test("live updates recover after the stream fails with a server error", async ({ page }) => {
  let failures = 0;
  await page.route("**/stream", (route) => {
    if (failures === 0) {
      failures += 1;
      return route.fulfill({ status: 500, body: "" });
    }
    return route.continue();
  });
  const task = await startTask(page);
  expect(failures).toBe(1);
  await expect(page.getByRole("heading", { name: task })).toBeVisible();
  await expect(page.getByRole("status")).not.toContainText("reconnecting");
});

test("limits show on the home page and the map, for this user's role", async ({ page }) => {
  await page.goto("/");
  const home = page.locator(".limits");
  await expect(home).toContainText("Local user");
  await expect(home).toContainText(/Active branches\s*\d+ of 5/);
  await expect(home).toContainText("Up to 12 turns per path");
  await startTask(page);
  const bar = page.locator(".limits");
  await expect(bar).toContainText(/Active branches\s*0 of 5/);
  await expect(bar).toContainText(/Original run\s*\d+ of 12 turns/);
  await expect(bar.locator(".limit").first().locator(".pip")).toHaveCount(5);
  await expect(bar.locator(".limit").nth(1).locator(".pip")).toHaveCount(12);
});

test("compare counts the tokens of the whole path", async ({ page }) => {
  await startTask(page);
  await stop(page, "Tool result: KB search, turn 1").click();
  await page.getByRole("textbox", { name: "Result" }).fill("A changed result.");
  await createBranchAndWait(page, "Branch 1");
  await page.getByLabel("Compare Original run").check();
  await page.getByLabel("Compare Branch 1", { exact: true }).check();
  await page.getByRole("button", { name: "Compare branches" }).click();
  const row = page.getByRole("row", { name: /Tokens, whole path/ });
  await expect(row).toBeVisible();
  const id = new URL(page.url()).searchParams.get("id");
  const tree = await (await page.request.get(`/api/trees/${id}`)).json();
  type Step = { id: string; parent_id: string | null; branch_id: string; seq: number; input_tokens: number; output_tokens: number };
  const fork = tree.branches.find((branch: { parent_branch_id: string | null }) => branch.parent_branch_id);
  const own: Step[] = tree.nodes.filter((step: Step) => step.branch_id === fork.id);
  let step: Step | undefined = own.reduce((latest, next) => (next.seq > latest.seq ? next : latest));
  let total = 0;
  while (step) {
    total += step.input_tokens + step.output_tokens;
    const parent: string | null = step.parent_id;
    step = tree.nodes.find((candidate: Step) => candidate.id === parent);
  }
  // A fork's own counter would leave out the steps before the split, so this must be larger.
  expect(total).toBeGreaterThan(fork.input_tokens + fork.output_tokens);
  await expect(row.getByRole("cell").nth(1)).toHaveText(total.toLocaleString());
});

test("a branch at its turn limit continues with a message and 4 more turns", async ({ page }) => {
  await startTask(page, "[run long]");
  const bar = page.locator(".limits");
  await expect(bar).toContainText("12 of 12 turns");
  await openBranches(page);
  const row = branchRow(page, "Original run");
  await row.getByRole("textbox", { name: "Your next message" }).fill("Check one more source");
  await row.getByRole("button", { name: "Continue branch" }).click();

  await expect(bar).toContainText("of 16 turns", { timeout: LIVE_TIMEOUT });
  await expect(page.locator(".block-you")).toHaveCount(1, { timeout: LIVE_TIMEOUT });
  await expect(page.locator(".block-you")).toContainText("Check one more source");
  // It reaches the new limit, and the same choice is there again.
  await expect(bar).toContainText("16 of 16 turns", { timeout: LIVE_TIMEOUT });
  await expect(row.getByRole("button", { name: "Continue branch" })).toBeVisible({
    timeout: LIVE_TIMEOUT,
  });
});

test("a follow-up message can be edited into a new branch", async ({ page }) => {
  await startTask(page, "[run long]");
  await openBranches(page);
  const row = branchRow(page, "Original run");
  await row.getByRole("textbox", { name: "Your next message" }).fill("Check one more source");
  await row.getByRole("button", { name: "Continue branch" }).click();
  await expect(page.locator(".block-you")).toHaveCount(1, { timeout: LIVE_TIMEOUT });
  await page.getByRole("button", { name: "Close branches" }).click();
  await showWholeMap(page);
  await page.locator(".block-you").click();
  await page.getByRole("textbox", { name: "Message" }).fill("Check a different source");
  await page.getByRole("button", { name: "Create branch" }).click();
  await expect(page.locator(".block-you")).toHaveCount(2, { timeout: LIVE_TIMEOUT });
});

test("a branch that finished normally can't be continued", async ({ page }) => {
  await startTask(page);
  await openBranches(page);
  await expect(branchRow(page, "Original run")).toContainText("Finished");
  await expect(page.getByRole("button", { name: "Continue branch" })).toHaveCount(0);
});

test("every step shows its type and content without being opened", async ({ page }) => {
  const task = await startTask(page);
  await expect(page.locator(".block-task")).toContainText(task);
  await expect(page.locator(".block-model .tag").first()).toHaveText("Model");
  await expect(page.locator(".block-call").first()).toContainText("KB search");
  await expect(page.locator(".block-result .tag").first()).toHaveText("Tool result");
  await expect(page.locator(".block-answer")).toContainText("Answer to");
  await expect(page.locator(".block-model").first()).toContainText(/\d tokens/);
  await expect(page.locator(".block-answer")).toContainText(/\d tokens/);
  await expect(page.locator(".block-call").first()).not.toContainText("tokens");
  await expect(page.locator(".block-result").first()).not.toContainText("tokens");
});

test("the timeline runs from top to bottom, and forks step to the right", async ({ page }) => {
  await startTask(page);
  await stop(page, "Tool result: KB search, turn 1").click();
  const parent = await stop(page, "Tool call: KB search, turn 1").boundingBox();
  const child = await stop(page, "Tool result: KB search, turn 1").boundingBox();
  expect(child!.y).toBeGreaterThan(parent!.y + parent!.height);
  expect(Math.abs(child!.x - parent!.x)).toBeLessThan(1);
  await page.getByRole("textbox", { name: "Result" }).fill("Changed result.");
  await page.getByRole("button", { name: "Create branch" }).click();
  const fork = page.getByRole("button", { name: "Tool result: KB search, turn 1, edited" });
  await expect(fork).toBeVisible({ timeout: LIVE_TIMEOUT });
  // The view may pan to the new step, so measure both together.
  const original = stop(page, "Tool result: KB search, turn 1");
  await page.waitForTimeout(600);
  const forkBox = (await fork.boundingBox())!;
  expect(forkBox.x).toBeGreaterThan((await original.boundingBox())!.x);
  // It must not land under the open sidebar, where its entrance would go unseen.
  const sidebar = (await page.locator(".sidebar-right").boundingBox())!;
  expect(forkBox.x + forkBox.width).toBeLessThanOrEqual(sidebar.x);
});

test("sidebars slide in on request and close with Escape", async ({ page }) => {
  await startTask(page);
  const steps = page.locator(".sidebar-right");
  await expect(steps).not.toHaveClass(/sidebar-open/);
  await stop(page, "Task, turn 0").click();
  await expect(steps).toHaveClass(/sidebar-open/);
  await page.keyboard.press("Escape");
  await expect(steps).not.toHaveClass(/sidebar-open/);
  await page.getByRole("button", { name: /^Branches \(/ }).click();
  await expect(page.locator(".sidebar-left")).toHaveClass(/sidebar-open/);
  await page.getByRole("button", { name: "Close branches" }).click();
  await expect(page.locator(".sidebar-left")).not.toHaveClass(/sidebar-open/);
});

/** Records every entrance as it's created, so a replay can't slip through unnoticed. */
async function recordEntrances(page: Page) {
  await page.addInitScript(() => {
    const entrances: { id: string; of: string; delay: number }[] = [];
    (window as unknown as { entrances: typeof entrances }).entrances = entrances;
    const animate = Element.prototype.animate;
    Element.prototype.animate = function (keyframes, options) {
      const animation = animate.call(this, keyframes, options);
      if (typeof options === "object" && options.id) {
        const owner = this.closest("[data-arrow]")?.getAttribute("data-arrow");
        entrances.push({
          id: options.id,
          of: owner ?? this.getAttribute("data-step") ?? "",
          delay: Number(options.delay ?? 0),
        });
      }
      return animation;
    };
  });
}

const entrances = (page: Page) =>
  page.evaluate(() => (window as unknown as { entrances: { id: string; of: string; delay: number }[] }).entrances);

const distinctTargets = async (page: Page, id: string) =>
  new Set((await entrances(page)).filter((entry) => entry.id === id).map((entry) => entry.of));

/** Which entrances are playing or waiting right now. */
const playing = (page: Page) =>
  page.evaluate(() => document.getAnimations().map((animation) => animation.id).filter(Boolean));

async function forkAnswerAndSettle(page: Page, text: string) {
  await stop(page, "Model's decision, turn 1").click();
  await page.getByLabel("Give a final answer").check();
  await page.getByRole("textbox", { name: "Answer" }).fill(text);
  await page.getByRole("button", { name: "Create branch" }).click();
  await expect(page.getByRole("button", { name: "Final answer, turn 1, edited" })).toBeVisible({
    timeout: LIVE_TIMEOUT,
  });
  await page.waitForTimeout(1200);
}

test("steps already on the page when it opens play no entrance", async ({ page }) => {
  await recordEntrances(page);
  await startTask(page);
  await page.reload();
  await page.locator(".block").first().waitFor();
  await page.waitForTimeout(800);
  expect(await entrances(page)).toEqual([]);
});

test("a new step pops up, then one arrow draws to it from the step before", async ({ page }) => {
  await startTask(page);
  await recordEntrances(page);
  await page.reload();
  await page.locator(".block").first().waitFor();
  await showWholeMap(page);
  await forkAnswerAndSettle(page, "A new answer.");

  // Only one step is new, so only its block and its one arrow play.
  const newStep = await page
    .getByRole("button", { name: "Final answer, turn 1, edited" })
    .getAttribute("data-step");
  expect([...(await distinctTargets(page, "block-pop"))]).toEqual([newStep]);
  const arrows = [...(await distinctTargets(page, "arrow-draw"))];
  expect(arrows).toHaveLength(1);
  expect(arrows[0]!.endsWith(`-${newStep}`)).toBe(true);

  // The arrow waits for the block to finish popping up, and the head lands last.
  const delay = (id: string) =>
    entrances(page).then((all) => all.find((entry) => entry.id === id)!.delay);
  expect(await delay("arrow-draw")).toBeGreaterThanOrEqual(320);
  expect(await delay("arrow-head")).toBeGreaterThan(await delay("arrow-draw"));
});

test("the fit button brings the whole tree into view", async ({ page }) => {
  await startTask(page);
  await expect(page.locator(".block-task")).toBeInViewport();
  await expect(page.locator(".block-answer")).toBeInViewport();
});

test("every step has its arrow, and clicks don't rebuild them", async ({ page }) => {
  await startTask(page);
  await page.reload();
  await page.locator(".block").first().waitFor();
  const blocks = await page.locator(".block").count();
  await expect(page.locator(".arrow")).toHaveCount(blocks - 1);
  await page.evaluate(() => {
    const state = window as unknown as { arrows: Element[] };
    state.arrows = [...document.querySelectorAll(".arrow")];
  });
  await showWholeMap(page);
  await stop(page, "Tool call: KB search, turn 1").click();
  await page.locator(".terminus").first().click();
  await page.waitForTimeout(300);
  const kept = await page.evaluate(() =>
    (window as unknown as { arrows: Element[] }).arrows.every((arrow) => arrow.isConnected),
  );
  expect(kept).toBe(true);
});

test("clicking steps and branches never replays an entrance", async ({ page }) => {
  await startTask(page);
  await recordEntrances(page);
  await page.reload();
  await page.locator(".block").first().waitFor();
  await showWholeMap(page);
  await forkAnswerAndSettle(page, "Another new answer.");
  const before = (await entrances(page)).length;

  await stop(page, "Task, turn 0").click();
  expect(await playing(page)).toEqual([]);
  await stop(page, "Tool call: KB search, turn 1").click();
  await page.locator(".terminus").first().click();
  await page.waitForTimeout(300);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);

  expect(await playing(page)).toEqual([]);
  expect((await entrances(page)).length).toBe(before);
});

test("a second new step plays only its own entrance", async ({ page }) => {
  await startTask(page);
  await recordEntrances(page);
  await page.reload();
  await page.locator(".block").first().waitFor();
  await showWholeMap(page);
  await forkAnswerAndSettle(page, "First new answer.");
  const first = await distinctTargets(page, "arrow-draw");
  await page.evaluate(() => {
    (window as unknown as { entrances: unknown[] }).entrances.length = 0;
  });

  await stop(page, "Model's decision, turn 2").click();
  await page.getByLabel("Give a final answer").check();
  await page.getByRole("textbox", { name: "Answer" }).fill("Second new answer.");
  await page.getByRole("button", { name: "Create branch" }).click();
  await expect(page.locator(".block-edited")).toHaveCount(2, { timeout: LIVE_TIMEOUT });
  await page.waitForTimeout(1200);

  const second = [...(await distinctTargets(page, "arrow-draw"))];
  expect(second).toHaveLength(1);
  expect(first.has(second[0]!)).toBe(false);
});

test("the root can't be edited", async ({ page }) => {
  await startTask(page);
  await stop(page, "Task, turn 0").click();
  await expect(page.getByText("The task can't be changed.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Create branch" })).toHaveCount(0);
});

test("the task appears once and can't be changed, even through the API", async ({ page }) => {
  await startTask(page);
  await expect(page.locator(".block-task")).toHaveCount(1);
  await expect(page.locator(".block-you")).toHaveCount(0);
  const id = new URL(page.url()).searchParams.get("id");
  const tree = await (await page.request.get(`/api/trees/${id}`)).json();
  const message = tree.nodes.find((node: { type: string }) => node.type === "user_msg");
  const response = await page.request.post(`/api/nodes/${message.id}/fork`, {
    data: { edit: { text: "A different task" } },
  });
  expect(response.status()).toBe(409);
});

test("a final answer edit closes the branch without running anything", async ({ page }) => {
  await startTask(page);
  await stop(page, "Model's decision, turn 1").click();
  await page.getByLabel("Give a final answer").check();
  await page.getByRole("textbox", { name: "Answer" }).fill("The answer is 42.");
  const row = await createBranchAndWait(page, "Branch 1");
  await expect(row).toContainText("0 tokens");
  await expect(page.getByRole("heading", { name: "Final answer" })).toBeVisible();
});

test("editing a tool call runs the tool again", async ({ page }) => {
  await startTask(page);
  await stop(page, "Tool call: KB search, turn 1").click();
  await page.getByRole("textbox", { name: "query" }).fill("a better search");
  await createBranchAndWait(page, "Branch 1");
  await expect(page.getByRole("heading", { name: "Tool call: KB search" })).toBeVisible();
  await expect(page.locator(".inspector-body")).toContainText("a better search");
});

test("editing a tool result continues from it, and the original stays", async ({ page }) => {
  await startTask(page);
  const original = stop(page, "Tool result: KB search, turn 1");
  await original.click();
  const before = (await page.locator(".inspector-body").textContent()) ?? "";
  expect(before).not.toBe("");
  await page.getByRole("textbox", { name: "Result" }).fill("Apollo 11 landed in 1970.");
  await page.getByLabel("Report this to the model as a tool error").check();
  await createBranchAndWait(page, "Branch 1");
  await expect(page.locator(".inspector-body")).toContainText("Apollo 11 landed in 1970.");
  await page.getByRole("button", { name: "Show the original step" }).click();
  await expect(page.locator(".inspector-body")).toHaveText(before);
});

test("an edit that changes nothing is refused with a reason", async ({ page }) => {
  await startTask(page);
  await stop(page, "Tool call: KB search, turn 1").click();
  await page.getByRole("button", { name: "Create branch" }).click();
  await expect(page.locator(".edit-form .error")).toHaveText("the edit doesn't change the node");
  await openBranches(page);
  await expect(branchRow(page, "Branch 1")).toHaveCount(0);
});

test("compare marks the words that differ", async ({ page }) => {
  await startTask(page);
  await stop(page, "Model's decision, turn 1").click();
  await page.getByLabel("Give a final answer").check();
  await page.getByRole("textbox", { name: "Answer" }).fill("Totally different words.");
  await createBranchAndWait(page, "Branch 1");
  await expect(page.getByRole("button", { name: "Compare branches" })).toBeDisabled();
  await page.getByLabel("Compare Original run").check();
  await page.getByLabel("Compare Branch 1", { exact: true }).check();
  await page.getByRole("button", { name: "Compare branches" }).click();
  await expect(page.getByRole("heading", { name: "Original run and Branch 1" })).toBeVisible();
  await expect(page.locator("mark.diff-b")).toHaveText(["Totally", "different", "words."]);
  await expect(page.getByRole("rowheader", { name: "Tokens" })).toBeVisible();
  await page.getByRole("button", { name: "Close comparison" }).click();
  await expect(page.locator(".compare")).toHaveCount(0);
});

test("page text renders as text, never as markup", async ({ page }) => {
  await startTask(page);
  await stop(page, "Tool result: KB search, turn 1").click();
  const markup = '<img src=x onerror="window.__injected=true">';
  await page.getByRole("textbox", { name: "Result" }).fill(markup);
  await createBranchAndWait(page, "Branch 1");
  await expect(page.locator(".inspector-body")).toContainText(markup);
  expect(await page.evaluate(() => "__injected" in window)).toBe(false);
});

test("an API key is saved, masked and removed", async ({ page }) => {
  await page.goto("/keys");
  const input = page.getByLabel("API key for Groq");
  await input.fill("gsk_test_key_1234");
  await expect(input).toHaveAttribute("type", "password");
  await page.getByRole("button", { name: "Save key" }).first().click();
  await expect(page.getByText("Key ending in 1234")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("gsk_test");
  await page.getByRole("button", { name: "Remove key" }).click();
  await expect(page.getByText("Key ending in 1234")).toHaveCount(0);
});

test("an unknown task shows where to go next", async ({ page }) => {
  await page.goto("/tree?id=00000000-0000-0000-0000-000000000000");
  await expect(page.getByRole("heading", { name: "This task can't be opened" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to your tasks" })).toBeVisible();
});

test("the workspace has no sideways scroll at phone width", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await startTask(page);
  await stop(page, "Task, turn 0").click();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});
