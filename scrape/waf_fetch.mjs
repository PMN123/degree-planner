#!/usr/bin/env node
/*
 * waf_fetch.mjs — headless-browser helper for the Purdue catalog scraper.
 *
 * catalog.purdue.edu sits behind AWS WAF, which serves a JavaScript "challenge"
 * (HTTP 202, `x-amzn-waf-action: challenge`) on content.php / preview_program.php.
 * Plain requests/curl can't compute the token, but a real browser runs the
 * challenge JS automatically and is issued an `aws-waf-token` cookie. That cookie
 * is then portable to plain HTTP clients (proven), so this helper only needs to
 * *mint* the token; the Python scraper does the bulk fetching with `requests`.
 *
 * Subcommands:
 *   mint  [--out FILE] [--warm URL]   solve the challenge, write {cookie,userAgent,mintedAt}
 *   fetch --url URL [--out FILE]       fetch one URL through the browser (fallback)
 *
 * Uses the system Chrome (channel: 'chrome') so no chromium download is needed.
 */

import { chromium } from 'playwright';
import { writeFileSync } from 'node:fs';

const UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';
const DEFAULT_WARM = 'https://catalog.purdue.edu/content.php?catoid=19&navoid=25484';
const SOLVED_NEEDLE = 'preview_program.php';

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) out[a.slice(2)] = argv[++i];
    else out._.push(a);
  }
  return out;
}

async function withPage(fn) {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const ctx = await browser.newContext({ userAgent: UA });
    const page = await ctx.newPage();
    return await fn(page, ctx);
  } finally {
    await browser.close();
  }
}

// Navigate and poll until the WAF challenge resolves into real content.
async function getSolved(page, url, needle = SOLVED_NEEDLE, maxSeconds = 40) {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
  let html = '';
  for (let i = 0; i < maxSeconds; i++) {
    try { html = await page.content(); } catch { html = ''; }
    if (html.includes(needle) || (html.length > 60000 && !/challenge|Just a moment/i.test(html))) return html;
    await page.waitForTimeout(1000);
  }
  return html;
}

function cookieHeader(cookies) {
  return cookies
    .filter((c) => c.domain.includes('catalog.purdue.edu') || c.name === 'aws-waf-token')
    .map((c) => `${c.name}=${c.value}`)
    .join('; ');
}

async function cmdMint(args) {
  const warm = args.warm || DEFAULT_WARM;
  const outPath = args.out || 'waf_session.json';
  const { cookie, ok } = await withPage(async (page, ctx) => {
    const html = await getSolved(page, warm);
    const cookies = await ctx.cookies();
    return { cookie: cookieHeader(cookies), ok: html.includes(SOLVED_NEEDLE), htmlLen: html.length };
  });
  if (!cookie.includes('aws-waf-token')) {
    console.error('mint: failed to obtain aws-waf-token');
    process.exit(2);
  }
  writeFileSync(outPath, JSON.stringify({ cookie, userAgent: UA, mintedAt: new Date().toISOString(), warmedOk: ok }, null, 2));
  console.error(`mint: wrote ${outPath} (token ok=${ok})`);
}

async function cmdFetch(args) {
  if (!args.url) { console.error('fetch: --url required'); process.exit(2); }
  const html = await withPage((page) => getSolved(page, args.url, args.needle || SOLVED_NEEDLE));
  if (args.out) { writeFileSync(args.out, html); console.error(`fetch: wrote ${args.out} (${html.length} bytes)`); }
  else process.stdout.write(html);
}

const args = parseArgs(process.argv.slice(2));
const cmd = args._[0];
if (cmd === 'mint') await cmdMint(args);
else if (cmd === 'fetch') await cmdFetch(args);
else { console.error('usage: waf_fetch.mjs <mint|fetch> [--url URL] [--out FILE] [--warm URL]'); process.exit(2); }
