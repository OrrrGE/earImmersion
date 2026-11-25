// 磨耳朵 · 页面脚本：选中英文 → 小喇叭 → 点击发音 + 中文释义
(() => {
  if (window.__moerduo) return;
  window.__moerduo = true;

  const MAX_LEN = 300;
  const NS = 'http://www.w3.org/2000/svg';
  const CSS = `
    :host{all:initial;position:fixed;z-index:2147483647;display:flex;align-items:flex-start;gap:6px;
      font:13px/1.5 system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
    :host([hidden]){display:none}
    button{all:unset;box-sizing:border-box;width:28px;height:28px;flex:none;display:grid;place-items:center;
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

  let host, btn, card, range, text = '', job, ctx, src, mx = 0, my = 0;
  const bufs = new Map(); // 文本 -> 已解码的音频

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

  function prefetch() {
    if (job && job.text === text) return job;
    const t = text;
    ctx = ctx || new AudioContext();
    const voice = bufs.has(t) ? Promise.resolve(bufs.get(t)) : send({ k: 'voice', q: t }).then(async b64 => {
      if (!b64) return null;
      const buf = await ctx.decodeAudioData(Uint8Array.from(atob(b64), c => c.charCodeAt(0)).buffer);
      if (bufs.size > 50) bufs.delete(bufs.keys().next().value);
      bufs.set(t, buf);
      return buf;
    }).catch(() => null);
    return (job = { text: t, voice, mean: send({ k: 'mean', q: t }) });
  }

  function show(mean, note) {
    card.textContent = '';
    if (mean && mean.phone) { const i = document.createElement('i'); i.textContent = `/${mean.phone}/`; card.append(i); }
    card.append(mean ? mean.lines.join('\n') : note);
    card.hidden = false;
    place();
  }

  async function speak() {
    const j = prefetch();
    if (card.hidden) show(null, '…');
    btn.classList.add('on');
    j.mean.then(m => job === j && show(m, '未找到释义'));
    const buf = await j.voice;
    if (job !== j) return;
    if (!buf) {
      btn.classList.remove('on');
      const m = await j.mean;
      if (job === j) show(m, '未找到释义'), card.append('\n（发音获取失败）');
      return;
    }
    try { src && src.stop(); } catch {}
    await ctx.resume();
    const s = (src = ctx.createBufferSource());
    s.buffer = buf;
    s.connect(ctx.destination);
    s.onended = () => src === s && btn.classList.remove('on');
    s.start();
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
