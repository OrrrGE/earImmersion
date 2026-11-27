// 磨耳朵 · 后台：负责跨域取发音和释义（内容脚本受页面 CORS 限制，取不了）。逻辑和桌面版 desktop/speak.py 一致。
const ACCENT = 2; // 2 = 美音，1 = 英音
const MT = true;  // 词典里没有的词组 / 生词 / 整句，交给机器翻译（MyMemory）；改成 false 就不会把选中的内容发给它
const YD = 'https://dict.youdao.com/';
const DICTS = '{"count":99,"dicts":[["ec","web_trans"]]}';
const MYMEMORY = 'https://api.mymemory.translated.net/get?';
const SEP = /[\s\-‐‑‒–—/_]+/; // 词与词之间：空白、各种连字符、斜杠、下划线
const STOP = new Set('a an the of to in on at for with by and or as is are be'.split(' ')); // 逐词给释义时略过的虚词
const EDGE = /^[\s"'“”‘’()[\]{}<>,;:!?*]+|[\s"'“”‘’()[\]{}<>,;:!?*]+$/g;

const norm = s => s.toLowerCase().replace(/[^a-z0-9]+/g, '');
const list = x => (Array.isArray(x) ? x : x ? [x] : []);
const uniq = a => [...new Set(a)];
const width = s => [...s].reduce((n, c) => n + (c.codePointAt(0) > 0x2e7f ? 2 : 1), 0); // 汉字算 2 列

// 去掉一个词两头的标点；e.g. / U.S. 这类自带句点的缩写保持原样
function strip(w) {
  w = w.replace(EDGE, '');
  const bare = w.replace(/\.+$/, '');
  return bare.includes('.') && !/^\d/.test(w) ? w : bare;
}
// 常见的前缀。词典里它们另有一条（"re-"），意思和读音都和碰巧同形的单词不一样：单独的 re 是"铼"，co 是"公司"，inter 是"埋葬"
const PREFIX = new Set(('re co bi de di un pre non sub semi multi anti self cross inter intra hyper super ultra micro macro nano mono ' +
  'poly pseudo quasi omni mid post auto tri uni').split(' '));

// 把选中的内容拆成一个个词：Utility-Oriented → Utility、Oriented。
// affix 为真：后面紧跟着连字符的前缀带上那个连字符（re-ranking → re-、ranking），好去查词典里前缀的那一条
function pieces(q, affix) {
  const xs = q.split(new RegExp(`(${SEP.source})`)); // 词、分隔符、词、分隔符……
  const ws = [];
  for (let i = 0; i < xs.length; i += 2) {
    const w = strip(xs[i]);
    if (w) ws.push([w, (xs[i + 1] || '').slice(0, 1)]);
  }
  return ws.map(([w, sep], i) => (affix && ['-', '‐', '‑'].includes(sep) && PREFIX.has(w.toLowerCase()) && i + 1 < ws.length ? w + '-' : w));
}
const base = w => (/^[A-Z]{2,}[’']?s$/.test(w) ? w.replace(/[’']?s$/, '') : w); // 全大写缩写的复数：LLMs → LLM

// 对每一项调用 fn（同时进行）。个别出错的记为 null；全都出错就把错误抛出去
async function together(fn, items) {
  const rs = await Promise.allSettled(items.map(fn));
  if (rs.length && rs.every(r => r.status === 'rejected')) throw rs[0].reason;
  return rs.map(r => (r.status === 'fulfilled' ? r.value : null));
}

// ───────── 释义：词典 + 机翻 ─────────

// 义项前面的学科标注：（计算机）、（博弈论，经济学）、（数）……
const FIELD = /^（[^（）]*(?:计算机|人工智能|机器学习|自然语言|数学|统计|逻辑|物理|化学|化工|生物|遗传|医学|心理学|语言学|经济|金融|博弈|电学|电子|通信|工程|天文|光学|力学|图形|图像|数据|算法)[^（）]*）|^（[数计医生化物电]）/;

// 词典里的一行释义可能很长：只留前面几个义项。排在后面、带学科标注的义项（"（计算机）转换器"）只要还放得下
// 也留着：读文献时要找的往往是它们
function brief(s, cols = 60) {
  s = s.replace(/<[^>]+>/g, '').split(/\s+/).join(' ').trim();
  const parts = s.match(/[^；;]+[；;]?/g) || [];
  let out = '';
  while (parts.length && !(out && width(out + parts[0]) > cols)) out += parts.shift();
  out = out.replace(/[；; ]+$/, '');
  for (let part of parts) {
    part = part.replace(/^[；; ]+|[；; ]+$/g, '');
    const m = part.match(FIELD);
    for (const p of m ? [part, part.replace(/（[^（）]{6,}）$/, '')] : []) { // 太长的话，去掉末尾括号里的说明再试一次
      if (p !== m[0] && width(out + '；' + p) <= cols * 1.5) {
        out += '；' + p;
        break;
      }
    }
  }
  if (width(out) > cols * 1.5) { // 一个义项就这么长（百科式的解释）：留冒号前面的词，没有冒号就截断
    const head = out.split('：')[0];
    if (head.length > 3 && head.length < out.length) return head;
    while (width(out) > cols) out = out.slice(0, -1);
    const cut = out.lastIndexOf('（');
    if (cut > 5 && !out.slice(cut).includes('）')) return out.slice(0, cut); // 别截在括号中间
    out += '…';
  }
  return out;
}

const entries = new Map(); // 词 -> Promise<词条 | null>

// 有道词典里 w 这一条 → {phone 音标, lines 释义, say 发音的键}；没有收录是 null
function entry(w) {
  let p = entries.get(w);
  if (!p) {
    p = fetchEntry(w);
    entries.set(w, p);
    p.catch(() => entries.delete(w)); // 出错不记，下次重查
    if (entries.size > 2000) entries.delete(entries.keys().next().value);
  }
  return p;
}

async function fetchEntry(w) {
  // 必须用 GET：POST 会带上 Origin: chrome-extension://…，被有道以 "Invalid CORS request" 拒绝
  const r = await fetch(YD + 'jsonapi?' + new URLSearchParams({ q: w, le: 'en', dicts: DICTS }));
  if (!r.ok) throw new Error('dictionary ' + r.status);
  const d = await r.json();
  try {
    if (norm(d.input || '') !== norm(w)) return null; // 答非所问的不要
    let word = list(d.ec && d.ec.word)[0] || {};
    const head = (((word['return-phrase'] || {}).l || {}).i) || w;
    if (pieces(w).length > 1 && head === head.toUpperCase() && /[A-Z]/.test(head) && !SEP.test(head) && head !== pieces(w).join('')) {
      return null; // ViT-S 查到的是另一个东西：缩写 VITS
    }
    let lines = list(word.trs).flatMap(t => list(t.tr)).flatMap(tr => list((tr.l || {}).i))
      .filter(i => typeof i === 'string' && i.trim());
    const plain = lines.filter(x => x[0] !== '《'); // 同名的书刊条目排在后面，有别的释义就不要它
    if (plain.length) lines = plain;
    if (lines.length && norm(head) !== norm(w)) lines[0] = `（${head}）${lines[0]}`; // 词典把它指到了原形上（logits → logit）
    const web = !lines.length;
    if (web) { // 词典正文里没有：看"网络释义"里有没有一模一样的词条。那是从网页上收集来的，不一定可靠，所以标出来
      word = {};
      const same = list(d.web_trans && d.web_trans['web-translation'])
        .find(x => x['@same'] === 'true' && norm(x.key || '') === norm(w));
      const vals = same ? list(same.trans).map(t => t.value).filter(v => typeof v === 'string') : [];
      if (vals.length) lines = ['（网络释义）' + uniq(vals).slice(0, 3).join('；')];
    }
    if (!lines.length) return null;
    const [us, uk] = ACCENT === 2 ? ['us', 'uk'] : ['uk', 'us'];
    return {
      phone: word[us + 'phone'] || word[uk + 'phone'] || word.phone || '',
      lines: lines.slice(0, 3).map(x => brief(x)),
      web,
      head,
      say: word[us + 'speech'] || word[uk + 'speech'] || word.speech || '',
    };
  } catch { return null; } // 应答的结构和预想的不一样：当作没查到
}

// "re-" 这样的前缀在词典里的那一条；词典里没有是 null
async function affix(w) {
  const e = await entry(w.toLowerCase());
  return e && e.head.endsWith('-') ? e : null;
}

// 查一个词；全大写缩写的复数（LLMs）查不到时改查单数 → [实际查到的词, 词条或 null]
async function gloss(w) {
  if (w.endsWith('-')) {
    const a = await affix(w);
    if (a) return [w, a];
    w = w.slice(0, -1);
  }
  const e = await entry(w);
  return !e && base(w) !== w ? [base(w), await entry(base(w))] : [w, e];
}

// 查释义 → {phone, lines, mt: 还要拿去机翻的原文（不需要就是空串）}
async function lookup(q) {
  const ws = pieces(q);
  if (ws.length > 6) return { phone: '', lines: [], mt: MT ? q : '' }; // 一句话：只能机翻
  let web = [];
  for (const cand of uniq([strip(q), ws.join(' ')])) { // 整个就是词典里的一条？（单词、fine-tuning、machine learning）
    const e = cand ? await entry(cand) : null;
    if (e && e.web && ws.length > 1) { // 词组只在"网络释义"里有（top-k 给的是"百度面试题"）：
      if (!web.length) web = e.lines.slice(0, 1); // 留着这一行，下面照样逐词解释、整体机翻
    } else if (e) return { phone: e.phone, lines: e.lines, mt: '' };
  }
  if (ws.length < 2) {
    const [w, e] = ws.length ? await gloss(ws[0]) : ['', null];
    if (e) return { phone: e.phone, lines: [`${w} 的复数`, ...e.lines.slice(0, 2)], mt: '' }; // LLMs：给出 LLM 的释义
    return { phone: '', lines: [], mt: MT ? ws.join(' ') : '' }; // 生词：只能机翻
  }
  // 词典里没有的复合词 / 词组：逐词给释义，整体交给机翻
  const words = uniq(pieces(q, true)).filter(w => !STOP.has(w.toLowerCase()) && !/^\d/.test(w));
  const got = words.length ? (await together(gloss, words)).filter(g => g && g[1]) : [];
  const phones = got.map(g => g[1].phone.split(';')[0].trim());
  const cols = got.length <= 3 ? 60 : 40; // 词少的时候多留几个义项：专业上的意思（retrieval 的"检索"）常常排在后面
  return {
    phone: got.length === ws.length && ws.length <= 4 && phones.every(Boolean) ? phones.join(' ') : '',
    lines: [...web, ...got.map(([w, e]) => `${w}：${brief(e.lines[0], cols)}`)],
    mt: MT ? ws.join(' ') : '',
  };
}

const unescape = s => s.replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(+n))
  .replace(/&(quot|amp|lt|gt|apos);/g, (_, n) => ({ quot: '"', amp: '&', lt: '<', gt: '>', apos: "'" })[n]);

// 机器翻译（MyMemory 的免费接口，匿名每天 5000 个字符）→ [译文, 说明]；没翻出来时译文是空串
async function translate(text) {
  const r = await fetch(MYMEMORY + new URLSearchParams({ q: text, langpair: 'en|zh-CN' }));
  const d = await r.json();
  const t = unescape(String((d.responseData || {}).translatedText || '')).trim();
  if (String(d.responseStatus) === '429' || d.quotaFinished || t.toUpperCase().includes('MYMEMORY WARNING')) {
    return ['', '今天的免费机翻额度用完了'];
  }
  if (String(d.responseStatus) !== '200' || !/[一-鿿]/.test(t) || norm(t) === norm(text)) return ['', '']; // 原样退回来的（专有名词）不算翻译
  return [t, ''];
}

// ───────── 发音：词典里的发音，一段段接起来；实在没有才用语音合成 ─────────

// 全大写的一串字母是不是一个个字母念的（LLM、GPT、OS），而不是当成一个词念（PLAID、NASA）
async function spelled(w) {
  if (!/^[A-Z]{2,6}$/.test(w)) return false;
  const e = await entry(w);
  if (e && e.phone) return e.phone.trim().includes(' '); // 音标里有空格 = 分开念的
  return !!(e && e.say) || stiff(w);
}

// 全大写的 w 没法当成一个单词读：三个字母以内，没有元音，或者辅音三个连在一起（RMSE）
const stiff = w => w.length <= 3 || !/[AEIOUY]/.test(w) || /[^AEIOUY]{3}/.test(w);

// 按大小写和数字把一个词拆开：ChatGPT → Chat、GPT；ColBERTv2 → Col、BERT、v、2。拆不开返回 []
function humps(w) {
  const sub = w.match(/[A-Z]{2,}(?![a-z]{2})|[A-Z]?[a-z]+|[A-Z]|\d+/g) || [];
  if (sub.length > 1 && sub[sub.length - 1] === 's' && /^[A-Z]{2,}$/.test(sub[sub.length - 2])) sub.pop(); // cFIMs：最后那个 s 是复数
  const joined = sub.join('');
  return sub.length > 1 && (joined === w || joined === w.slice(0, -1)) ? sub : [];
}

// 一个词怎么读 → [[[词典的发音键, 文本], …], 读音有没有把握]。发音键为空 = 交给语音合成去读文本。strict 见 plan
async function stepsFor(w, strict) {
  if (w.endsWith('-')) { // 前缀：词典里另有一条，有它自己的发音就用
    const a = await affix(w);
    if (a && a.say && a.phone) return [[[a.say, w]], true];
    w = w.slice(0, -1);
  }
  let e = await entry(w);
  if (!(e && e.say) && base(w) !== w) { // LLMs：词典里只有 LLM
    w = base(w);
    e = await entry(w);
  }
  const sub = humps(w);
  if (await spelled(w) && ((strict && stiff(w)) || !(e && e.say))) {
    return [[...w].map(c => [`${c}&type=${ACCENT}`, c]), true]; // 按字母读：26 个字母的发音是词典自己的
  }
  if (e && e.say) {
    // 有音标的是词典自己的发音。没有音标的那些是词典用语音合成读的，里面夹着缩写时会读错
    // （ChatGPT 只读出 chat，macOS 读成一个词）：这种拆开，缩写单独按字母读
    if (e.phone || !(sub.length && (await together(spelled, sub)).some(Boolean))) return [[[e.say, w]], true];
  }
  if (/^\d[\d.,]*$/.test(w)) return [[[`${w.replace(/,/g, '')}&type=${ACCENT}`, w]], true]; // 数字：词典的发音接口读得对（语音合成会把单个数字读错）
  if (sub.length) { // 驼峰、字母数字混排（PixelMatte、GPT4）：拆开，各读各的
    const rs = await together(x => stepsFor(x, strict), sub);
    return [rs.flatMap(r => (r ? r[0] : [])), !!e && rs.every(r => r && r[1])];
  }
  return [[['', w === w.toUpperCase() ? w.toLowerCase() : w]], false]; // 生词：语音合成按拼写去读
}

// 把 q 读出来的步骤 → [[[词典的发音键, 文本], …], 读音有没有把握]。
// 词典里有两种发音不一定靠得住，取下来之后要核对（见 limits 和 voice）：整条词组的发音（fine-tuning、state of the art），
// 和按字母念的缩写的发音（GPT、AUC：多半是词典用语音合成读的，有的没把字母念全）。
// strict 为真就是这两种都不用：词组逐词读，缩写一个字母一个字母地读
async function plan(q, strict) {
  let ws = pieces(q);
  if (ws.length > 1 && ws.length <= 6 && !strict) {
    for (const cand of uniq([strip(q), ws.join(' ')])) {
      const e = cand ? await entry(cand) : null; // 词典把它指到别的词形上的不算（language-modeling → language model）
      if (e && e.say && norm(e.head) === norm(cand)) return [[[e.say, cand]], true];
    }
  }
  ws = pieces(q, true);
  if (ws.length && ws.length <= 3) { // 短的：每个词都用词典里的发音，依次播放
    const rs = await together(w => stepsFor(w, strict), ws);
    return [ws.flatMap((w, i) => (rs[i] ? rs[i][0] : [['', w.replace(/-+$/, '')]])), rs.every(r => r && r[1])];
  }
  // 长的：整句交给语音合成（连字符换成空格，否则会读错）。按字母念的缩写它读不准，单独用词典里的发音
  let toks = q.split(SEP).filter(Boolean).join(' ').split(/\s+/).filter(Boolean);
  if (!toks.length) toks = [q];
  const shout = toks.filter(t => { const w = strip(t); return w === w.toUpperCase() && /[A-Z]/.test(w); }).length;
  if (shout >= 3 && shout * 3 > toks.length * 2) { // 通篇大写（标题）：不是缩写，转成小写再读
    return [[['', toks.join(' ').toLowerCase()]], true];
  }
  const caps = uniq(toks.map(t => base(strip(t))).filter(w => /^[A-Z]{2,6}$/.test(w)));
  const found = new Map();
  try {
    const yes = caps.length ? await together(spelled, caps) : [];
    const apart = caps.filter((_, i) => yes[i]);
    const rs = apart.length ? await together(w => stepsFor(w, strict), apart) : [];
    apart.forEach((w, i) => found.set(w, rs[i]));
  } catch {}
  const steps = [];
  let cur = [];
  for (const t of toks) {
    const r = found.get(base(strip(t)));
    if (r && r[1] && r[0].every(s => s[0])) {
      if (cur.length) steps.push(['', cur.join(' ')]);
      cur = [];
      steps.push(...r[0]);
    } else cur.push(t);
  }
  if (cur.length) steps.push(['', cur.join(' ')]);
  return [steps.length <= 14 ? steps : [['', toks.join(' ')]], true];
}

// 词典的这一段发音至少、至多该有多长（秒）。超出了，说明它靠不住（见 plan）
async function limits(key, text) {
  if (key && pieces(text).length > 1) return [0, 0.6 + 0.13 * text.length]; // 整条的词组：偶尔是一串字母，长得离谱（data-driven 将近 5 秒）
  if (key.split('&')[0].length > 1 && await spelled(text) && stiff(text)) {
    return [0.25 * [...text].reduce((n, c) => n + (c === 'W' ? 3 : 1), 0), 99]; // 缩写：太短 = 没把字母一个个念出来（AUC 只有 0.3 秒）
  }
  return [0, 99];
}

async function mp3(url) {
  const r = await fetch(url);
  if (!r.ok) return null;
  const b = new Uint8Array(await r.arrayBuffer());
  if (b.length < 500 || b[0] === 0x7b || b[0] === 0x3c) return null; // "{" 或 "<" 开头的是报错信息，不是音频
  let s = '';
  for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
  return btoa(s);
}

const clips = new Map(); // "键|文本" -> Promise<base64 | null>

// 取一段发音 → base64 的 mp3；取不到是 null。有发音键就取词典里的那一段，没有就让语音合成读 text
function clip(key, text) {
  const id = key + '|' + (key ? '' : text);
  let p = clips.get(id);
  if (!p) {
    const e = encodeURIComponent;
    const urls = key
      ? [YD + 'dictvoice?audio=' + encodeURI(key).replace(/%25/g, '%') + (key.includes('&type=') ? '' : '&type=' + ACCENT)]
      : [`https://fanyi.baidu.com/gettts?lan=${ACCENT === 1 ? 'uk' : 'en'}&spd=3&source=web&text=${e(text)}`,
         `${YD}dictvoice?audio=${e(text.toLowerCase())}&type=${ACCENT}`];
    p = (async () => {
      for (const u of urls) {
        const a = await mp3(u).catch(() => null);
        if (a) return a;
      }
      return null;
    })();
    clips.set(id, p);
    p.then(v => v || clips.delete(id)); // 失败不缓存，下次点击可重试
    if (clips.size > 300) clips.delete(clips.keys().next().value);
  }
  return p;
}

// q 的发音 → {clips: [要依次播放的音频（base64）…], sure: 读音有没有把握, limits: [每一段该有多长 [至少, 至多]…], patchy}；
// 一段都取不到是 null。长短要解码了才知道，所以由页面去核对：不对的话，页面会带着 strict 再要一次稳妥的读法。
// patchy 为真 = 词典的发音有没取到的，是凑合出来的读法（词典的发音时有时无）：这种不要记住，下次再试
async function voice(q, strict) {
  const fetchAll = steps => Promise.all(steps.map(([key, text]) => clip(key, text)));
  let [steps, sure] = await plan(q, strict);
  let got = await fetchAll(steps);
  if (!strict && steps.some(([key], i) => key && !got[i])) { // 词典的发音有的没取到：换成稳妥的读法
    strict = true;
    [steps, sure] = await plan(q, true);
    got = await fetchAll(steps);
  }
  let patchy = false;
  for (let i = 0; i < steps.length; i++) { // 还是没取到的：退而用语音合成，但读音就没把握了
    if (steps[i][0] && !got[i]) {
      got[i] = await clip('', steps[i][1]);
      sure = false;
      patchy = true;
    }
  }
  const kept = steps.filter((_, i) => got[i]);
  if (!kept.length) return null;
  return {
    clips: got.filter(Boolean),
    sure: sure && kept.length === steps.length,
    limits: strict ? [] : await Promise.all(kept.map(([key, text]) => limits(key, text))),
    patchy,
  };
}

const cache = new Map(); // "种类:文本" -> Promise

chrome.runtime.onMessage.addListener((m, _sender, send) => {
  const fn = m && { mean: lookup, voice, mt: translate }[m.k];
  if (!fn || typeof m.q !== 'string') return;
  const key = m.k + (m.strict ? '!' : ':') + m.q;
  let p = cache.get(key);
  if (!p) {
    p = fn(m.q, !!m.strict).catch(() => null); // null = 没连上
    cache.set(key, p);
    p.then(v => (v && !v.patchy) || cache.delete(key)); // 失败的、凑合的不缓存，下次点击可重试
    if (cache.size > 200) cache.delete(cache.keys().next().value);
  }
  p.then(send);
  return true; // 异步应答
});
