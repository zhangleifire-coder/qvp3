// 图片放大浏览遮罩：点击配图打开，显示大图 + 对应分页文案
// 支持多张切换：左右按钮、键盘 ←/→ 翻页、Esc 关闭、页码计数
// 用法：<img-lightbox :img="zoom" @close="zoom=null" />
// zoom = {src, title, text}（单张）或 {list: [{src,title,text}...], index: n}（多张）
const ImgLightbox = {
  props: { img: { type: Object, default: null } },
  emits: ['close'],
  data() { return { idx: 0 }; },
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
  },
  watch: {
    img(v) {
      this.idx = (v && v.index) || 0;
      if (v) window.addEventListener('keydown', this.onKey);
      else window.removeEventListener('keydown', this.onKey);
    },
  },
  beforeUnmount() { window.removeEventListener('keydown', this.onKey); },
  methods: {
    prev() { if (this.idx > 0) this.idx--; },
    next() { if (this.idx < this.list.length - 1) this.idx++; },
    onKey(e) {
      if (e.key === 'Escape') this.$emit('close');
      else if (e.key === 'ArrowLeft') this.prev();
      else if (e.key === 'ArrowRight') this.next();
    },
  },
  template: `
  <div v-if="cur" class="lb-mask" @click.self="$emit('close')">
    <div class="lb-box">
      <button class="lb-close" @click="$emit('close')">✕</button>
      <button v-if="multi" class="lb-nav lb-prev" :disabled="idx===0" @click="prev">‹</button>
      <button v-if="multi" class="lb-nav lb-next" :disabled="idx===list.length-1" @click="next">›</button>
      <img class="lb-img" :src="cur.src" :key="cur.src" alt="">
      <div class="lb-caption">
        <b>{{ cur.title }}</b>
        <span v-if="multi" class="lb-counter">{{ idx + 1 }} / {{ list.length }}</span>
        <p v-if="cur.text">{{ cur.text }}</p>
      </div>
    </div>
  </div>`,
};
