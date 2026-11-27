// 磨耳朵 · 页面脚本：选中英文 → 小喇叭 → 点击发音 + 中文释义
(() => {
  if (window.__moerduo) return;
  window.__moerduo = true;

  const MAX_LEN = 300;
  const GAP = 0.06; // 几段发音接着播时，段与段之间留多少秒
  const NS = 'http://www.w3.org/2000/svg';
  const CSS = `
    :host{all:initial;position:fixed;z-index:2147483647;display:flex;align-items:flex-start;gap:6px;
      font:13px/1.5 system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
    :host([hidden]){display:none}
    button{all:unset;box-sizing:border-box;width:24px;height:24px;flex:none;display:grid;place-items:center;
      cursor:pointer;border-radius:50%;background:#fff;color:#1a73e8;border:1px solid #d0d7de;
      box-shadow:0 2px 8px rgba(0,0,0,.18)}
    button:hover{background:#e8f0fe}
    button.on{color:#fff;background:#1a73e8;border-color:#1a73e8}
    svg{width:16px;height:16px;fill:currentColor}
    div{max-width:340px;padding:6px 10px;border-radius:8px;background:#fff;color:#1f2328;
      border:1px solid #d0d7de;box-shadow:0 2px 8px rgba(0,0,0,.18);white-space:pre-wrap;word-break:break-word}
    div[hidden]{display:none}
    i{font-style:normal;color:#656d76;display:block}
    @media (prefers-color-scheme:dark){
      button,div{background:#22272e;border-color:#444c56;color:#adbac7}
      button{color:#6cb6ff} button:hover{background:#2d333b} i{color:#8b949e}}`;

  let host, btn, card, range, text = '', job, ctx, playing = [], mx = 0, my = 0;
  const bufs = new Map(); // 文本 -> {parts: 已解码、去掉了首尾静音的各段音频, sure: 读音有没有把握}

  const clean = s => {
    s = s.replace(/([a-z])-\s*\n\s*([a-z])/g, '$1$2').replace(/\s+/g, ' ').trim(); // 合并 PDF 断行连字符
    if (!s || s.length > MAX_LEN) return '';
    const letters = (s.match(/[A-Za-z]/g) || []).length;
    return letters && letters * 2 >= s.replace(/\s/g, '').length ? s : ''; // 以英文为主才出现
  };

  const send = m => new Promise(res => {
    try { chrome.runtime.sendMessage(m, r => { void chrome.runtime.lastError; res(r || null); }); }
    catch { res(null); } // 扩展被重载后旧页面会走到这里
  });

  function build() {
    host = document.createElement('moerduo-pop');
    host.hidden = true;
    const root = host.attachShadow({ mode: 'closed' });
    const sheet = new CSSStyleSheet(); // 不受页面 CSP 限制
    sheet.replaceSync(CSS);
    root.adoptedStyleSheets = [sheet];
    btn = document.createElement('button');
    btn.title = '朗读';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    const path = document.createElementNS(NS, 'path');
    path.setAttribute('d', 'M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 7.97v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z');
    svg.append(path);
    btn.append(svg);
    card = document.createElement('div');
    card.hidden = true;
    root.append(btn, card);
    btn.addEventListener('mousedown', e => e.preventDefault()); // 点击时不丢失选区
    btn.addEventListener('pointerenter', prefetch);              // 悬停即预取，点击时更快
    btn.addEventListener('click', speak);
    document.documentElement.append(host);
  }

  function place() {
    let r;
    try { const rs = range.getClientRects(); r = rs[rs.length - 1] || range.getBoundingClientRect(); } catch {}
    if (!r || (!r.width && !r.height)) r = { right: mx, top: my, bottom: my }; // 输入框等取不到位置时用鼠标位置
    const w = host.offsetWidth, h = host.offsetHeight;
    const x = Math.max(4, Math.min(r.right + 4, innerWidth - w - 8));
    const y = r.bottom + 6 + h > innerHeight ? r.top - h - 6 : r.bottom + 6;
    host.style.left = x + 'px';
    host.style.top = y + 'px';
  }

  function hide() { if (host) host.hidden = true; }

  function check() {
    const sel = getSelection();
    const t = sel && sel.rangeCount ? clean(sel.toString()) : '';
    if (!t) return hide();
    if (!host) build();
    if (!host.isConnected) document.documentElement.append(host);
    range = sel.getRangeAt(sel.rangeCount - 1).cloneRange();
    if (t !== text || host.hidden) {
      text = t;
      card.hidden = true;
      btn.classList.remove('on');
    }
    host.hidden = false;
    place();
  }

  // 词典的发音前后各有零点几秒的静音：找出有声音的那一段，这样开口快，几个词接着读也不会一顿一顿的
  function trim(buf) {
    const n = buf.length, rate = buf.sampleRate, chans = [];
    for (let c = 0; c < buf.numberOfChannels; c++) chans.push(buf.getChannelData(c));
    let peak = 0, a = n, z = 0;
    for (const d of chans) for (let i = 0; i < n; i++) if (Math.abs(d[i]) > peak) peak = Math.abs(d[i]);
    const floor = Math.max(peak * 0.01, 0.0005); // 比最响处低 40 分贝以上的算静音
    for (const d of chans) {
      let i = 0, k = n - 1;
      while (i < a && Math.abs(d[i]) <= floor) i++;
      while (k > z && Math.abs(d[k]) <= floor) k--;
      a = Math.min(a, i);
      z = Math.max(z, k);
    }
    if (a >= z) return { buf, from: 0, len: buf.duration };
    const from = Math.max(0, a / rate - 0.03), to = Math.min(buf.duration, z / rate + 0.08);
    return { buf, from, len: to - from };
  }

  // 取 t 的发音并解码 → {parts, sure}；取不到是 null
  async function load(t, strict) {
    const v = await send({ k: 'voice', q: t, strict });
    if (!v) return null;
    const parts = [];
    for (const b64 of v.clips) {
      parts.push(trim(await ctx.decodeAudioData(Uint8Array.from(atob(b64), c => c.charCodeAt(0)).buffer)));
    }
    // 词典里有些发音靠不住：整条词组的偶尔是一串字母（data-driven 将近 5 秒），缩写的常常没把字母念全（AUC 只有 0.3 秒）。
    // 长短对不上，就改要稳妥的读法：词组逐词读，缩写按字母读
    if (!strict && v.limits.some(([lo, hi], i) => parts[i].len < lo || parts[i].len > hi)) return load(t, true);
    return { parts, sure: v.sure, patchy: v.patchy };
  }

  // 一次"取数据"：m 释义、v 发音、r 机翻。undefined = 还没回来，null = 没取到
  function prefetch() {
    if (job && job.text === text) return job;
    const t = text, j = { text: t };
    ctx = ctx || new AudioContext();
    j.voice = bufs.has(t) ? Promise.resolve(bufs.get(t)) : load(t).then(v => {
      if (!v || v.patchy) return v; // 凑合出来的读法不记，下次再试
      if (bufs.size > 50) bufs.delete(bufs.keys().next().value);
      bufs.set(t, v);
      return v;
    }).catch(() => null);
    j.voice.then(v => { j.v = v; update(j); });
    send({ k: 'mean', q: t }).then(m => { j.m = m; update(j); });
    return (job = j);
  }

  // 数据回来了：已经点过喇叭的话，刷新卡片；需要机翻就去翻（只是鼠标移上去的时候不翻，免得白白用掉额度）
  function update(j) {
    if (job !== j || !j.clicked) return;
    if (j.m && j.m.mt && !j.asked) {
      j.asked = true;
      send({ k: 'mt', q: j.m.mt }).then(r => { j.r = r; update(j); });
    }
    const m = j.m, lines = [];
    card.textContent = '';
    if (m && m.phone) { const i = document.createElement('i'); i.textContent = `/${m.phone}/`; card.append(i); }
    if (m && m.mt) { // 机翻的结果晚一步到：先占一行
      lines.push(j.r === undefined ? '机翻：…' : !j.r ? '（机翻没连上）' : j.r[0] ? '机翻：' + j.r[0] : j.r[1] ? `（${j.r[1]}）` : '');
    }
    if (m) lines.push(...m.lines);
    let body = lines.filter(Boolean);
    if (!body.length && !(m && m.phone)) {
      body = [m === undefined ? '…' : m === null ? '查词失败，请检查网络' : '词典里没有收录（多半是专有名词）'];
    }
    if (j.v && !j.v.sure) body.push('（读音是按拼写推测的，仅供参考）');
    if (j.v === null) body.push('（发音获取失败，请检查网络）');
    card.append(body.join('\n'));
    card.hidden = false;
    place();
  }

  function stop() {
    for (const s of playing) { s.onended = null; try { s.stop(); } catch {} }
    playing = [];
  }

  async function speak() {
    const j = prefetch();
    j.clicked = true;
    btn.classList.add('on');
    update(j);
    const v = await j.voice;
    if (job !== j) return;
    if (!v) return btn.classList.remove('on');
    stop();
    ctx.resume().catch(() => {}); // 不等它：没有声音输出设备的电脑上，它永远等不到
    let t = ctx.currentTime + 0.03;
    const mine = playing = v.parts.map(p => { // 一段接一段，事先排好各自的开始时刻
      const s = ctx.createBufferSource();
      s.buffer = p.buf;
      s.connect(ctx.destination);
      s.start(t, p.from, p.len);
      t += p.len + GAP;
      return s;
    });
    const off = () => playing === mine && btn.classList.remove('on');
    mine[mine.length - 1].onended = off;
    setTimeout(off, (t - ctx.currentTime) * 1000 + 1500); // 声音没能放出来的话，喇叭也不要一直亮着
  }

  addEventListener('mouseup', e => {
    if (e.target === host) return;
    mx = e.clientX; my = e.clientY;
    setTimeout(check, 10); // 等浏览器更新完选区
  }, true);
  addEventListener('keyup', e => { if (e.shiftKey || e.key === 'Shift') setTimeout(check, 10); }, true);
  addEventListener('mousedown', e => { if (e.target !== host) hide(); }, true);
  addEventListener('scroll', () => { if (host && !host.hidden) place(); }, { capture: true, passive: true });
})();
