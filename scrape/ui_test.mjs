import { chromium } from "playwright";

const BASE = process.argv[2] || "http://127.0.0.1:8000/";
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", (e) => errors.push("PAGEERROR: " + e.message));

await page.goto(BASE, { waitUntil: "networkidle", timeout: 30000 });
await page.waitForSelector("text=Majors", { timeout: 10000 });
await page.screenshot({ path: "/tmp/ui_wizard.png" });
console.log("wizard rendered. headings:", await page.locator("h2").allInnerTexts());

// pick a major
const majorCard = page.locator(".picker-card").nth(0);
await majorCard.locator(".course-input.wide").fill("Machine Intelligence");
await page.waitForTimeout(700);
const firstMajor = majorCard.locator(".prog-row").first();
console.log("first major option:", await firstMajor.innerText().catch(() => "(none)"));
await firstMajor.click();

// pick a minor
const minorCard = page.locator(".picker-card").nth(1);
await minorCard.locator(".course-input.wide").fill("Finance");
await page.waitForTimeout(700);
await minorCard.locator(".prog-row").first().click();

await page.waitForTimeout(300);
await page.screenshot({ path: "/tmp/ui_wizard_filled.png" });

// generate
await page.locator("button.primary-btn").click();
await page.waitForSelector(".board .term-col", { timeout: 20000 });
await page.waitForTimeout(2500); // let audit + ensureBatch + lines settle
const cols = await page.locator(".term-col").count();
const cards = await page.locator(".course-card").count();
const edges = await page.locator(".dep-svg path.edge").count();
const slots = await page.locator(".course-card.slot").count();
console.log(`board: ${cols} terms, ${cards} cards, ${slots} slots, ${edges} dependency lines`);
await page.screenshot({ path: "/tmp/ui_board.png", fullPage: false });

console.log("console errors:", errors.length ? errors.slice(0, 8) : "none");
await browser.close();
