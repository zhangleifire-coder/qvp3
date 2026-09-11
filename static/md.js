// 迷你 Markdown 渲染器（2026-09-08）：正文校稿后含 #/## 小标题等标记，
// 页面按文档格式渲染而非裸显符号。无依赖、无构建，先转义 HTML 再转标记。
// 支持：#~#### 标题、**粗体**、__粗体__、行首 - / * 列表、空行分段。
function renderMd(text) {
  const esc = String(text ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
  const inline = s => s
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/__([^_]+)__/g, '<strong>$1</strong>');
  const out = [];
  for (const raw of esc.split('\n')) {
    const line = raw.trimEnd();
    const h = line.match(/^\s*(#{1,4})\s+(.*)$/);
    if (h) {
      const lv = h[1].length;
      out.push(`<div class="md-h md-h${lv}">${inline(h[2])}</div>`);
      continue;
    }
    const li = line.match(/^\s*[-*]\s+(.*)$/);
    if (li) { out.push(`<div class="md-li">${inline(li[1])}</div>`); continue; }
    if (!line.trim()) { out.push('<div class="md-gap"></div>'); continue; }
    out.push(`<div class="md-p">${inline(line)}</div>`);
  }
  return out.join('');
}
