/**
 * 逐帧渲染成片。
 * 帧直接从 Playwright 截图喂给 ffmpeg（image2pipe），不落盘 7000 张 PNG。
 *
 * 用法: node render.mjs [fps]
 */
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import fs from "node:fs/promises";

const FPS = Number(process.argv[2] || 30);
const ROOT = "C:/Users/Lenovo/projects/meetingVAR/competition/promo-video";
const FFMPEG = `${ROOT}/_tools/node_modules/ffmpeg-static/ffmpeg.exe`;
const TAG = process.env.TAG || ""; // 例如 60s
const PAGE = process.env.PAGE || "index.html";
const VIDEO_ONLY = `${ROOT}/out/video_silent${TAG}.mp4`;
const MUSIC = `${ROOT}/out/music${TAG}.wav`;
const FINAL = `${ROOT}/out/meetingVAR_promo${TAG}_1080p.mp4`;

await fs.mkdir(`${ROOT}/out`, { recursive: true });

const browser = await chromium.launch({
  channel: "chrome",
  args: ["--force-color-profile=srgb", "--font-render-hinting=none", "--disable-lcd-text"],
});
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
page.on("pageerror", (e) => console.log("[pageerror]", e.message));

await page.goto(`http://127.0.0.1:8734/${PAGE}`, { waitUntil: "load" });
await page.waitForFunction(() => window.__ready === true, null, { timeout: 120000 });
const dur = await page.evaluate(() => window.DURATION);
const LIMIT = Number(process.env.LIMIT_SECONDS || 0); // 冒烟测试用
const renderDur = LIMIT > 0 ? Math.min(LIMIT, dur) : dur;
const total = Math.round(renderDur * FPS);
const gaps = await page.evaluate(() => window.__gaps);
if (gaps.length) throw new Error("timeline has gaps: " + JSON.stringify(gaps));
console.log(`duration=${dur}s fps=${FPS} frames=${total}`);

const ff = spawn(FFMPEG, [
  "-y",
  "-f", "image2pipe",
  "-vcodec", "mjpeg",
  "-framerate", String(FPS),
  "-i", "-",
  "-c:v", "libx264",
  "-preset", "slow",
  "-crf", "17",
  "-pix_fmt", "yuv420p",
  "-colorspace", "bt709",
  "-color_primaries", "bt709",
  "-color_trc", "bt709",
  "-x264-params", "keyint=60:min-keyint=60",
  VIDEO_ONLY,
], { stdio: ["pipe", "inherit", "inherit"] });

const ffDone = new Promise((res, rej) => {
  ff.on("close", (code) => (code === 0 ? res() : rej(new Error("ffmpeg exit " + code))));
});

const started = Date.now();
for (let i = 0; i < total; i++) {
  const t = i / FPS;
  await page.evaluate((v) => window.renderAt(v), t);
  const buf = await page.screenshot({ type: "jpeg", quality: 95 });
  if (!ff.stdin.write(buf)) {
    await new Promise((r) => ff.stdin.once("drain", r));
  }
  if (i % 300 === 0 || i === total - 1) {
    const el = (Date.now() - started) / 1000;
    const fps = (i + 1) / el;
    const eta = (total - i - 1) / Math.max(0.1, fps);
    console.log(`frame ${i + 1}/${total}  ${fps.toFixed(1)} fps  elapsed ${el.toFixed(0)}s  eta ${eta.toFixed(0)}s`);
  }
}
ff.stdin.end();
await ffDone;
await browser.close();
console.log("video track done:", VIDEO_ONLY);

// 合入配乐
const mux = spawn(FFMPEG, [
  "-y",
  "-i", VIDEO_ONLY,
  "-i", MUSIC,
  "-c:v", "copy",
  "-c:a", "aac",
  "-b:a", "192k",
  "-shortest",
  "-movflags", "+faststart",
  FINAL,
], { stdio: "inherit" });
await new Promise((res, rej) => mux.on("close", (c) => (c === 0 ? res() : rej(new Error("mux exit " + c)))));
console.log("final:", FINAL);