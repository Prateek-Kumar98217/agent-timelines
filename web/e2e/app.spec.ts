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
  return task;
}

const stop = (page: Page, name: string) => page.getByRole("button", { name }).first();

async function createBranchAndWait(page: Page, branchName: string) {
  await page.getByRole("button", { name: "Create branch" }).click();
  const row = page.getByRole("row", { name: new RegExp(branchName) });
  await expect(row).toBeVisible({ timeout: LIVE_TIMEOUT });
  await expect(row.getByRole("cell", { name: "Finished", exact: true })).toBeVisible({
    timeout: LIVE_TIMEOUT,
  });
  return row;
}

test("a task runs live and appears in the task list", async ({ page }) => {
  const task = await startTask(page);
  await expect(page.getByRole("row", { name: /Original run/ })).toContainText("Finished");
  await page.getByRole("link", { name: "agent-timelines" }).click();
  await page.getByLabel(/Search \d+ task/).fill(task);
  await expect(page.getByRole("link", { name: new RegExp(task) })).toBeVisible();
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
  await expect(page.getByRole("row", { name: /Original run/ })).toContainText("Finished");
});

test("a final answer edit closes the branch without running anything", async ({ page }) => {
  await startTask(page);
  await stop(page, "Model's decision, turn 1").click();
  await page.getByLabel("Give a final answer").check();
  await page.getByRole("textbox", { name: "Answer" }).fill("The answer is 42.");
  const row = await createBranchAndWait(page, "Branch 1");
  await expect(row.getByRole("cell", { name: "0", exact: true }).first()).toBeVisible();
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
  const before = await page.locator(".inspector-body").innerText();
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
  await expect(page.getByRole("row", { name: /Branch 1/ })).toHaveCount(0);
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
  await page.getByRole("button", { name: "Back to the map" }).click();
  await expect(page.locator(".map")).toBeVisible();
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
