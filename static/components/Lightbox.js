// 全屏图片查看器：点击配图进入，支持缩放（滚轮/双击/按钮/键盘±）、拖拽平移、
// 左右切换（按钮/键盘 ←→）、Esc 关闭。
// 用法：<img-lightbox :img="zoom" @close="zoom=null" @save-text="onSaveText" />
// zoom = {src, title, text}（单张）或 {list: [{src,title,text,editable}...], index: n}（多张）
// list 项带 editable 时文案区显示「✎ 改文案」：textarea 保存后 emit('save-text', {index, text})
const ImgLightbox = {
  props: { img: { type: Object, default: null } },
  emits: ['close', 'save-text'],
  data() {
    return { idx: 0, scale: 1, tx: 0, ty: 0, dragging: false, sx: 0, sy: 0,
            editing: false, draft: '' };
  },
  computed: {
    list() {
      if (!this.img) return [];
      return this.img.list ? this.img.list : [this.img];
    },
    cur() {
      if (!this.list.length) return null;
      return this.list[Math.min(this.idx, this.list.length - 1)];
    },
    multi() { return this.list.length > 1; },
    transform() {
      return `translate(${this.tx}px, ${this.ty}px) scale(${this.scale})`;
    },
  },
  watch: {
    img(v) {
      this.idx = (v && v.index) || 0;
      this.resetView();
      this.editing = false;   // 切图退出编辑态
      if (v) {
        window.addEventListener('keydown', this.onKey);
        window.addEventListener('wheel', this.onWheel, { passive: false });
      } else {
        window.removeEventListener('keydown', this.onKey);
        window.removeEventListener('wheel', this.onWheel);
      }
    },
  },
  beforeUnmount() {
    window.removeEventListener('keydown', this.onKey);
    window.removeEventListener('wheel', this.onWheel);
  },
  methods: {
    resetView() { this.scale = 1; this.tx = 0; this.ty = 0; },
    prev() { if (this.idx > 0) { this.idx--; this.resetView(); } },
    next() { if (this.idx < this.list.length - 1) { this.idx++; this.resetView(); } },
    zoomAt(factor, cx, cy) {
      // 稳定缩放的关键：图片由 .lbv-mask flex 居中，屏幕点 p = C + t + s·u
      // （C=视口中心，t=平移，u=图内偏移）。保持光标下像素不动必须以 C 为原点补偿：
      // t' = (p−C) − k·((p−C) − t)，k=新缩放/旧缩放。
      // （旧版以视口左上角为原点，缩放时光标点会持续向左上漂移——2026-09-01 修复）
      const ns = Math.min(8, Math.max(0.2, this.scale * factor));
      if (ns === this.scale) return;
      const k = ns / this.scale;
      const px = cx - innerWidth / 2, py = cy - innerHeight / 2;
      this.tx = px - k * (px - this.tx);
      this.ty = py - k * (py - this.ty);
      this.scale = ns;
    },
    onWheel(e) {
      e.preventDefault();
      this.zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX, e.clientY);
    },
    onDblClick(e) {
      if (this.scale > 1.05) this.resetView();
      else this.zoomAt(2.5, e.clientX, e.clientY);
    },
    zoomBtn(f) {
      this.zoomAt(f, innerWidth / 2, innerHeight / 2);
    },
    onDown(e) {
      if (this.scale <= 1) return;    // 原尺寸时无需平移
      this.dragging = true;
      this.sx = e.clientX - this.tx;
      this.sy = e.clientY - this.ty;
      e.preventDefault();
      // 指针捕获：鼠标移出图片仍持续拖拽；同时天然支持触屏单指平移
      try { e.target.setPointerCapture(e.pointerId); } catch (_) { /* 旧浏览器 */ }
    },
    onMove(e) {
      if (!this.dragging) return;
      this.tx = e.clientX - this.sx;
      this.ty = e.clientY - this.sy;
      e.preventDefault();
    },
    onUp() { this.dragging = false; },
    onKey(e) {
      if (this.editing) return;   // 编辑文案时按键留给输入框
      if (e.key === 'Escape') this.$emit('close');
      else if (e.key === 'ArrowLeft') this.prev();
      else if (e.key === 'ArrowRight') this.next();
      else if (e.key === '0') this.resetView();
      else if (e.key === '+' || e.key === '=') this.zoomBtn(1.25);
      else if (e.key === '-') this.zoomBtn(1 / 1.25);
    },
    // ── 文案编辑（2026-09-01 吸收 8002）：editable 项可 ✎ 改文案 ──
    startEdit() {
      this.draft = this.cur.text || '';
      this.editing = true;
    },
    saveText() {
      const t = this.draft.trim();
      if (!t) { alert('文案不能为空'); return; }
      this.$emit('save-text', { index: this.idx, text: t,
                                page: (this.cur.title || '').match(/^P(\d+)/) });
      this.editing = false;
    },
  },
  template: `
  <div v-if="cur" class="lbv-mask" @click.self="scale <= 1 && $emit('close')">
    <button class="lb-close" @click="$emit('close')" title="关闭 (Esc)">✕</button>

    <div class="lbv-toolbar">
      <button class="lbv-btn" @click="zoomBtn(1/1.25)" title="缩小 (-)">−</button>
      <span class="lbv-zoom">{{ Math.round(scale * 100) }}%</span>
      <button class="lbv-btn" @click="zoomBtn(1.25)" title="放大 (+)">＋</button>
      <button class="lbv-btn" @click="resetView" title="还原 (0)">⟲</button>
    </div>

    <button v-if="multi" class="lb-nav lb-prev" :disabled="idx===0" @click="prev">‹</button>
    <button v-if="multi" class="lb-nav lb-next" :disabled="idx===list.length-1" @click="next">›</button>

    <img class="lbv-img" :src="cur.src" :key="cur.src" :style="{transform}"
         :class="{grab: scale > 1, dragging}"
         @dblclick="onDblClick"
         @pointerdown="onDown" @pointermove="onMove" @pointerup="onUp"
         @pointercancel="onUp" @mouseleave="onUp"
         alt="" draggable="false">

    <div class="lb-caption">
      <b>{{ cur.title }}</b>
      <span v-if="multi" class="lb-counter">{{ idx + 1 }} / {{ list.length }}</span>
      <template v-if="!editing">
        <a v-if="cur.editable" href="javascript:" class="lb-edit-link"
           @click="startEdit">✎ 改文案</a>
        <p v-if="cur.text">{{ cur.text }}</p>
      </template>
      <template v-else>
        <textarea v-model="draft" rows="4" class="lb-edit-area"
                  placeholder="输入新文案（≤200字），保存后可立即重画该页配图"></textarea>
        <div style="margin-top:6px;display:flex;gap:8px">
          <button class="btn btn-primary btn-sm" @click="saveText">保存文案</button>
          <button class="btn btn-outline btn-sm" @click="editing=false">取消</button>
          <span class="muted" style="font-size:12px;align-self:center">{{ draft.length }}/200</span>
        </div>
      </template>
      <span class="muted" style="font-size:11px">滚轮/双击缩放 · 拖拽平移 · ←→ 切换 · Esc 关闭</span>
    </div>
  </div>`,
};
