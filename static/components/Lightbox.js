// 全屏图片查看器：点击配图进入，支持缩放（滚轮/双击/按钮/键盘±）、拖拽平移、
// 左右切换（按钮/键盘 ←→）、Esc 关闭。
// 用法：<img-lightbox :img="zoom" @close="zoom=null" />
// zoom = {src, title, text}（单张）或 {list: [{src,title,text}...], index: n}（多张）
const ImgLightbox = {
  props: { img: { type: Object, default: null } },
  emits: ['close'],
  data() {
    return { idx: 0, scale: 1, tx: 0, ty: 0, dragging: false, sx: 0, sy: 0 };
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
      // 以视口坐标 (cx, cy) 为中心缩放：补偿平移让该点视觉不动
      const ns = Math.min(8, Math.max(0.2, this.scale * factor));
      if (ns === this.scale) return;
      const k = ns / this.scale;
      this.tx = cx - k * (cx - this.tx);
      this.ty = cy - k * (cy - this.ty);
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
    },
    onMove(e) {
      if (!this.dragging) return;
      this.tx = e.clientX - this.sx;
      this.ty = e.clientY - this.sy;
      e.preventDefault();
    },
    onUp() { this.dragging = false; },
    onKey(e) {
      if (e.key === 'Escape') this.$emit('close');
      else if (e.key === 'ArrowLeft') this.prev();
      else if (e.key === 'ArrowRight') this.next();
      else if (e.key === '0') this.resetView();
      else if (e.key === '+' || e.key === '=') this.zoomBtn(1.25);
      else if (e.key === '-') this.zoomBtn(1 / 1.25);
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
         @mousedown="onDown" @mousemove="onMove" @mouseup="onUp" @mouseleave="onUp"
         alt="" draggable="false">

    <div class="lb-caption">
      <b>{{ cur.title }}</b>
      <span v-if="multi" class="lb-counter">{{ idx + 1 }} / {{ list.length }}</span>
      <p v-if="cur.text">{{ cur.text }}</p>
      <span class="muted" style="font-size:11px">滚轮/双击缩放 · 拖拽平移 · ←→ 切换 · Esc 关闭</span>
    </div>
  </div>`,
};
