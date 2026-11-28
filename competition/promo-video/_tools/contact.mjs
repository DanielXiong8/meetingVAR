/** 全片联络表：每 4 秒取一帧，供整体构图与节奏检查 */
import { chromium } from "playwright";
import fs from "node:fs/promises";

const OUT = "C:/Users/Lenovo/projects/meetingVAR/competition/promo-video/build/preview/sheet";
await fs.rm(OUT, { recursive: true, force: true });
await fs.mkdir(OUT, { recursive: true });

const browser = await chromium.launch({ channel: "chrome", args: ["--force-color-profile=srgb"] });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
page.on("pageerror", (e) => console.log("[pageerror]", e.message));
await page.goto("http://127.0.0.1:8734/index.html", { waitUntil: "load" });
await page.waitForFunction(() => window.__ready === true, null, { timeout: 60000 });

const dur = await page.evaluate(() => window.DURATION);
const times = [];
for (let t = 2; t < dur; t += 4) times.push(t);

for (const t of times) {
  await page.evaluate((v) => window.renderAt(v), t);
  await page.waitForTimeout(25);
  await page.screenshot({ path: `${OUT}/f_${String(t).padStart(3, "0")}.png` });
}
console.log("frames:", times.length, "gaps:", JSON.stringify(await page.evaluate(() => window.__gaps)));
await browser.close();