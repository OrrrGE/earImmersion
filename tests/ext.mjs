// 在真实的浏览器里测插件：node tests/ext.mjs chromium|firefox [输出目录] [浏览器的路径]
// 只需要 Node 22+ 和浏览器本身（Firefox 还要 geckodriver）。Linux 下自己开一个虚拟显示器；浏览器是静音的，不出声。
// 做的事和人一样：用鼠标双击 / 拖选网页上的词 → 看喇叭出没出来、多大、在哪 → 移上去、点一下 → 看卡片上的字、有没有按顺序把几段发音播完。
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const EXT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../extension');
const [WHICH = 'chromium', OUT = 'ext-' + WHICH, BIN] = process.argv.slice(2);
const PAGE = `<!doctype html><meta charset="utf-8"><title>earImmersion test</title>
<style>body{font:20px/2.2 Arial,sans-serif;margin:40px;width:900px}</style>
<p><span id="word">plaid</span> is an efficient engine for <span id="phrase">late interaction retrieval</span>.</p>
<p><span id="zh">这一行是中文不应该触发。</span></p>
<p><span id="compound">Utility-Oriented</span> ranking with a <span id="unknown">reranker</span></p>
<p>a <span id="entry">state-of-the-art</span> and <span id="odd">data-driven</span> method</p>
<p><span id="wav">cache set</span> and the <span id="acronym">AUC</span> of a <span id="prefix">bi-encoder</span></p>`;

// 这台机器上的浏览器放得出声音吗（静音也算，要的是音频的时钟在走）。GitHub 的测试机没有声卡，放不出
const PROBE = 'const c = new AudioContext(); c.resume(); setTimeout(() => done(c.state === "running" && c.currentTime > 0), 1500);';
const sleep = ms => new Promise(r => setTimeout(r, ms));
const kids = [];
let failed = 0;

function check(name, ok, detail = '') {
  if (!ok) failed++;
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? '  ← ' + detail : ''}`);
}

// 每隔一会儿问一次，直到 fn 给出真值或者超时
async function until(fn, ms, step = 100) {
  for (const end = Date.now() + ms; ;) {
    const v = await fn();
    if (v || Date.now() > end) return v;
    await sleep(step);
  }
}

const freePort = () => new Promise(res => {
  const s = net.createServer().listen(0, '127.0.0.1', () => { const { port } = s.address(); s.close(() => res(port)); });
});

function start(cmd, args, env) {
  const p = spawn(cmd, args, { stdio: 'ignore', env });
  p.on('error', e => console.log(`could not start ${cmd}: ${e.message}`));
  kids.push(p);
  return p;
}

// ───────── Chromium：DevTools 协议 ─────────

async function devtools(url) {
  const ws = new WebSocket(url), waiting = new Map(), events = [];
  let n = 0;
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('cannot connect to ' + url)); });
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (!m.id) return void events.push(m);
    const w = waiting.get(m.id);
    waiting.delete(m.id);
    m.error ? w.rej(new Error(`${w.method}: ${m.error.message}`)) : w.res(m.result);
  };
  const send = (method, params = {}) => new Promise((res, rej) => {
    waiting.set(++n, { res, rej, method });
    ws.send(JSON.stringify({ id: n, method, params }));
  });
  return { send, events, close: () => ws.close() };
}

async function chromium(env) {
  const snap = path.join(os.homedir(), 'snap/chromium/common'); // snap 版的 Chromium 只碰得到这里面的目录
  const dir = fs.mkdtempSync(path.join(fs.existsSync(snap) ? snap : os.tmpdir(), 'earimmersion-test-'));
  const port = await freePort();
  start(BIN || 'chromium', [`--user-data-dir=${dir}`, `--load-extension=${EXT}`,
    '--disable-features=DisableLoadExtensionCommandLineSwitch', `--remote-debugging-port=${port}`, '--mute-audio',
    '--no-first-run', '--no-default-browser-check', '--window-size=1100,800', '--force-device-scale-factor=1', 'about:blank'], env);
  const targets = () => fetch(`http://127.0.0.1:${port}/json/list`).then(r => r.json()).catch(() => []);
  const page = await until(async () => (await targets()).find(t => t.type === 'page'), 40000, 300);
  if (!page) throw new Error('Chromium did not start');
  const cdp = await devtools(page.webSocketDebuggerUrl);
  for (const d of ['Page', 'Runtime', 'Log', 'DOM', 'WebAudio']) await cdp.send(d + '.enable');
  const run = async code => (await cdp.send('Runtime.evaluate', { expression: code, returnByValue: true })).result.value;
  const mouse = (type, x, y, more = {}) => cdp.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', buttons: 1, clickCount: 1, ...more });
  const find = (n, ok) => (ok(n) ? n : [...(n.children || []), ...(n.shadowRoots || [])].reduce((r, c) => r || find(c, ok), null));
  return {
    version: async () => (await fetch(`http://127.0.0.1:${port}/json/version`).then(r => r.json())).Browser,
    async open(url) {
      await cdp.send('Page.navigate', { url });
      await until(() => run('document.readyState === "complete"'), 10000);
    },
    run,
    async dblclick(x, y) {
      for (const clickCount of [1, 2]) {
        await mouse('mousePressed', x, y, { clickCount });
        await mouse('mouseReleased', x, y, { clickCount, buttons: 0 });
      }
    },
    async drag(x1, y1, x2, y2) {
      await mouse('mousePressed', x1, y1);
      for (const k of [0.3, 0.7, 1]) await mouse('mouseMoved', x1 + (x2 - x1) * k, y1 + (y2 - y1) * k);
      await mouse('mouseReleased', x2, y2, { buttons: 0 });
    },
    hover: (x, y) => mouse('mouseMoved', x, y, { button: 'none', buttons: 0 }),
    async click(x, y) {
      await mouse('mousePressed', x, y);
      await mouse('mouseReleased', x, y, { buttons: 0 });
    },
    // 喇叭和卡片在一个封闭的 shadow root 里，网页里的脚本看不到，只能从调试协议这边进去
    async popup() {
      const { root } = await cdp.send('DOM.getDocument', { depth: -1, pierce: true });
      const host = find(root, n => n.localName === 'moerduo-pop');
      if (!host || (host.attributes || []).includes('hidden')) return null;
      const { object } = await cdp.send('DOM.resolveNode', { nodeId: host.shadowRoots[0].nodeId });
      const r = await cdp.send('Runtime.callFunctionOn', {
        objectId: object.objectId, returnByValue: true, functionDeclaration: `function () {
          const b = this.querySelector('button'), d = this.querySelector('div'), r = b.getBoundingClientRect();
          return { x: r.x, y: r.y, w: r.width, h: r.height, icon: this.querySelector('svg').getBoundingClientRect().width,
                   on: b.classList.contains('on'), card: d.hidden ? null : d.textContent };
        }` });
      return r.result.value;
    },
    shot: async () => Buffer.from((await cdp.send('Page.captureScreenshot')).data, 'base64'),
    sound: async () => (await cdp.send('Runtime.evaluate', { expression: `new Promise(done => { ${PROBE} })`, awaitPromise: true, returnByValue: true, userGesture: true })).result.value,
    // 到现在为止，网页里一共排了多少段音频去播
    sources: () => cdp.events.filter(e => e.method === 'WebAudio.audioNodeCreated' && e.params.node.nodeType === 'AudioBufferSource').length,
    // 到现在为止，后台脚本被问过哪些东西（"mean:…"、"voice:…"、"mt:…"）
    async asked() {
      const sw = (await targets()).find(t => t.type === 'service_worker' && t.url.endsWith('/bg.js'));
      if (!sw) return null;
      const c = await devtools(sw.webSocketDebuggerUrl);
      const r = await c.send('Runtime.evaluate', { expression: '[...cache.keys()]', returnByValue: true });
      c.close();
      return r.result.value || null;
    },
    errors: () => cdp.events.filter(e => e.method === 'Runtime.exceptionThrown' || (e.method === 'Log.entryAdded' && e.params.entry.level === 'error'))
      .map(e => (e.params.entry ? e.params.entry.text : e.params.exceptionDetails.exception?.description || e.params.exceptionDetails.text)),
    async close() {
      await cdp.send('Browser.close').catch(() => {});
      await sleep(1500);
      fs.rmSync(dir, { recursive: true, force: true });
    },
  };
}

// ───────── Firefox：WebDriver（geckodriver）─────────

async function firefox(env) {
  const port = await freePort();
  start('geckodriver', ['--port', String(port)], env);
  const call = async (method, url, body) => {
    const r = await fetch(`http://127.0.0.1:${port}${url}`, { method, headers: { 'content-type': 'application/json' }, body: body && JSON.stringify(body) });
    const j = await r.json();
    if (!r.ok) throw new Error((j.value && j.value.message) || 'webdriver ' + r.status);
    return j.value;
  };
  if (!await until(() => call('GET', '/status').catch(() => null), 20000, 300)) throw new Error('geckodriver did not start');
  const options = { prefs: { 'media.volume_scale': '0.0' }, ...(BIN ? { binary: BIN } : {}) }; // 音量调到 0：照常播放，只是不出声
  const s = await call('POST', '/session', { capabilities: { alwaysMatch: { 'moz:firefoxOptions': options } } });
  const wd = (method, url, body) => call(method, `/session/${s.sessionId}${url}`, body);
  await wd('POST', '/moz/addon/install', { path: EXT, temporary: true });
  await wd('POST', '/window/rect', { width: 1100, height: 800 }).catch(() => {});
  const run = (code, ...args) => wd('POST', '/execute/sync', { script: 'return ' + code, args });
  const act = actions => wd('POST', '/actions', { actions: [{ type: 'pointer', id: 'mouse', parameters: { pointerType: 'mouse' }, actions }] });
  const to = (x, y, duration = 0) => ({ type: 'pointerMove', duration, x: Math.round(x), y: Math.round(y), origin: 'viewport' });
  const down = { type: 'pointerDown', button: 0 }, up = { type: 'pointerUp', button: 0 };
  const id = ref => Object.values(ref)[0];
  return {
    version: async () => `Firefox/${s.capabilities.browserVersion}`,
    open: url => wd('POST', '/url', { url }),
    run,
    dblclick: (x, y) => act([to(x, y), down, up, down, up]),
    drag: (x1, y1, x2, y2) => act([to(x1, y1), down, to((x1 + x2) / 2, (y1 + y2) / 2, 60), to(x2, y2, 60), up]),
    hover: (x, y) => act([to(x, y, 40)]),
    click: (x, y) => act([to(x, y), down, up]),
    async popup() {
      const host = await wd('POST', '/element', { using: 'css selector', value: 'moerduo-pop' }).then(id, () => null);
      if (!host || await wd('GET', `/element/${host}/attribute/hidden`) !== null) return null;
      const root = id(await wd('GET', `/element/${host}/shadow`)); // 封闭的 shadow root，WebDriver 进得去
      const el = async css => id(await wd('POST', `/shadow/${root}/element`, { using: 'css selector', value: css }));
      const [b, d, svg] = [await el('button'), await el('div'), await el('svg')];
      const r = await wd('GET', `/element/${b}/rect`);
      return {
        x: r.x, y: r.y, w: r.width, h: r.height, icon: (await wd('GET', `/element/${svg}/rect`)).width,
        on: /\bon\b/.test(await wd('GET', `/element/${b}/attribute/class`) || ''),
        card: await wd('GET', `/element/${d}/attribute/hidden`) !== null ? null : await wd('GET', `/element/${d}/property/textContent`),
      };
    },
    shot: async () => Buffer.from(await wd('GET', '/screenshot'), 'base64'),
    sound: () => wd('POST', '/execute/async', { script: 'const done = arguments[0]; ' + PROBE, args: [] }),
    sources: () => null, // 这两样 WebDriver 看不到
    asked: async () => null,
    errors: () => [],
    close: () => wd('DELETE', '').catch(() => {}),
  };
}

// ───────── 测试本身 ─────────

const PENDING = /^…$|机翻：…(\n|$)/; // 卡片上还有没回来的东西

// 选中 id 这一处的字 → 喇叭 → 移上去 → 点击。want：{clips 几段发音, secs 播完大概要多久, card 卡片上该有的, not 不该有的, mt 该不该有机翻}
const rect = async (b, id) => JSON.parse(await b.run(`JSON.stringify(document.getElementById("${id}").getBoundingClientRect())`));

async function scenario(b, name, id, how, want) {
  const r = await rect(b, id);
  const y = r.y + r.height / 2;
  how === 'dblclick' ? await b.dblclick(r.x + r.width / 2, y) : await b.drag(r.x + 1, y, r.right - 1, y);
  const chosen = await b.run('String(getSelection())');
  check(`${name}：选中了 ${JSON.stringify(want.text)}`, chosen === want.text, chosen === want.text ? '' : JSON.stringify(chosen));
  let p = await until(() => b.popup(), 3000);
  check(`${name}：出现了小喇叭`, !!p);
  if (!p) return;
  check(`${name}：喇叭是 24 像素见方，图标 16 像素`, Math.abs(p.w - 24) < 0.6 && Math.abs(p.h - 24) < 0.6 && Math.abs(p.icon - 16) < 0.6, `${p.w}x${p.h}, icon ${p.icon}`);
  check(`${name}：喇叭紧挨在选区的右下方`, p.x >= r.right && p.x <= r.right + 12 && p.y >= r.bottom && p.y <= r.bottom + 12,
    `selection ends at ${Math.round(r.right)},${Math.round(r.bottom)}; button at ${Math.round(p.x)},${Math.round(p.y)}`);
  check(`${name}：没点之前不显示卡片`, p.card === null);
  fs.writeFileSync(path.join(OUT, `${name}-1-icon.png`), await b.shot());

  const before = b.sources(), earlier = await b.asked() || [];
  await b.hover(p.x + p.w / 2, p.y + p.h / 2);
  await sleep(1500); // 鼠标移上去就开始取数据了
  const hovered = (await b.asked() || []).filter(k => !earlier.includes(k));
  if (b.sources() !== null) {
    check(`${name}：鼠标移上去时已经在取释义和发音`, hovered.includes('mean:' + want.text) && hovered.includes('voice:' + want.text), hovered.join(' | '));
    check(`${name}：没点之前不会把选中的内容发给机翻`, !hovered.some(k => k.startsWith('mt:')), hovered.join(' | '));
  }
  await b.click(p.x + p.w / 2, p.y + p.h / 2);
  const t0 = Date.now();
  let lit = false, secs = null, card = null;
  await until(async () => { // 喇叭亮着 = 正在读；读完会自己灭
    p = await b.popup();
    if (!p) return true;
    if (p.on) lit = true;
    else if (lit && secs === null) secs = (Date.now() - t0) / 1000;
    card = p.card;
    return secs !== null && card !== null && !PENDING.test(card);
  }, 20000, 50);
  fs.writeFileSync(path.join(OUT, `${name}-2-card.png`), await b.shot());
  console.log(`     卡片：${JSON.stringify(card)}`);
  check(`${name}：点击后喇叭亮起，${b.audible ? '读完后' : '过一会儿自己'}熄灭`, lit && secs !== null, lit ? (secs === null ? 'never went off' : '') : 'never lit');
  if (!b.audible) console.log(`??   ${name}：这台机器放不出声音，"几段发音依次读完、用时对得上"这一项没能验证`);
  else if (secs !== null) check(`${name}：读了 ${want.secs[0]}–${want.secs[1]} 秒`, secs >= want.secs[0] && secs <= want.secs[1], secs.toFixed(2) + ' s');
  if (before !== null) check(`${name}：依次播了 ${want.clips.join(' 或 ')} 段发音`, want.clips.includes(b.sources() - before), `${b.sources() - before} audio sources`);
  for (const re of want.card) check(`${name}：卡片上有 ${re.source}`, re.test(card || ''));
  for (const re of want.not || []) check(`${name}：卡片上没有 ${re.source}`, !re.test(card || ''));
  if (want.mt) {
    const asked = await b.asked();
    if (asked) check(`${name}：点了之后才去机翻`, asked.some(k => k.startsWith('mt:')), asked.join(' | '));
    if (/机翻：.*[一-鿿]/.test(card || '')) check(`${name}：卡片上有机翻的译文`, true);
    else console.log(`??   ${name}：机翻这一次没有结果（服务没连上或者当天的免费额度用完了），这一项没能验证`);
  }
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const env = { ...process.env };
  if (process.platform === 'linux' && !env.SHOW) { // 设了 SHOW=1 就用当前的显示器，看得到浏览器窗口
    start('Xvfb', [':98', '-screen', '0', '1280x900x24', '-nolisten', 'tcp'], env);
    Object.assign(env, { DISPLAY: ':98' });
    delete env.XAUTHORITY;
    delete env.WAYLAND_DISPLAY;
    await sleep(1000);
  }
  const server = http.createServer((q, s) => (q.url === '/' ? s.writeHead(200, { 'content-type': 'text/html' }).end(PAGE) : s.writeHead(204).end()));
  await new Promise(res => server.listen(0, '127.0.0.1', res));
  const b = await (WHICH === 'firefox' ? firefox : chromium)(env);
  try {
    console.log(await b.version());
    await b.open(`http://127.0.0.1:${server.address().port}/`);
    await sleep(800); // 页面脚本在页面加载完之后才注入
    const wide = await b.run('innerWidth');
    check('浏览器窗口够宽，测试页上的字不会折行', wide >= 1000, `innerWidth ${wide}`);
    await b.click(500, 600); // 浏览器要求先有过一次点击，网页才可以出声
    b.audible = await b.sound();
    console.log(`     这台机器上的浏览器${b.audible ? '放得出声音（已静音）' : '放不出声音'}`);

    await scenario(b, '1-word', 'word', 'dblclick', { text: 'plaid', clips: [1], secs: [0.3, 2.5], card: [/^\/plæd\//, /格子/], not: [/机翻/, /仅供参考/, /失败/] });
    await scenario(b, '2-phrase', 'phrase', 'drag', { text: 'late interaction retrieval', clips: [3], secs: [1.5, 6], mt: true,
      card: [/late：/, /interaction：/, /retrieval：/], not: [/仅供参考/, /失败/] });
    await scenario(b, '3-compound', 'compound', 'drag', { text: 'Utility-Oriented', clips: [2], secs: [1, 5], mt: true,
      card: [/Utility：.*(效用|实用|功用)/, /Oriented：.*(导向|面向|以)/], not: [/仅供参考/, /失败/] });
    await scenario(b, '4-unknown', 'unknown', 'dblclick', { text: 'reranker', clips: [1], secs: [0.3, 4], mt: true,
      card: [/读音是按拼写推测的/], not: [/失败/] });

    // 词典里收了整条的复合词：有现成的发音就一段读完
    await scenario(b, '5-entry', 'entry', 'drag', { text: 'state-of-the-art', clips: [1], secs: [0.5, 3], card: [/最先进/], not: [/机翻/, /仅供参考/, /失败/] });
    // 词典给 data-driven 的那段发音是坏的（一串字母，将近 5 秒）：应该发现它太长，改成 data、driven 两段。哪天词典修好了，一段也对
    await scenario(b, '6-odd', 'odd', 'drag', { text: 'data-driven', clips: [1, 2], secs: [0.5, 3.4], card: [/数据驱动/], not: [/仅供参考/, /失败/] });

    // cache、set：词典给的这两段发音是 WAV，不是 MP3
    await scenario(b, '7-wav', 'wav', 'drag', { text: 'cache set', clips: [2], secs: [0.8, 4], mt: true, card: [/cache：/, /set：/], not: [/仅供参考/, /失败/] });
    // AUC：词典的那段发音只有 0.3 秒，没把字母念出来（有时干脆取不到）：应该改成 A、U、C 三个字母
    await scenario(b, '8-acronym', 'acronym', 'dblclick', { text: 'AUC', clips: [3], secs: [1, 5], card: [/abbr/], not: [/仅供参考/, /失败/] });
    // bi-encoder：bi- 是前缀，用词典里前缀的那一条（单独的 bi 是另一个词）
    await scenario(b, '9-prefix', 'prefix', 'drag', { text: 'bi-encoder', clips: [2], secs: [0.8, 4], mt: true, card: [/bi-：pref/, /encoder：.*编码器/], not: [/仅供参考/, /失败/] });

    const z = await rect(b, 'zh');
    await b.drag(z.x + 1, z.y + z.height / 2, z.right - 1, z.y + z.height / 2);
    await sleep(700);
    check('选中中文：不出现喇叭', (await b.run('String(getSelection())')).includes('中文') && await b.popup() === null);
    const w = await rect(b, 'word');
    await b.dblclick(w.x + w.width / 2, w.y + w.height / 2);
    const shown = await until(() => b.popup(), 2000);
    await b.click(500, 600);
    await sleep(500);
    check('点一下空白处：喇叭消失', !!shown && await b.popup() === null, shown ? '' : 'popup was not shown first');
    const errors = b.errors();
    check('页面上没有报错', !errors.length, errors.join(' | '));
  } finally {
    await b.close();
    server.close();
  }
}

main().catch(e => { failed++; console.log('FAIL ' + (e.stack || e)); }).finally(() => {
  for (const k of kids) k.kill();
  console.log(failed ? `${failed} failed` : 'all passed');
  process.exit(failed ? 1 : 0);
});
