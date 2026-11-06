import { type Page, expect, test } from "@playwright/test";

const LIVE_TIMEOUT = 30_000;

let counter = 0;

async function startTask(page: Page): Promise<string> {
  counter += 1;
  const task = `Check fact number ${counter} about the Apollo programme`;
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
  await stop(page, "Your message, turn 0").click();
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

test("every step shows its type and content without being opened", async ({ page }) => {
  const task = await startTask(page);
  await expect(page.locator(".block-task")).toContainText(task);
  await expect(page.locator(".block-you .tag")).toHaveText("You");
  await expect(page.locator(".block-model .tag").first()).toHaveText("Model");
  await expect(page.locator(".block-call").first()).toContainText("KB search");
  await expect(page.locator(".block-result .tag").first()).toHaveText("Tool result");
  await expect(page.locator(".block-answer")).toContainText("Answer to");
  await expect(page.locator(".block-model").first()).toContainText("tokens");
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
  await stop(page, "Your message, turn 0").click();
  await expect(steps).toHaveClass(/sidebar-open/);
  await page.keyboard.press("Escape");
  await expect(steps).not.toHaveClass(/sidebar-open/);
  await page.getByRole("button", { name: /^Branches \(/ }).click();
  await expect(page.locator(".sidebar-left")).toHaveClass(/sidebar-open/);
  await page.getByRole("button", { name: "Close branches" }).click();
  await expect(page.locator(".sidebar-left")).not.toHaveClass(/sidebar-open/);
});

test("a new step pops up, then its arrow draws from the step before", async ({ page }) => {
  await startTask(page);
  // Steps that were already there when the page opened don't play an entrance.
  await page.reload();
  await page.locator(".block").first().waitFor();
  await expect(page.locator(".block-arriving")).toHaveCount(0);
  await showWholeMap(page);
  await stop(page, "Tool result: KB search, turn 1").click();
  await page.getByRole("textbox", { name: "Result" }).fill("Another result.");
  await page.getByRole("button", { name: "Create branch" }).click();
  const block = page.locator(".block-arriving").first();
  await expect(block).toBeVisible({ timeout: LIVE_TIMEOUT });
  const arrow = page.locator(".arrow-arriving").first();
  await expect(arrow).toBeAttached();
  const timings = await page.evaluate(() => {
    const read = (selector: string) => getComputedStyle(document.querySelector(selector)!);
    return {
      block: read(".block-arriving").animationName,
      line: read(".arrow-arriving .arrow-line").animationName,
      lineDelay: parseFloat(read(".arrow-arriving .arrow-line").animationDelay),
      blockLength: parseFloat(read(".block-arriving").animationDuration),
    };
  });
  expect(timings.block).toBe("block-pop");
  expect(timings.line).toBe("arrow-draw");
  // The arrow can't start until the block has finished popping up.
  expect(timings.lineDelay).toBeGreaterThanOrEqual(timings.blockLength);
});

test("the root can't be edited", async ({ page }) => {
  await startTask(page);
  await stop(page, "Task, turn 0").click();
  await expect(page.getByText("The task can't be edited.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Create branch" })).toHaveCount(0);
});

test("editing your message runs the model on a new branch", async ({ page }) => {
  await startTask(page);
  await stop(page, "Your message, turn 0").click();
  await page.getByRole("textbox", { name: "Message" }).fill("A different question about Hubble");
  const row = await createBranchAndWait(page, "Branch 1");
  await expect(row).toContainText("Edited your message");
  await expect(branchRow(page, "Original run")).toContainText("Finished");
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
  await stop(page, "Your message, turn 0").click();
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
  await stop(page, "Your message, turn 0").click();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});
