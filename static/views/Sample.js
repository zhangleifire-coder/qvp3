// 随机抽查
const SampleView = {
  data() { return { item: null, error: '', loading: false, zoom: null }; },
  computed: {
    riskTag() {
      const lv = this.item && this.item.risk && this.item.risk.level;
      return RISK[lv] || { label: lv || '未知', cls: 'tag-gray' };
    },
    genAssets() {
      return ((this.item && this.item.assets) || []).filter(a => a.source_type !== 'official');
    },
    refAssets() {
      return ((this.item && this.item.assets) || []).filter(a => a.source_type === 'official');
    },
  },
  methods: {
    pageCopyOf(i) {
      const pcs = (this.item && this.item.page_copies) || [];
      const p = pcs.find(x => x.page_index === i);
      return p ? p.body : '';
    },
    zoomItems() {
      const gen = this.genAssets.map(a => ({
        src: a.display_url || a.image_url,
        title: `P${a.page_index} · AI 生成`,
        text: this.pageCopyOf(a.page_index),
      }));
      const ref = this.refAssets.map(a => ({
        src: a.display_url || a.image_url,
        title: `参考 ${a.page_index} · 实景抓取`,
        text: '',
      }));
      return gen.concat(ref);
    },
    openZoom(a, isRef) {
      const list = this.zoomItems();
      const src = a.display_url || a.image_url;
      const index = Math.max(0, list.findIndex(x => x.src === src));
      this.zoom = { list, index };
    },
    async draw() {
      this.loading = true; this.error = '';
      try {
        const r = await api.get('/api/tasks/random_sample');
        if (!r.ok) { this.item = null; this.error = r.error || '暂无已完成内容'; }
        else this.item = r;
      } catch (e) { this.error = e.message; this.item = null; }
      finally { this.loading = false; }
    },
  },
  mounted() { this.draw(); },
  template: `
  <app-layout title="随机抽查">
    <div class="card" style="display:flex;align-items:center;gap:12px">
      <button class="btn btn-primary" :disabled="loading" @click="draw">{{ loading ? '抽取中…' : '🎲 抽查一条' }}</button>
      <span class="muted">从已完成（待审核/已通过）内容中随机抽取一条做质量抽查</span>
    </div>
    <div v-if="error && !item" class="card empty">{{ error }}</div>
    <template v-if="item">
      <div class="card">
        <h2>{{ item.query }}</h2>
        <p>
          <span class="tag" :class="riskTag.cls">风险：{{ riskTag.label }}</span>
          <span class="tag tag-blue">{{ MODE[item.mode] ? MODE[item.mode].label : item.mode }}</span>
          <span class="muted" style="margin-left:8px">{{ item.content_type }} · 模型 {{ item.draft ? item.draft.model_version : '-' }}</span>
        </p>
        <div v-if="item.risk && item.risk.reasons && item.risk.reasons.length" style="margin-top:10px">
          <label>风险原因</label>
          <ul class="plain-list"><li v-for="r in item.risk.reasons" :key="r">{{ r }}</li></ul>
        </div>
      </div>
      <div class="card">
        <h2>正文</h2>
        <p class="article-body">{{ item.draft ? item.draft.body : '' }}</p>
      </div>
      <div class="card" v-if="genAssets.length">
        <h2>交付配图（{{ genAssets.length }}）</h2>
        <div class="img-grid">
          <figure v-for="a in genAssets" :key="a.page_index">
            <img :src="a.display_url || a.image_url" loading="lazy" alt="" @click="openZoom(a, false)">
            <figcaption class="muted">P{{ a.page_index }} · AI 生成</figcaption>
          </figure>
        </div>
      </div>
      <div class="card" v-if="refAssets.length">
        <h2>实景参考图（{{ refAssets.length }}）<span class="muted" style="font-weight:normal;font-size:13px">仅作生图参考，不随内容交付</span></h2>
        <div class="img-grid">
          <figure v-for="a in refAssets" :key="a.page_index">
            <img :src="a.display_url || a.image_url" loading="lazy" alt="" @click="openZoom(a, true)">
            <figcaption class="muted">参考 {{ a.page_index }} · 实景抓取</figcaption>
          </figure>
        </div>
      </div>
      <div class="grid grid-2">
        <div class="card">
          <h2>事实点（{{ item.claims.length }}）</h2>
          <ul class="plain-list">
            <li v-for="c in item.claims" :key="c.claim_text">{{ c.claim_text }} <span class="tag tag-gray">{{ c.risk_level }}</span></li>
          </ul>
        </div>
        <div class="card">
          <h2>证据来源（{{ item.evidences.length }}）</h2>
          <ul class="plain-list">
            <li v-for="e in item.evidences" :key="e.source_url"><a :href="e.source_url" target="_blank">{{ e.source_url }}</a><br><span class="muted">{{ e.excerpt }}</span></li>
          </ul>
        </div>
      </div>
    </template>
    <img-lightbox :img="zoom" @close="zoom=null" />
  </app-layout>`,
  created() { this.MODE = MODE; },
};
