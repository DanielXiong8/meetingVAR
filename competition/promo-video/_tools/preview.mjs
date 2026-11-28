/**
 * 预览：渲染指定时间点的静帧，用于人工检查构图。
 * 用法: node preview.mjs 3.2 21 47 88
 */
import { chromium } from "playwright";
import fs from "node:fs/promises";

const times = process.argv.slice(2).map(Number).filter((n) => !Number.isNaN(n));
const OUT = "C:/Users/Lenovo/projects/meetingVAR/competition/promo-video/build/preview";
await fs.mkdir(OUT, { recursive: true });

const browser = await chromium.launch({ channel: "chrome", args: ["--force-color-profile=srgb", "--font-render-hinting=none"] });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });

page.on("console", (m) => {
  if (m.type() === "error") console.log("[console]", m.text());
});
page.on("pageerror", (e) => console.log("[pageerror]", e.message));

const PAGE = process.env.PAGE || "index.html";
await page.goto(`http://127.0.0.1:8734/${PAGE}`, { waitUntil: "load" });
await page.waitForFunction(() => window.__ready === true, null, { timeout: 60000 });
console.log("ready. duration =", await page.evaluate(() => window.DURATION));
console.log("gaps =", JSON.stringify(await page.evaluate(() => window.__gaps)));

for (const t of times) {
  await page.evaluate((v) => window.renderAt(v), t);
  await page.waitForTimeout(60);
  const name = `${OUT}/t_${String(t).replace(".", "_")}.png`;
  await page.screenshot({ path: name });
  console.log("saved", name);
}

await browser.close();