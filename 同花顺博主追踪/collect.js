/**
 * 【全能的野人】同花顺发文 —— 采集脚本
 *
 * 逻辑：
 *   1. 用缓存的 hexin-v 令牌，纯 HTTP 调同花顺接口（无浏览器）
 *   2. 接口 403 时才用无头 Chrome 静默刷新令牌（约 2.5 秒，无窗口）
 *   3. 按帖子 ID 去重，只追加新发文
 *   4. 输出两份：根目录的人可读 .md + data/posts.json 结构化数据
 *
 * 零第三方依赖（Node 18+ 自带 fetch / WebSocket）
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const { spawn } = require('child_process');

// ---------- 路径与常量 ----------
const SCRIPT_DIR = __dirname;
const WORKSPACE = path.resolve(SCRIPT_DIR, '..');
const MD_PATH = path.join(WORKSPACE, '【全能的野人】同花顺发文.md');
const DATA_DIR = path.join(SCRIPT_DIR, 'data');
const POSTS_FILE = path.join(DATA_DIR, 'posts.json');
const STATE_FILE = path.join(DATA_DIR, 'state.json');
const LOG_DIR = path.join(SCRIPT_DIR, 'logs');
const LOG_FILE = path.join(LOG_DIR, 'run.log');
const LOCK_FILE = path.join(DATA_DIR, 'run.lock');

const AUTHOR = '全能的野人';
const USER_ID = 639669238;
const HOMEPAGE = 'https://t.10jqka.com.cn/lgt/user_page/?fid=305529#/';
const API = 'https://t.10jqka.com.cn/user_center/open/api/content/v2/get_by_uid';
const REFERER = 'https://t.10jqka.com.cn/lgt/user_page/?fid=305529';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36';

/** 只保留最近 N 天（含今天）的发文，超出部分自动清理 */
const RETENTION_DAYS = 30;
/** 文件格式指纹：命中说明是当前格式，可安全追加；未命中则整篇重建 */
const MD_FORMAT_MARK = '- 保留范围：最近一个月（超过的内容自动清理）';

const CHROME_CANDIDATES = [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  path.join(os.homedir(), 'AppData\\Local\\Google\\Chrome\\Application\\chrome.exe'),
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- 工具函数 ----------
function ensureDirs() {
  for (const d of [DATA_DIR, LOG_DIR]) {
    if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true });
  }
}

function log(msg) {
  const line = `[${cnNow()}] ${msg}`;
  try {
    ensureDirs();
    fs.appendFileSync(LOG_FILE, line + '\n', 'utf8');
  } catch (e) {
    /* 日志失败不影响主流程 */
  }
  console.log(line);
}

function readJSON(file, fallback) {
  try {
    if (!fs.existsSync(file)) return fallback;
    // 去掉可能存在的 BOM（手工编辑器保存时容易带上）
    return JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
  } catch (e) {
    log(`读取 ${path.basename(file)} 失败：${e.message}`);
    return fallback;
  }
}

function writeJSON(file, data) {
  const tmp = file + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2), 'utf8');
  fs.renameSync(tmp, file);
}

/** 毫秒时间戳 → 北京时间 {date:'2026-11-10', time:'14:35', full:'2026-11-10 14:35:56'} */
function toCN(ms) {
  const d = new Date(Number(ms) + 8 * 3600 * 1000);
  const p = (n) => String(n).padStart(2, '0');
  const date = `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`;
  const time = `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
  return { date, time, full: `${date} ${time}:${p(d.getUTCSeconds())}` };
}

function cnNow() {
  return toCN(Date.now()).full;
}

/** 保留期起始日（含）：今天往前推 RETENTION_DAYS-1 天，即「含今天共 30 天」 */
function cutoffDateCN() {
  return toCN(Date.now() - (RETENTION_DAYS - 1) * 86400000).date;
}

// ---------- 令牌：无头 Chrome 静默刷新 ----------
function findChrome() {
  for (const p of CHROME_CANDIDATES) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

async function refreshTokenViaHeadless() {
  const chrome = findChrome();
  if (!chrome) throw new Error('未找到可用的 Chrome / Edge');

  const port = 9400 + Math.floor(Math.random() * 200);
  const profile = path.join(os.tmpdir(), `ths-token-${Date.now()}`);
  const child = spawn(chrome, [
    '--headless=new',
    '--disable-gpu',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-extensions',
    '--disable-background-networking',
    '--mute-audio',
    '--window-size=1280,800',
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    REFERER,
  ], { stdio: 'ignore' });

  let token = null;
  try {
    let pageTarget = null;
    for (let i = 0; i < 40; i++) {
      await sleep(250);
      try {
        const res = await fetch(`http://127.0.0.1:${port}/json/list`);
        const list = await res.json();
        const page = list.find((t) => t.type === 'page' && String(t.url).includes('10jqka'));
        if (page && page.webSocketDebuggerUrl) { pageTarget = page; break; }
      } catch (e) { /* 端口尚未就绪 */ }
    }
    if (!pageTarget) throw new Error('无头浏览器启动超时');

    const ws = new WebSocket(pageTarget.webSocketDebuggerUrl);
    await new Promise((res, rej) => {
      ws.onopen = res;
      ws.onerror = () => rej(new Error('调试通道连接失败'));
      setTimeout(() => rej(new Error('调试通道连接超时')), 15000);
    });

    let seq = 0;
    const pending = new Map();
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
      } catch (e) { /* ignore */ }
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq;
      pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });

    for (let i = 0; i < 30; i++) {
      const r = await send('Runtime.evaluate', {
        expression: "localStorage.getItem('hexin-v')",
        returnByValue: true,
      });
      token = r && r.result && r.result.result && r.result.result.value;
      if (token) break;
      await sleep(300);
    }
    ws.close();
  } finally {
    try { child.kill(); } catch (e) { /* ignore */ }
    await sleep(500);
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  if (!token) throw new Error('未从页面取到令牌');
  return token;
}

// ---------- 抓取 ----------
async function fetchPostsOnce(token, timeoutMs) {
  const res = await fetch(API, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      referer: REFERER,
      'user-agent': UA,
      'hexin-v': token,
    },
    body: JSON.stringify({ user_id: USER_ID }),
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (res.status === 403) return { forbidden: true };
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const json = await res.json();
  if (json.status_code !== 0) throw new Error(`接口返回异常：${json.status_msg || json.status_code}`);
  return { contents: (json.data && json.data.contents) || [] };
}

/** 带重试的抓取：网络抖动时自动再试，避免单次超时导致整轮丢失 */
async function fetchPosts(token, attempts = 3, timeoutMs = 12000) {
  let lastErr = null;
  for (let i = 1; i <= attempts; i++) {
    try {
      return await fetchPostsOnce(token, timeoutMs);
    } catch (e) {
      lastErr = e;
      if (e && e.name === 'AbortError' || String(e.message).includes('aborted')) {
        log(`第 ${i}/${attempts} 次请求超时，${i < attempts ? '1 秒后重试' : '放弃'}`);
      } else {
        log(`第 ${i}/${attempts} 次请求失败：${e.message}`);
      }
      if (i < attempts) await sleep(1000);
    }
  }
  throw lastErr;
}

function normalize(c) {
  const info = c.info || {};
  const stat = c.stat || {};
  const tags = (c.tag && c.tag.tags) || [];
  const imgs = (c.image && c.image.urls) || [];
  const raw = (c.abstract && c.abstract.content) || '';
  const text = raw
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .trim();
  const cn = toCN(info.ctime);
  return {
    id: String(info.id || ''),
    ctime: Number(info.ctime) || 0,
    date: cn.date,
    time: cn.time,
    text,
    stocks: tags.map((t) => ({ name: t.name, code: t.code })),
    images: imgs.length,
    url: info.pc_jump_url || info.jump_url || '',
  };
}

// ---------- Markdown 渲染（追加式） ----------
function renderHeader() {
  return [
    `# 【${AUTHOR}】同花顺发文`,
    '',
    `- 博主：${AUTHOR}（同花顺用户 ID：${USER_ID}）`,
    `- 主页：${HOMEPAGE}`,
    '- 简介：慢就是快，保住本金是第一守则。',
    '- 记录内容：发布时间（北京时间，精确到分）｜关联个股标签｜正文原文',
    MD_FORMAT_MARK,
    '- 数据来源：同花顺博主主页自动采集；交易时段每 5 分钟一次，其余时段每 30 分钟一次',
    '',
    '> 本文件由脚本自动生成与维护，新发文直接追加在末尾，请勿手工编辑。',
    '',
    '---',
    '',
    '',
  ].join('\n');
}

function stockLabel(p) {
  return p.stocks && p.stocks.length
    ? p.stocks.map((s) => `${s.name}(${s.code})`).join('、')
    : '无个股标签';
}

/** 单条发文的 Markdown 片段 */
function renderPostBlock(p) {
  const body = p.text || (p.images > 0 ? '（纯图片帖，正文为空）' : '（正文为空）');
  return `### ${p.time} ｜ ${stockLabel(p)}\n${body}\n\n`;
}

/** 整篇重建：按时间正序、按日期分组 */
function renderFull(posts) {
  const sorted = [...posts].sort((a, b) => a.ctime - b.ctime);
  const out = [renderHeader()];
  let curDate = '';
  for (const p of sorted) {
    if (p.date !== curDate) {
      curDate = p.date;
      out.push(`## ${curDate}\n\n`);
    }
    out.push(renderPostBlock(p));
  }
  return out.join('');
}

/** 文件中最后出现的日期分组标题 */
function lastDateInMD(text) {
  const matches = [...String(text).matchAll(/^## (\d{4}-\d{2}-\d{2})$/gm)];
  return matches.length ? matches[matches.length - 1][1] : null;
}

/**
 * 写入 Markdown：
 * - 文件不存在 / 格式不符 / 乱序补录 → 整篇重建
 * - 其余情况只在末尾追加新发文，已有内容一字不动
 */
function writeMarkdown(posts, fresh) {
  const sortedFresh = [...fresh].sort((a, b) => a.ctime - b.ctime);
  let existing = '';
  try { existing = fs.readFileSync(MD_PATH, 'utf8'); } catch (e) { existing = ''; }

  const lastDate = lastDateInMD(existing);
  // 条数对不上说明文件被外部改过（或历史写入不完整），此时整篇重建，避免重复或漏记
  const blockCount = (existing.match(/^### /gm) || []).length;
  const needRebuild = !existing
    || !existing.includes(MD_FORMAT_MARK)
    || blockCount !== posts.length
    || (sortedFresh.length > 0 && lastDate && sortedFresh[0].date < lastDate);

  if (needRebuild) {
    fs.writeFileSync(MD_PATH, renderFull(posts), 'utf8');
    return 'rebuild';
  }
  if (!sortedFresh.length) return 'noop';

  let curDate = lastDate;
  let chunk = '';
  for (const p of sortedFresh) {
    if (p.date !== curDate) {
      curDate = p.date;
      chunk += `## ${curDate}\n\n`;
    }
    chunk += renderPostBlock(p);
  }
  fs.appendFileSync(MD_PATH, chunk, 'utf8');
  return 'append';
}

// ---------- 并发锁 ----------
function acquireLock() {
  if (fs.existsSync(LOCK_FILE)) {
    try {
      const age = Date.now() - fs.statSync(LOCK_FILE).mtimeMs;
      if (age < 180000) return false; // 3 分钟内有任务在跑
    } catch (e) { /* ignore */ }
  }
  fs.writeFileSync(LOCK_FILE, String(process.pid), 'utf8');
  return true;
}

function releaseLock() {
  try { fs.unlinkSync(LOCK_FILE); } catch (e) { /* ignore */ }
}

// 兜底：进程异常退出也要把锁清掉，避免卡住后续轮次
process.on('exit', () => { releaseLock(); });
process.on('SIGINT', () => { releaseLock(); process.exit(130); });
process.on('uncaughtException', (e) => {
  log(`未捕获异常：${e.message}`);
  releaseLock();
  process.exit(1);
});

// ---------- 主流程 ----------
async function main() {
  ensureDirs();
  if (!acquireLock()) {
    log('上一轮仍在执行，本轮跳过');
    return;
  }

  try {
    const state = readJSON(STATE_FILE, { token: '', token_at: 0, seen_ids: [] });
    const store = readJSON(POSTS_FILE, { posts: [] });
    const known = new Set(state.seen_ids || []);
    const posts = store.posts || [];

    let token = state.token;
    let result;
    if (token) {
      try {
        result = await fetchPosts(token, 2);
      } catch (e) {
        log(`缓存令牌抓取失败（${e.message}），转为刷新令牌`);
        result = { forbidden: true };
      }
    } else {
      result = { forbidden: true };
    }

    if (result.forbidden) {
      log('令牌失效或缺失，启动无头浏览器刷新');
      const t0 = Date.now();
      token = await refreshTokenViaHeadless();
      log(`令牌刷新完成，耗时 ${Date.now() - t0}ms`);
      result = await fetchPosts(token, 3);
      if (result.forbidden) throw new Error('刷新后仍被拒绝访问');
    }

    const contents = result.contents || [];
    if (!contents.length) {
      log('接口返回 0 条，可能被限流，本轮不改动数据');
      return;
    }

    const fresh = [];
    for (const c of contents) {
      const item = normalize(c);
      if (!item.id || known.has(item.id)) continue;
      known.add(item.id);
      fresh.push(item);
    }

    state.token = token;
    if (!state.token_at || fresh.length) state.token_at = Date.now();

    if (fresh.length) posts.push(...fresh);
    posts.sort((a, b) => a.ctime - b.ctime);

    // 只保留最近半个月：超出保留期的记录从数据与文件中一并清理
    const cutoff = cutoffDateCN();
    const kept = posts.filter((p) => p.date >= cutoff);
    const removed = posts.length - kept.length;
    const freshKept = fresh.filter((p) => p.date >= cutoff);

    // 有新增、有清理、或文件格式不符需要重建时才写盘；其余情况内部自动 noop
    const mode = writeMarkdown(kept, freshKept);
    if (mode !== 'noop') log(`Markdown 写入方式：${mode === 'append' ? '末尾追加' : '整篇重建'}`);

    writeJSON(POSTS_FILE, {
      meta: { updated_at: cnNow(), retention_days: RETENTION_DAYS, total: kept.length },
      posts: kept,
    });
    state.seen_ids = Array.from(known);
    writeJSON(STATE_FILE, state);

    if (fresh.length) {
      log(`新增 ${fresh.length} 条：${fresh.map((p) => `${p.date} ${p.time}[${p.id}]`).join(' , ')}`);
    } else {
      log(`无新发文（当前已收录 ${kept.length} 条）`);
    }
    if (removed > 0) log(`清理超期发文 ${removed} 条（保留 ${cutoff} 及之后）`);
  } catch (e) {
    log(`执行失败：${e.message}`);
    process.exitCode = 1;
  } finally {
    releaseLock();
  }
}

main();
