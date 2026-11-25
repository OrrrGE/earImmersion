// 磨耳朵 · 后台：负责跨域取发音和释义（内容脚本受页面 CORS 限制，取不了）
const ACCENT = 2; // 2 = 美音，1 = 英音
const YD = 'https://dict.youdao.com/';
const DICTS = '{"count":99,"dicts":[["ec","fanyi","web_trans","aiDefinition"]]}';
const cache = new Map(); // "种类:文本" -> Promise

const norm = s => s.toLowerCase().replace(/[^a-z0-9]+/g, '');

// 查一个词或一句话，返回 {phone, lines} 或 null
async function lookupOne(q) {
  // 必须用 GET：POST 会带上 Origin: chrome-extension://…，被有道以 "Invalid CORS request" 拒绝
  const p = new URLSearchParams({ doctype: 'json', jsonversion: 4, q, le: 'en', client: 'web', keyfrom: 'webdict', dicts: DICTS });
  const d = await (await fetch(YD + 'jsonapi_s?' + p)).json();
  // 查不到时接口会返回一个无关词条，用 input 字段识别
  if (norm(d.input || '') !== norm(q)) return null;
  let w = (d.ec && d.ec.word) || {};
  if (Array.isArray(w)) w = w[0] || {};
  let lines = (w.trs || []).filter(t => t.tran).map(t => ((t.pos || '') + ' ' + t.tran).trim());
  if (!lines.length && d.ec && d.ec.web_trans) lines = [d.ec.web_trans.slice(0, 3).join('；')];
  if (!lines.length && d.fanyi && d.fanyi.tran) lines = [d.fanyi.tran];
  if (!lines.length && d.aiDefinition && d.aiDefinition.words && d.aiDefinition.words[0]) {
    const s = (d.aiDefinition.words[0].content || '').replace(/<[^>]+>/g, '').split(/[。\n]/)[0];
    if (s) lines = [s];
  }
  return lines.length ? { phone: w.usphone || w.ukphone || '', lines: lines.slice(0, 3) } : null;
}

async function lookup(q) {
  const r = await lookupOne(q).catch(() => null);
  if (r) return r;
  // 整体查不到：几个不相关的词一起选中时，逐词给释义
  const ws = [...new Set(q.split(/\s+/).map(w => w.replace(/^[^A-Za-z]+|[^A-Za-z]+$/g, '')).filter(Boolean))];
  if (ws.length < 2 || ws.length > 6) return null;
  const rs = await Promise.all(ws.map(w => lookupOne(w).catch(() => null)));
  const lines = ws.map((w, i) => rs[i] && `${w}：${rs[i].lines[0]}`).filter(Boolean);
  return lines.length ? { phone: '', lines } : null;
}

async function mp3(url) {
  const r = await fetch(url);
  if (!r.ok) return null;
  const b = new Uint8Array(await r.arrayBuffer());
  if (b.length < 500 || b[0] === 0x7b) return null; // 0x7b = "{"，是报错 JSON
  let s = '';
  for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
  return btoa(s);
}

// 先取有道真人发音（区分大小写，小写命中率高），取不到再用百度合成
async function voice(q) {
  const e = encodeURIComponent;
  const urls = [...new Set([q.toLowerCase(), q])].map(t => `${YD}dictvoice?audio=${e(t)}&type=${ACCENT}`);
  urls.push(`https://fanyi.baidu.com/gettts?lan=${ACCENT === 1 ? 'uk' : 'en'}&spd=3&source=web&text=${e(q)}`);
  for (const u of urls) {
    const a = await mp3(u).catch(() => null);
    if (a) return a;
  }
  return null;
}

chrome.runtime.onMessage.addListener((m, _sender, send) => {
  const fn = m && { mean: lookup, voice }[m.k];
  if (!fn || typeof m.q !== 'string') return;
  const key = m.k + ':' + m.q;
  let p = cache.get(key);
  if (!p) {
    p = fn(m.q).catch(() => null);
    cache.set(key, p);
    p.then(v => v || cache.delete(key)); // 失败不缓存，下次点击可重试
    if (cache.size > 200) cache.delete(cache.keys().next().value);
  }
  p.then(send);
  return true; // 异步应答
});
