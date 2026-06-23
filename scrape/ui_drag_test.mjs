import { chromium } from "playwright";

const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1500, height: 950 }, deviceScaleFactor: 2 });
await page.addInitScript(() => localStorage.clear());
await page.goto("http://127.0.0.1:8000/", { waitUntil: "networkidle" });
await page.waitForSelector("text=Majors");

// pick the NON-honors Machine Intelligence major
const majorCard = page.locator(".picker-card").nth(0);
await majorCard.locator(".course-input.wide").fill("Machine Intelligence, BS");
await page.waitForTimeout(700);
const rows = majorCard.locator(".prog-row");
const n = await rows.count();
for (let i = 0; i < n; i++) {
  const t = await rows.nth(i).innerText();
  if (t.includes("Machine Intelligence") && !t.includes("Honors")) { await rows.nth(i).click(); break; }
}
await page.locator("button.primary-btn").click();
await page.waitForSelector(".board .term-col");
await page.waitForTimeout(2500);

// zoomed board screenshot (lines clearly visible)
const initialBad = await page.locator(".course-card.bad").count();
console.log(`initial flagged cards (should be 0): ${initialBad}`);
await page.locator(".board").screenshot({ path: "/tmp/ui_board_zoom.png" });

// Drag CS 25100 into the FIRST term (Fall 2026) — before its prereqs -> should scream
const src = page.locator('[data-code="CS 25100"]').first();
if (await src.count()) {
  const b = await src.boundingBox();
  const dst = await page.locator(".term-col").first().boundingBox();
  await page.mouse.move(b.x + b.width / 2, b.y + 14);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2 + 12, b.y + 26, { steps: 5 });
  await page.mouse.move(dst.x + dst.width / 2, dst.y + 80, { steps: 12 });
  await page.mouse.up();
  await page.waitForTimeout(1800);
  const badCards = await page.locator(".course-card.bad").count();
  const violatedLines = await page.locator(".dep-svg path.edge.violated").count();
  console.log(`after illegal move: ${badCards} flagged card(s), ${violatedLines} violated line(s)`);
  await page.locator(".board").screenshot({ path: "/tmp/ui_violation.png" });
} else {
  console.log("CS 25100 not found in plan");
}
await browser.close();
