// API 封装 + 公共工具（全局）
window.esc = function (s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
};
window.getUser = function () { try { return JSON.parse(localStorage.getItem('qvp_user') || 'null'); } catch (e) { return null; } };
window.setUser = function (u) { localStorage.setItem('qvp_user', JSON.stringify(u)); };
window.logout = function () { localStorage.removeItem('qvp_user'); location.hash = '#/login'; };
window.roleName = function (r) { return { A: '文案事实', B: '图片版权', C: '合规交付', admin: '管理员' }[r] || r; };
window.fmtTime = function (iso) { if (!iso) return '-'; try { return new Date(iso).toLocaleString('zh-CN', { hour12: false }); } catch (e) { return iso; } };

// 缩略图（2026-09-10 性能优化）：网格/列表页用 384 宽 webp（~50KB）替代 2MB 原图；
// 原图只在灯箱点开大图时加载。本地资产走 /api/assets/<id>/thumb（后端缓存落盘），
// 外部 URL（参考图原链）无法缩略时原样返回。
window.thumbOf = function (a) {
  const d = (a && a.display_url) || '';
  const m = d.match(/^\/api\/assets\/([\w-]+)\/image$/);
  if (m) return `/api/assets/${m[1]}/thumb`;
  return (a && (a.display_url || a.image_url)) || '';
};

// 任务状态 / 模式 / 风险 的展示口径（全局唯一，避免各视图各写一份）
window.STATUS = {
  draft:      { label: '排队中', cls: 'tag-gray' },
  processing: { label: '生产中', cls: 'tag-blue' },
  awaiting_refs: { label: '待确认参考图', cls: 'tag-yellow' },
  awaiting_text: { label: '待人工核查', cls: 'tag-yellow' },
  review:     { label: '待审核', cls: 'tag-yellow' },
  approved:   { label: '已通过', cls: 'tag-green' },
  rejected:   { label: '已驳回', cls: 'tag-red' },
  failed:     { label: '失败',   cls: 'tag-red' },
  cancelled:  { label: '已中断', cls: 'tag-gray' },
};
window.MODE = {
  general: { label: '通用', desc: '通用科普/教程，纯文生图' },
  single:  { label: '单品', desc: '单一产品深测，搜实景图做图生图参考' },
  compare: { label: '对比', desc: '两主体对比，搜实景图做图生图参考' },
};
window.RISK = { green: { label: '绿', cls: 'tag-green' }, yellow: { label: '黄', cls: 'tag-yellow' }, red: { label: '红', cls: 'tag-red' } };

window.api = {
  async _req(method, url, body, form) {
    const opt = { method, headers: {} };
    if (form) { opt.body = form; }
    else if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    const res = await fetch(url, opt);
    let data = null;
    try { data = await res.json(); } catch (e) { /* 非 JSON 响应 */ }
    if (!res.ok) {
      let msg = data && (data.detail || data.error);
      if (Array.isArray(msg)) msg = msg.map(m => m.msg).join('; ');
      throw new Error(typeof msg === 'string' && msg ? msg : ('HTTP ' + res.status));
    }
    return data;
  },
  get(url) { return this._req('GET', url); },
  post(url, body) { return this._req('POST', url, body || {}); },
  postForm(url, formData) { return this._req('POST', url, undefined, formData); },
  put(url, body) { return this._req('PUT', url, body || {}); },
  patch(url, body) { return this._req('PATCH', url, body || {}); },
  delete(url) { return this._req('DELETE', url); },
};
