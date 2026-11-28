/** 渲染封面图。用法: node poster.mjs <out.png> */
import { chromium } from "playwright";

const out = process.argv[2] || "C:/Users/Lenovo/projects/meetingVAR/competition/promo-video/out/poster.png";
const browser = await chromium.launch({ channel: "chrome", args: ["--force-color-profile=srgb"] });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
await page.goto("http://127.0.0.1:8734/poster.html", { waitUntil: "load" });
await page.evaluate(() => document.fonts.ready);
await page.waitForTimeout(400);
await page.screenshot({ path: out });
console.log("saved", out);
await browser.close();