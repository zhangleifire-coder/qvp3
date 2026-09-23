// 系统管理（仅 admin）
const AdminView = {
  data() {
    return { st: null, error: '', msg: '', timer: null, logs: [], showLogs: false,
             costs: null, costTask: null,
             rates: null, balance: null, balanceLoading: false, ratesSaving: false,
             baselineForm: { deepseek: '', kimi: '', fusion: '' },
             ct: null, ctUrl: '', ctBusy: false, ctMsg: '', ctPreviews: {} };
  },
  computed: {
    isAdmin() { const u = getUser(); return u && u.role === 'admin'; },
    cycleText() {
      const c = this.st && this.st.cycle;
      if (!c) return '-';
      return c.mode === 'work' ? '工作中' : '检修停机';
    },
    remainText() {
      const c = this.st && this.st.cycle;
      if (!c || c.remaining_seconds == null) return '-';
      const s = Math.max(0, Math.round(c.remaining_seconds));
      const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
      return `${h}h ${m}m`;
    },
    statusCells() {
      const bs = (this.st && this.st.tasks && this.st.tasks.by_status) || {};
      return Object.keys(STATUS).map(k => ({ label: STATUS[k].label, cls: STATUS[k].cls, count: bs[k] || 0 }));
    },
  },
  methods: {
    async load() {
      try { this.st = await api.get('/api/admin/status'); this.error = ''; }
      catch (e) { this.error = e.message; }
    },
    async act(fn, confirmText) {
      if (confirmText && !confirm(confirmText)) return;
      this.error = ''; this.msg = '';
      try { await fn(); this.msg = '操作成功'; this.load(); }
      catch (e) { this.error = e.message; }
    },
    mtStart() { this.act(() => api.post('/api/admin/maintenance/start?actor=' + encodeURIComponent(getUser().name)), '确定进入手动检修？生产将暂停。'); },
    mtEnd() { this.act(() => api.post('/api/admin/maintenance/end?actor=' + encodeURIComponent(getUser().name)), '确定结束检修、恢复生产？'); },
    exportCsv() { location.href = '/api/admin/export?actor=' + encodeURIComponent(getUser().name); },
    async clearAll() {
      if (!confirm('危险操作：将删除全部任务与内容数据！确定继续？')) return;
      if (!confirm('再次确认：此操作不可恢复，确定清空？')) return;
      this.act(() => api.post('/api/admin/clear?actor=' + encodeURIComponent(getUser().name)));
    },
    async loadLogs() {
      this.showLogs = !this.showLogs;
      if (this.showLogs) {
        try { const r = await api.get('/api/admin/logs'); this.logs = r.logs || r || []; }
        catch (e) { this.error = e.message; }
      }
    },
    async loadCosts() {
      try { this.costs = await api.get('/api/admin/costs'); }
      catch (e) { this.error = e.message; }
    },
    toggleCostTask(id) { this.costTask = this.costTask === id ? null : id; },
    async loadCt() {
      try {
        const r = await api.get('/api/compose-templates');
        this.ct = r.templates || [];
        this.ct.forEach(t => this.previewCt(t.template_id));
      } catch (e) { this.ctMsg = '加载失败：' + e.message; }
    },
    async previewCt(tid) {
      try {
        const r = await api.post('/api/compose-templates/preview', { template_id: tid });
        this.ctPreviews = Object.assign({}, this.ctPreviews, { [tid]: r.preview_url + '?t=' + Date.now() });
      } catch (e) { this.ctPreviews = Object.assign({}, this.ctPreviews, { [tid]: null }); }
    },
    async toggleCt(t) {
      this.ctBusy = true; this.ctMsg = '';
      try {
        await api.patch('/api/compose-templates/' + t.template_id, { enabled: !t.enabled });
        t.enabled = !t.enabled;
      } catch (e) { this.ctMsg = '操作失败：' + e.message; }
      this.ctBusy = false;
    },
    async extractCt() {
      if (!this.ctUrl.trim()) return;
      this.ctBusy = true; this.ctMsg = 'VL 提取中…';
      try {
        const r = await api.post('/api/compose-templates/extract', { image_url: this.ctUrl.trim() });
        if (r.errors && r.errors.length) { this.ctMsg = 'spec 校验未过：' + r.errors.join('；'); }
        else {
          const tid = 'ext_' + Date.now().toString(36);
          await api.post('/api/compose-templates', {
            template_id: tid, name: '提取·' + (r.analysis.title_text || '').slice(0, 10),
            spec: r.spec, notes: r.spec.notes || '' });
          this.ctMsg = '已提取入库（默认停用），预览确认后启用';
          this.ctUrl = '';
          await this.loadCt();
        }
      } catch (e) { this.ctMsg = '提取失败：' + e.message; }
      this.ctBusy = false;
    },
    async loadRates() {
      try { this.rates = await api.get('/api/admin/rates'); }
      catch (e) { this.error = e.message; }
    },
    async saveRates() {
      if (!this.rates || !this.rates.rates) return;
      this.ratesSaving = true; this.error = ''; this.msg = '';
      try {
        await api.put('/api/admin/rates', {
          actor: getUser().name,
          rates: this.rates.rates.map(r => ({
            model_key: r.model_key, label: r.label || '',
            input_hit_peak: Number(r.input_hit_peak) || 0,
            input_miss_peak: Number(r.input_miss_peak) || 0,
            output_peak: Number(r.output_peak) || 0,
            offpeak_ratio: Number(r.offpeak_ratio) || 0,
            per_call_cny: Number(r.per_call_cny) || 0,
          })),
        });
        this.msg = '费率已保存并即时生效';
        await this.loadRates();
      } catch (e) { this.error = e.message; }
      finally { this.ratesSaving = false; }
    },
    async loadBalance() {
      this.balanceLoading = true;
      try { this.balance = await api.get('/api/admin/balance'); }
      catch (e) { this.error = e.message; }
      finally { this.balanceLoading = false; }
    },
    fmtNum(v) { return v == null ? '-' : Number(v).toFixed(2); },
    async saveBaseline(provider) {
      const v = Number(this.baselineForm[provider]);
      if (!(v >= 0)) { this.error = '请输入有效余额数字'; return; }
      this.error = ''; this.msg = '';
      try {
        await api.put('/api/admin/balance_baseline', {
          actor: getUser().name, provider, balance_cny: v });
        this.msg = provider + ' 余额基准已录入';
        this.baselineForm[provider] = '';
        await this.loadBalance();
      } catch (e) { this.error = e.message; }
    },
    estText(p) {
      if (!p) return '-';
      if (!p.ok) return p.error || '未录入基准';
      return '¥' + Number(p.estimated_balance_cny).toFixed(2);
    },
    fmtTime(s) { return s ? new Date(s).toLocaleString('zh-CN', { hour12: false }) : '-'; },
    fmtMoney(v) { return '¥' + Number(v || 0).toFixed(4); },
    costModeLabel(m) { return (typeof MODE !== 'undefined' && MODE[m]) ? MODE[m].label : (m || '-'); },
    costStatusLabel(s) { return (typeof STATUS !== 'undefined' && STATUS[s]) ? STATUS[s].label : (s || '-'); },
  },
  mounted() {
    if (!this.isAdmin) return;
    this.load(); this.loadCosts(); this.loadRates(); this.loadBalance(); this.loadCt();
    this.timer = setInterval(this.load, 3000);
  },
  beforeUnmount() { clearInterval(this.timer); },
  template: `
  <app-layout title="系统管理">
    <div v-if="!isAdmin" class="card empty">无权限：仅管理员可访问系统管理。</div>
    <template v-else>
      <p v-if="error" class="form-error">{{ error }}</p>
      <p v-if="msg" class="form-ok">{{ msg }}</p>
      <div class="grid grid-2">
        <div class="card">
          <h2>工作周期</h2>
          <p><span class="tag" :class="st && st.cycle && st.cycle.mode==='work' ? 'tag-green' : 'tag-yellow'">{{ cycleText }}</span></p>
          <p class="muted" style="margin-top:8px" v-if="st && st.cycle">
            距下一事件（{{ st.cycle.next_event || '-' }}）：{{ remainText }}<br>
            周期配置：工作 {{ st.cycle.work_hours }}h / 检修 {{ st.cycle.maintenance_hours }}h
            <span v-if="st.cycle.reason"><br>原因：{{ st.cycle.reason }}</span>
          </p>
          <div style="margin-top:12px;display:flex;gap:8px">
            <button class="btn btn-outline" @click="mtStart">手动检修</button>
            <button class="btn btn-outline" @click="mtEnd">结束检修</button>
          </div>
        </div>
        <div class="card">
          <h2>任务概览</h2>
          <div class="status-row">
            <div v-for="c in statusCells" :key="c.label" class="status-cell">
              <span class="tag" :class="c.cls">{{ c.label }}</span><span class="status-count">{{ c.count }}</span>
            </div>
          </div>
          <p class="muted" style="margin-top:10px" v-if="st && st.tasks">总数：{{ st.tasks.total }}</p>
        </div>
      </div>
      <div class="card">
        <h2>运维操作</h2>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn btn-outline" @click="exportCsv">导出 CSV</button>
          <button class="btn btn-outline" @click="loadLogs">{{ showLogs ? '收起日志' : '查看日志' }}</button>
          <a class="btn btn-outline" href="/api/admin/logs/download" style="text-decoration:none">下载日志</a>
          <button class="btn btn-danger" @click="clearAll">删除全部内容</button>
        </div>
        <pre v-if="showLogs" class="log-box">{{ logs.join('\\n') }}</pre>
      </div>

      <div class="card">
        <h2>页型模板库 <span class="muted" style="font-weight:normal;font-size:13px">混合合成链的程序版式；提取的模板默认停用，预览确认后启用</span></h2>
        <div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap;align-items:center">
          <button class="btn btn-outline btn-sm" @click="loadCt" :disabled="ctBusy">加载模板</button>
          <input v-model="ctUrl" placeholder="粘贴参考图 URL，自动提取为模板" style="flex:1;min-width:260px;padding:6px 10px">
          <button class="btn btn-primary btn-sm" @click="extractCt" :disabled="ctBusy || !ctUrl.trim()">从参考图提取</button>
        </div>
        <p v-if="ctMsg" class="muted" style="font-size:13px">{{ ctMsg }}</p>
        <div v-if="ct && ct.length" style="display:flex;gap:12px;flex-wrap:wrap">
          <div v-for="t in ct" :key="t.template_id" style="width:170px;border:1px solid #e5e5e5;border-radius:10px;padding:8px">
            <img v-if="ctPreviews[t.template_id]" :src="ctPreviews[t.template_id]" style="width:100%;border-radius:6px" alt="预览">
            <div v-else class="muted" style="height:200px;display:flex;align-items:center;justify-content:center;font-size:12px">预览生成中…</div>
            <div style="font-size:13px;margin-top:6px;font-weight:600">{{ t.name }}</div>
            <div class="muted" style="font-size:11px">{{ t.page_role }} · {{ t.source }}</div>
            <div style="margin-top:6px;display:flex;gap:6px;align-items:center">
              <button class="btn btn-sm" :class="t.enabled ? 'btn-outline' : 'btn-primary'" @click="toggleCt(t)" :disabled="ctBusy">
                {{ t.enabled ? '停用' : '启用' }}
              </button>
            </div>
          </div>
        </div>
        <p v-else class="muted">点击「加载模板」查看当前模板库（预置 12 个 seed 模板 + 提取模板）。</p>
      </div>

      <div class="card">
        <h2>余额手工校准 <span class="muted" style="font-weight:normal;font-size:13px">充值后在这里重新录入实际余额；录入后会覆盖 API 实拉值展示</span></h2>
        <div class="grid grid-3" v-if="balance">
          <div v-for="p in [['deepseek', 'DeepSeek'], ['kimi', 'Kimi'], ['fusion', 'FusionAI']]" :key="p[0]" style="display:flex;flex-direction:column;gap:6px">
            <div class="muted" style="font-size:13px">{{ p[1] }}</div>
            <div style="display:flex;gap:6px;align-items:center">
              <input type="number" step="0.01" min="0" v-model="baselineForm[p[0]]" :placeholder="'当前' + (balance[p[0]] && balance[p[0]].manual_balance ? '已校准 ¥' + fmtNum(balance[p[0]].manual_balance.balance_cny) : '未校准')" style="width:160px">
              <button class="btn btn-outline btn-sm" @click="saveBaseline(p[0])">保存</button>
            </div>
            <div class="muted" style="font-size:12px" v-if="balance[p[0]] && balance[p[0]].manual_balance">
              已校准 ¥{{ fmtNum(balance[p[0]].manual_balance.balance_cny) }}（{{ fmtTime(balance[p[0]].manual_balance.recorded_at) }}）
            </div>
          </div>
        </div>
      </div>

      <div class="card">
        <h2>费率与余额 <span class="muted" style="font-weight:normal;font-size:13px">费率改库/保存即生效（60s 内全进程刷新）；高峰=北京时间工作日 9:00-12:00、14:00-18:00，空闲按折扣价</span></h2>

        <!-- DeepSeek -->
        <div class="grid grid-4" style="margin:12px 0" v-if="balance">
          <div class="stat">
            <template v-if="balance.deepseek && balance.deepseek.manual_balance">
              <div class="n">¥{{ fmtNum(balance.deepseek.manual_balance.balance_cny) }}</div>
              <div class="l">DeepSeek 手工校准余额</div>
            </template>
            <template v-else>
              <div class="n" v-if="balance.deepseek && balance.deepseek.ok">¥{{ fmtNum(balance.deepseek.total_balance) }}</div>
              <div class="n" v-else style="color:#c00;font-size:16px">{{ (balance.deepseek && balance.deepseek.error) || '拉取失败' }}</div>
              <div class="l">DeepSeek 真实余额</div>
            </template>
          </div>
          <div class="stat">
            <template v-if="balance.deepseek && balance.deepseek.manual_balance && balance.deepseek.ok">
              <div class="n" style="font-size:16px">API 实拉 ¥{{ fmtNum(balance.deepseek.total_balance) }}</div>
              <div class="muted" style="font-size:12px">赠 ¥{{ fmtNum(balance.deepseek.granted_balance) }} / 充 ¥{{ fmtNum(balance.deepseek.topped_up_balance) }}</div>
            </template>
            <template v-else-if="balance.deepseek && balance.deepseek.ok">
              <div class="n" style="font-size:16px">赠 ¥{{ fmtNum(balance.deepseek.granted_balance) }} / 充 ¥{{ fmtNum(balance.deepseek.topped_up_balance) }}</div>
            </template>
            <div class="l">余额明细</div>
          </div>
          <div class="stat"><div class="n">{{ fmtMoney(balance.deepseek && balance.deepseek.daily_avg_7d_cny != null ? balance.deepseek.daily_avg_7d_cny : balance.ledger.daily_avg_7d_cny) }}</div><div class="l">近7天日均消耗</div></div>
          <div class="stat"><div class="n">{{ balance.deepseek && balance.deepseek.est_available_days != null ? balance.deepseek.est_available_days + ' 天' : '-' }}</div><div class="l">预计可用天数</div></div>
        </div>
        <p class="muted" style="font-size:13px" v-if="balance && balance.deepseek && (balance.deepseek.fetched_at || balance.deepseek.manual_balance)">
          <span v-if="balance.deepseek.manual_balance">手工校准于 {{ fmtTime(balance.deepseek.manual_balance.recorded_at) }}；</span>
          <span v-if="balance.deepseek.fetched_at">API 拉取于 {{ fmtTime(balance.deepseek.fetched_at) }}</span>
        </p>

        <!-- Kimi -->
        <div class="grid grid-4" style="margin:12px 0" v-if="balance">
          <div class="stat">
            <template v-if="balance.kimi && balance.kimi.manual_balance">
              <div class="n">¥{{ fmtNum(balance.kimi.manual_balance.balance_cny) }}</div>
              <div class="l">Kimi 手工校准余额</div>
            </template>
            <template v-else-if="balance.kimi && balance.kimi.ok">
              <div class="n">¥{{ fmtNum(balance.kimi.available_balance) }}</div>
              <div class="l">Kimi 真实余额</div>
            </template>
            <template v-else>
              <div class="n" style="color:#c00;font-size:16px">{{ (balance.kimi && balance.kimi.error) || '拉取失败' }}</div>
              <div class="l">Kimi 余额</div>
            </template>
          </div>
          <div class="stat">
            <template v-if="balance.kimi && balance.kimi.manual_balance && balance.kimi.ok">
              <div class="n" style="font-size:16px">API 实拉 ¥{{ fmtNum(balance.kimi.available_balance) }}</div>
              <div class="muted" style="font-size:12px">赠 ¥{{ fmtNum(balance.kimi.voucher_balance) }} / 充 ¥{{ fmtNum(balance.kimi.cash_balance) }}</div>
            </template>
            <template v-else-if="balance.kimi && balance.kimi.ok">
              <div class="n" style="font-size:16px">赠 ¥{{ fmtNum(balance.kimi.voucher_balance) }} / 充 ¥{{ fmtNum(balance.kimi.cash_balance) }}</div>
            </template>
            <div class="l">余额明细</div>
          </div>
          <div class="stat"><div class="n">{{ fmtMoney(balance.kimi && balance.kimi.daily_avg_7d_cny != null ? balance.kimi.daily_avg_7d_cny : 0) }}</div><div class="l">近7天日均消耗</div></div>
          <div class="stat"><div class="n">{{ balance.kimi && balance.kimi.est_available_days != null ? balance.kimi.est_available_days + ' 天' : '-' }}</div><div class="l">预计可用天数</div></div>
        </div>
        <p class="muted" style="font-size:13px" v-if="balance && balance.kimi && (balance.kimi.fetched_at || balance.kimi.manual_balance)">
          <span v-if="balance.kimi.manual_balance">手工校准于 {{ fmtTime(balance.kimi.manual_balance.recorded_at) }}；</span>
          <span v-if="balance.kimi.fetched_at">API 拉取于 {{ fmtTime(balance.kimi.fetched_at) }}</span>
          <span v-if="balance.kimi.rate">；单次计费：{{ balance.kimi.rate.model }} 输入命中 ¥{{ fmtNum(balance.kimi.rate.input_hit_peak) }}/1M tokens，输出 ¥{{ fmtNum(balance.kimi.rate.output_peak) }}/1M tokens</span>
        </p>

        <!-- FusionAI -->
        <div class="grid grid-4" style="margin:12px 0" v-if="balance">
          <div class="stat">
            <div class="n" v-if="balance.fusion && balance.fusion.ok">¥{{ fmtNum(balance.fusion.estimated_balance_cny) }}</div>
            <div class="n" v-else style="color:#c00;font-size:16px">{{ (balance.fusion && balance.fusion.error) || '未录入基准' }}</div>
            <div class="l">FusionAI 估算余额</div>
          </div>
          <div class="stat">
            <div class="n" style="font-size:16px" v-if="balance.fusion && balance.fusion.ok">基准 ¥{{ fmtNum(balance.fusion.baseline_cny) }} / 已消耗 ¥{{ fmtNum(balance.fusion.consumed_since_cny) }}</div>
            <div class="l">余额明细</div>
          </div>
          <div class="stat"><div class="n">{{ fmtMoney(balance.fusion && balance.fusion.daily_avg_7d_cny != null ? balance.fusion.daily_avg_7d_cny : 0) }}</div><div class="l">近7天日均消耗</div></div>
          <div class="stat"><div class="n">{{ balance.fusion && balance.fusion.est_available_days != null ? balance.fusion.est_available_days + ' 天' : '-' }}</div><div class="l">预计可用天数</div></div>
        </div>
        <p class="muted" style="font-size:13px" v-if="balance && balance.fusion">
          <span v-if="balance.fusion.ok">基准录入于 {{ fmtTime(balance.fusion.recorded_at) }}</span>
          <span v-else>{{ balance.fusion.note }}</span>
          <span v-if="balance.fusion.rate">；单次计费：{{ balance.fusion.rate.model }} 按次 ¥{{ fmtNum(balance.fusion.rate.per_call_cny) }}</span>
        </p>

        <p class="muted" style="font-size:13px" v-if="balance">
          台账口径：累计 {{ fmtMoney(balance.ledger.total_cny) }}，近24h {{ fmtMoney(balance.ledger.last_24h_cny) }}，近7天 {{ fmtMoney(balance.ledger.last_7d_cny) }}
        </p>
        <div style="margin:10px 0;display:flex;gap:8px">
          <button class="btn btn-outline btn-sm" @click="loadBalance" :disabled="balanceLoading">{{ balanceLoading ? '拉取中…' : '刷新余额' }}</button>
          <button class="btn btn-outline btn-sm" @click="loadRates">刷新费率</button>
        </div>
        <template v-if="rates">
          <p class="muted" style="font-size:13px" v-if="rates.source !== 'db'">⚠️ 费率表读取失败，当前显示代码兜底值：{{ rates.error }}</p>
          <table class="table">
            <thead><tr>
              <th>模型</th><th>说明</th><th>输入·命中<br>(元/1M·高峰)</th><th>输入·未命中</th>
              <th>输出</th><th>空闲折扣</th><th>按次(元)</th><th>更新时间</th>
            </tr></thead>
            <tbody><tr v-for="r in rates.rates" :key="r.model_key">
              <td class="muted">{{ r.model_key }}</td>
              <td><input v-model="r.label" style="width:180px"></td>
              <td><input type="number" step="0.01" min="0" v-model.number="r.input_hit_peak" style="width:80px"></td>
              <td><input type="number" step="0.01" min="0" v-model.number="r.input_miss_peak" style="width:80px"></td>
              <td><input type="number" step="0.01" min="0" v-model.number="r.output_peak" style="width:80px"></td>
              <td><input type="number" step="0.05" min="0" max="1" v-model.number="r.offpeak_ratio" style="width:70px"></td>
              <td><input type="number" step="0.05" min="0" v-model.number="r.per_call_cny" style="width:70px"></td>
              <td class="muted" style="font-size:12px">{{ fmtTime(r.updated_at) }}</td>
            </tr></tbody>
          </table>
          <div style="margin-top:10px">
            <button class="btn btn-primary btn-sm" @click="saveRates" :disabled="ratesSaving">{{ ratesSaving ? '保存中…' : '保存费率（即时生效）' }}</button>
          </div>
        </template>
      </div>

      <div class="card" v-if="costs">
        <h2>成本明细 <span class="muted" style="font-weight:normal;font-size:13px">全部计费事件逐项拆分，供成本决策（仅管理员可见）</span>
          <button class="btn btn-outline btn-sm" style="float:right" @click="loadCosts">刷新</button></h2>
        <div class="grid grid-4" style="margin:12px 0">
          <div class="stat"><div class="n">{{ fmtMoney(costs.summary.total_cny) }}</div><div class="l">累计总成本</div></div>
          <div class="stat"><div class="n">{{ fmtMoney(costs.summary.total_24h_cny) }}</div><div class="l">24h 成本</div></div>
          <div class="stat"><div class="n">{{ costs.summary.task_count }}</div><div class="l">计费任务数</div></div>
          <div class="stat"><div class="n">{{ fmtMoney(costs.summary.avg_per_task_cny) }}</div><div class="l">单任务平均成本</div></div>
        </div>
        <div class="grid grid-4" style="margin:0 0 12px" v-if="costs.by_category">
          <div class="stat" v-for="c in costs.by_category" :key="c.category">
            <div class="n">{{ fmtMoney(c.cost) }}</div>
            <div class="l">{{ c.label }}（{{ costs.summary.total_cny ? Math.round(c.cost / costs.summary.total_cny * 100) : 0 }}%）</div>
          </div>
        </div>
        <div class="grid grid-2">
          <div>
            <h3>按环节汇总</h3>
            <table class="table">
              <thead><tr><th>环节</th><th>计费次数</th><th>费用</th><th>占比</th></tr></thead>
              <tbody><tr v-for="n in costs.by_node" :key="n.node">
                <td>{{ n.label }}</td><td>{{ n.count }}</td><td>{{ fmtMoney(n.cost) }}</td>
                <td>{{ costs.summary.total_cny ? Math.round(n.cost / costs.summary.total_cny * 100) : 0 }}%</td>
              </tr></tbody>
            </table>
          </div>
          <div>
            <h3>按模型汇总</h3>
            <table class="table">
              <thead><tr><th>模型</th><th>费用</th><th>占比</th></tr></thead>
              <tbody><tr v-for="m in costs.by_model" :key="m.model">
                <td class="muted">{{ m.model }}</td><td>{{ fmtMoney(m.cost) }}</td>
                <td>{{ costs.summary.total_cny ? Math.round(m.cost / costs.summary.total_cny * 100) : 0 }}%</td>
              </tr></tbody>
            </table>
          </div>
        </div>
        <h3 style="margin-top:14px">按任务明细（点击行展开逐项费用）</h3>
        <div v-if="!costs.tasks.length" class="empty">暂无计费记录</div>
        <template v-else>
          <table class="table" style="margin-bottom:0">
            <thead><tr><th>Query</th><th>模式</th><th>状态</th><th>总费用</th><th></th></tr></thead>
          </table>
          <div v-for="t in costs.tasks" :key="t.task_id">
            <table class="table" style="margin-bottom:0">
              <tbody>
                <tr @click="toggleCostTask(t.task_id)" style="cursor:pointer">
                  <td class="q-cell">{{ t.query }}
                    <div class="muted" style="font-size:12px" v-if="t.by_category && t.by_category.length">
                      <span v-for="c in t.by_category" :key="c.category" style="margin-right:8px">{{ c.label }} {{ fmtMoney(c.cost) }}</span>
                    </div>
                  </td>
                  <td style="width:90px">{{ costModeLabel(t.mode) }}</td>
                  <td style="width:90px">{{ costStatusLabel(t.status) }}</td>
                  <td style="width:110px">{{ fmtMoney(t.total) }}</td>
                  <td class="muted" style="width:30px">{{ costTask === t.task_id ? '▲' : '▼' }}</td>
                </tr>
              </tbody>
            </table>
            <table v-if="costTask === t.task_id" class="table" style="background:#fafbfc">
              <thead><tr><th>环节</th><th>类别</th><th>模型/引擎</th><th>费用</th><th>完成时间</th></tr></thead>
              <tbody><tr v-for="(it, i) in t.items" :key="i">
                <td>{{ it.label }}</td><td class="muted">{{ it.category_label || '-' }}</td><td class="muted">{{ it.model || '-' }}</td>
                <td>{{ fmtMoney(it.cost) }}</td><td class="muted">{{ fmtTime(it.finished_at) }}</td>
              </tr></tbody>
            </table>
          </div>
        </template>
      </div>
    </template>
  </app-layout>`,
};
