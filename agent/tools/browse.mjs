#!/usr/bin/env node
// Browser helper for the verify and design stages: drives the droplet's Chrome through
// puppeteer-core with a persistent profile, so one login serves every later screenshot.
//
//   node browse.mjs --profile DIR [--auth user:pass] [--viewport WxH] [--timeout MS] steps.json
//
// steps.json is a list of single-key objects, run in order:
//   {"goto": "https://host/path"}          open a page (basic auth from --auth applies)
//   {"fill": ["css", "value"]}             clear a field and type into it
//   {"click": "css"}                       click; follows a navigation if one starts
//   {"wait": "css" | 1500 | "navigation"}  wait for a selector, milliseconds, or the next load
//   {"viewport": "390x844"}                change the viewport (default 1366x900)
//   {"screenshot": "path.png"}             viewport screenshot (add "full": true for the page)
//   {"dump": "path.html"}                  write the page's current HTML
// One JSON line per step goes to stdout ({"step", "ok", "url", "status"}); a failing step
// prints "step N (<action>): <reason>" to stderr and exits 1. Cookies, local storage and
// the login they carry live in --profile; reuse it across invocations.

import fs from "node:fs";
import path from "node:path";
import puppeteer from "puppeteer-core";

const DEFAULT_VIEWPORT = "1366x900";
const DEFAULT_TIMEOUT = 30_000;
const NAV_AFTER_CLICK_MS = 3_000;

function usage(msg) {
  if (msg) console.error(msg);
  console.error("usage: node browse.mjs --profile DIR [--auth user:pass] [--viewport WxH] [--timeout MS] steps.json");
  process.exit(2);
}

function parseArgs(argv) {
  const opts = { viewport: DEFAULT_VIEWPORT, timeout: DEFAULT_TIMEOUT };
  const rest = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--profile" || a === "--auth" || a === "--viewport" || a === "--timeout") {
      if (i + 1 >= argv.length) usage(`${a} needs a value`);
      opts[a.slice(2)] = argv[++i];
    } else if (a.startsWith("--")) {
      usage(`unknown option ${a}`);
    } else {
      rest.push(a);
    }
  }
  if (!opts.profile) usage("--profile is required");
  if (rest.length !== 1) usage("exactly one steps.json is required");
  opts.steps = rest[0];
  opts.timeout = Number(opts.timeout);
  if (!Number.isFinite(opts.timeout) || opts.timeout <= 0) usage("--timeout must be a positive number of ms");
  return opts;
}

function parseViewport(text) {
  const m = /^(\d+)x(\d+)$/.exec(String(text));
  if (!m) throw new Error(`viewport must look like 1366x900, got ${text}`);
  return { width: Number(m[1]), height: Number(m[2]) };
}

function readSteps(file) {
  let steps;
  try {
    steps = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (e) {
    usage(`cannot read ${file}: ${e.message}`);
  }
  if (!Array.isArray(steps) || steps.length === 0) usage("steps.json must be a non-empty list");
  return steps;
}

const ACTIONS = new Set(["goto", "fill", "click", "wait", "viewport", "screenshot", "dump"]);

function actionOf(step) {
  if (!step || typeof step !== "object") throw new Error("each step must be an object");
  const keys = Object.keys(step).filter((k) => ACTIONS.has(k));
  if (keys.length !== 1) throw new Error(`each step needs exactly one of ${[...ACTIONS].join(", ")}`);
  return keys[0];
}

async function runStep(page, step, action, opts) {
  const arg = step[action];
  let status = null;
  switch (action) {
    case "goto": {
      const res = await page.goto(String(arg), { waitUntil: "load", timeout: opts.timeout });
      status = res ? res.status() : null;
      break;
    }
    case "fill": {
      if (!Array.isArray(arg) || arg.length !== 2) throw new Error('fill needs ["css", "value"]');
      const [sel, value] = arg;
      await page.waitForSelector(sel, { timeout: opts.timeout });
      await page.$eval(sel, (el) => { el.value = ""; });
      await page.type(sel, String(value));
      break;
    }
    case "click": {
      await page.waitForSelector(String(arg), { timeout: opts.timeout });
      const nav = page.waitForNavigation({ waitUntil: "load", timeout: NAV_AFTER_CLICK_MS }).catch(() => null);
      await page.click(String(arg));
      const res = await nav;
      status = res ? res.status() : null;
      break;
    }
    case "wait": {
      if (typeof arg === "number") await new Promise((r) => setTimeout(r, arg));
      else if (arg === "navigation") await page.waitForNavigation({ waitUntil: "load", timeout: opts.timeout });
      else await page.waitForSelector(String(arg), { timeout: opts.timeout });
      break;
    }
    case "viewport":
      await page.setViewport(parseViewport(arg));
      break;
    case "screenshot": {
      const file = String(arg);
      fs.mkdirSync(path.dirname(file), { recursive: true });
      await page.screenshot({ path: file, fullPage: Boolean(step.full) });
      break;
    }
    case "dump": {
      const file = String(arg);
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, await page.content());
      break;
    }
    default:
      throw new Error(`unknown action ${action}`);
  }
  return status;
}

// $BLACKSMITH_BROWSER is a command name (google-chrome-stable, chromium); puppeteer wants a path.
function resolveBrowser(name) {
  if (name.includes("/")) return name;
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    const candidate = path.join(dir, name);
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      return candidate;
    } catch {
      // not here
    }
  }
  throw new Error(`browser ${name} not found on PATH (set BLACKSMITH_BROWSER)`);
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const steps = readSteps(opts.steps);
  const executablePath = resolveBrowser(process.env.BLACKSMITH_BROWSER || "chromium");
  fs.mkdirSync(opts.profile, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath,
    headless: true,
    userDataDir: opts.profile,
    args: ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
  });
  const cookieFile = path.join(opts.profile, "blacksmith-cookies.json");
  let exitCode = 0;
  try {
    await restoreCookies(browser, cookieFile);
    const page = await browser.newPage();
    page.setDefaultTimeout(opts.timeout);
    await page.setViewport(parseViewport(opts.viewport));
    if (opts.auth) {
      const i = opts.auth.indexOf(":");
      if (i < 1) usage("--auth must be user:pass");
      await page.authenticate({ username: opts.auth.slice(0, i), password: opts.auth.slice(i + 1) });
    }
    for (let i = 0; i < steps.length; i++) {
      const step = steps[i];
      let action;
      try {
        action = actionOf(step);
        const status = await runStep(page, step, action, opts);
        console.log(JSON.stringify({ step: i + 1, action, ok: true, url: page.url(), status }));
      } catch (e) {
        const what = action ? `${action} ${JSON.stringify(step[action])}` : JSON.stringify(step);
        console.error(`step ${i + 1} (${what}): ${e.message}`);
        exitCode = 1;
        break;
      }
    }
  } finally {
    await saveCookies(browser, cookieFile).catch((e) => console.error(`cookies not saved: ${e.message}`));
    await browser.close();
  }
  process.exit(exitCode);
}

// Chrome drops session cookies (no Expires) when it closes, which is what most logins set.
// Keep every cookie in the profile directory and put it back on the next run.
async function saveCookies(browser, file) {
  const cookies = await browser.cookies();
  fs.writeFileSync(file, JSON.stringify(cookies), { mode: 0o600 });
}

async function restoreCookies(browser, file) {
  if (!fs.existsSync(file)) return;
  let cookies;
  try {
    cookies = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return;
  }
  const fresh = cookies.filter((c) => !c.expires || c.expires < 0 || c.expires * 1000 > Date.now());
  if (fresh.length) await browser.setCookie(...fresh);
}

main().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
