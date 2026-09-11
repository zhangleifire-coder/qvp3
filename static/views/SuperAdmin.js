// 超级管理控制台（2026-09-10）：模型供给配置 · 独立超级密码门禁
// 入口：设置页「超级管理」卡片（仅 admin）。会话 token 存 sessionStorage，
// 30 分钟滑动过期；全部接口要求 X-Super-Token 头（api.js 不带自定义头，
// 这里用本地 sreq 封装 fetch）。
const SuperAdminView = {
  data() {
    return {
      user: getUser() || {},
      token: sessionStorage.getItem('qvp_super_token') || '',
      // 门禁
      password: '', gateMsg: '', gateLoading: false,
      // 控制台
      fields: [], effective: null,
      dirty: {},          // key -> 新值（仅用户改过的字段）
      revealed: {},       // key -> 明文（查看后临时显示）
      switchMsg: '', configMsg: '',
      loading: false,
      customModel: '',    // 自定义主模型输入
      // 改超级密码
      pwOld: '', pwNew: '', pwNew2: '', pwMsg: '',
    };
  },
  computed: {
    isAdmin() { return this.user && this.user.role === 'admin'; },
    gatewayLabel() {
      if (!this.effective) return '';
      return { dsh: 'dsh_serve (:8901)', nanobot: 'Nanobot (:8900)' }[this.effective.gateway] || this.effective.gateway_url;
    },
    secretFields() { return this.fields.filter(f => f.kind === 'secret'); },
    plainFields() { return this.fields.filter(f => f.kind === 'plain'); },
    boolFields() { return this.fields.filter(f => f.kind === 'bool'); },
  },
  methods: {
    fmtTime: (iso) => (iso ? new Date(iso).toLocaleString() : ''),
    async sreq(method, url, body) {
      const opt = { method, headers: { 'X-Super-Token': this.token, 'Content-Type': 'application/json' } };
      if (body !== undefined) opt.body = JSON.stringify(body);
      const res = await fetch(url, opt);
      let data = {};
      try { data = await res.json(); } catch (e) { /* 空 body */ }
      if (res.status === 401) { this.token = ''; sessionStorage.removeItem('qvp_super_token'); throw new Error(data.detail || '超级会话已过期，请重新验证'); }
      if (!res.ok) throw new Error(data.detail || data.error || ('HTTP ' + res.status));
      return data;
    },
    async verify() {
      this.gateLoading = true; this.gateMsg = '';
      try {
        const res = await fetch('/api/superadmin/verify', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ actor: this.user.name, password: this.password }),
        });
        const data = await res.json();
        if (data.ok === false) { this.gateMsg = data.error || '验证失败'; return; }
        if (!res.ok) { this.gateMsg = data.detail || '验证失败'; return; }
        this.token = data.token;
        sessionStorage.setItem('qvp_super_token', this.token);
        this.password = '';
        await this.load();
      } catch (e) { this.gateMsg = e.message; }
      finally { this.gateLoading = false; }
    },
    async load() {
      this.loading = true;
      try {
        const d = await this.sreq('GET', `/api/superadmin/model-config?actor=${encodeURIComponent(this.user.name)}`);
        this.fields = d.fields; this.effective = d.effective;
        this.dirty = {}; this.revealed = {};
      } catch (e) { this.switchMsg = '加载失败：' + e.message; }
      finally { this.loading = false; }
    },
    // ── 保存修改 ──
    collectItems() {
      return Object.entries(this.dirty)
        .filter(([k, v]) => !(this.fields.find(f => f.key === k) || {}).kind === undefined)
        .map(([key, value]) => ({ key, value }));
    },
    async saveAll() {
      const items = Object.entries(this.dirty).map(([key, value]) => ({ key, value }));
      if (!items.length) { this.configMsg = '没有修改'; return; }
      try {
        const d = await this.sreq('PUT', '/api/superadmin/model-config', { actor: this.user.name, items });
        this.configMsg = `已保存并即时生效：${(d.changed || []).join('、') || '无'}`;
        this.dirty = {};
        await this.load();
      } catch (e) { this.configMsg = '保存失败：' + e.message; }
    },
    async reveal(f) {
      try {
        if (this.revealed[f.key]) { this.revealed[f.key] = ''; return; }
        const d = await this.sreq('POST', '/api/superadmin/reveal', { actor: this.user.name, key: f.key });
        this.revealed[f.key] = d.value;
      } catch (e) { this.switchMsg = e.message; }
    },
    // ── 快捷切换 ──
    async switchPrimary(model) {
      try {
        const d = await this.sreq('POST', '/api/superadmin/switch/primary-model', { actor: this.user.name, model });
        this.switchMsg = `主模型已切换：${d.old} → ${d.new}（即时生效）`;
        await this.load();
      } catch (e) { this.switchMsg = '切换失败：' + e.message; }
    },
    async setChannelPrimary(ch) {
      try {
        const d = await this.sreq('POST', '/api/superadmin/switch/channel-primary', { actor: this.user.name, channel: ch });
        this.switchMsg = `生图主通道已切到 ${ch}（顺序：${d.channels.join(' → ')}）`;
        await this.load();
      } catch (e) { this.switchMsg = '切换失败：' + e.message; }
    },
    async setGateway(t) {
      try {
        const d = await this.sreq('POST', '/api/superadmin/switch/gateway', { actor: this.user.name, target: t });
        this.switchMsg = `创作网关已切换：${d.old} → ${d.new}（即时生效）`;
        await this.load();
      } catch (e) { this.switchMsg = '切换失败：' + e.message; }
    },
    async toggleFallback(level, enabled) {
      try {
        await this.sreq('POST', '/api/superadmin/switch/fallback', { actor: this.user.name, level, enabled });
        this.switchMsg = `备用链 ${level === 'fallback1' ? '备1 Kimi-k3' : '备2 Kimi-Code'} 已${enabled ? '启用' : '停用'}（key 保留不变）`;
        await this.load();
      } catch (e) { this.switchMsg = '切换失败：' + e.message; }
    },
    // ── 改超级密码 ──
    async changePw() {
      this.pwMsg = '';
      if (this.pwNew.length < 8) { this.pwMsg = '新密码至少 8 位'; return; }
      if (this.pwNew !== this.pwNew2) { this.pwMsg = '两次输入的新密码不一致'; return; }
      try {
        await this.sreq('POST', '/api/superadmin/password',
                        { actor: this.user.name, old_password: this.pwOld, new_password: this.pwNew });
        this.pwMsg = '已修改，全部超级会话已注销——请重新验证';
        this.token = ''; sessionStorage.removeItem('qvp_super_token');
        this.pwOld = this.pwNew = this.pwNew2 = '';
      } catch (e) { this.pwMsg = '修改失败：' + e.message; }
    },
    logout() { this.token = ''; sessionStorage.removeItem('qvp_super_token'); },
  },
  async mounted() { if (this.token && this.isAdmin) await this.load(); },
  template: `
<div>
  <h2>🛡️ 超级管理 · 模型供给控制台</h2>
  <p class="muted" style="font-size:13px">
    独立于账号密码的第二道门禁；此处改动<b>即时生效</b>（网关均调用时读取）并持久化（重启不丢）。
    密钥明文查看会记入工作日志审计。
  </p>

  <div v-if="!isAdmin" class="card">⛔ 仅管理员账号可进入超级管理控制台。</div>

  <div v-else-if="!token" class="card" style="max-width:460px">
    <h3>验证超级密码</h3>
    <p class="muted" style="font-size:13px">
      首次进入时，此处输入的密码即被设为超级密码（之后每次进入需输入它）。
      与任何账号密码无关；连续错 5 次锁定 5 分钟。
    </p>
    <input type="password" v-model="password" placeholder="超级密码" @keyup.enter="verify" style="width:100%">
    <button class="btn" :disabled="gateLoading || !password" @click="verify" style="margin-top:8px">
      {{ gateLoading ? '验证中…' : '进入控制台' }}
    </button>
    <p v-if="gateMsg" class="muted" style="color:#c0392b;margin-top:8px">{{ gateMsg }}</p>
  </div>

  <template v-else>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
      <button class="btn btn-outline btn-sm" @click="logout">注销超管会话</button>
      <span v-if="loading" class="muted">加载中…</span>
    </div>
    <p v-if="switchMsg" class="muted" style="color:#1a6bb3">{{ switchMsg }}</p>

    <div v-if="effective" class="card" style="margin-bottom:12px">
      <b>当前生效：</b>
      主模型 <code>{{ effective.primary_model }}</code> ｜
      创作网关 <code>{{ gatewayLabel }}</code> ｜
      生图通道 <code>{{ effective.channels.join(' → ') }}</code> ｜
      备1 <code>{{ effective.fallback1_enabled ? '启用' : '停用' }}</code> ｜
      备2 <code>{{ effective.fallback2_enabled ? (effective.fallback2_has_key ? '启用' : '启用(未配key·自动跳过)') : '停用' }}</code>
    </div>

    <div class="card" style="margin-bottom:12px">
      <h3>⚡ 快捷切换</h3>
      <p style="margin:6px 0 4px"><b>文本主模型</b>
        <button class="btn btn-outline btn-sm" :class="{on: effective && effective.primary_model==='deepseek-v4-flash'}" @click="switchPrimary('deepseek-v4-flash')">deepseek-v4-flash</button>
        <button class="btn btn-outline btn-sm" :class="{on: effective && effective.primary_model==='deepseek-v4-pro'}" @click="switchPrimary('deepseek-v4-pro')">deepseek-v4-pro</button>
        <input v-model="customModel" placeholder="自定义模型名" style="width:200px" class="btn-sm">
        <button class="btn btn-outline btn-sm" :disabled="!customModel" @click="switchPrimary(customModel); customModel=''">切换到自定义</button>
      </p>
      <p style="margin:6px 0 4px"><b>生图主通道</b>
        <button v-for="ch in (effective ? effective.channels : [])" :key="ch"
                class="btn btn-outline btn-sm"
                :class="{on: effective && effective.channels[0]===ch}"
                @click="setChannelPrimary(ch)">{{ ch }} 设为主</button>
      </p>
      <p style="margin:6px 0 4px"><b>创作网关</b>
        <button class="btn btn-outline btn-sm" :class="{on: effective && effective.gateway==='dsh'}" @click="setGateway('dsh')">dsh_serve (:8901)</button>
        <button class="btn btn-outline btn-sm" :class="{on: effective && effective.gateway==='nanobot'}" @click="setGateway('nanobot')">Nanobot (:8900)</button>
        <span class="muted" style="font-size:12px">（协议 1:1，后端 NANOBOT_BASE_URL 即时指向）</span>
      </p>
      <p style="margin:6px 0 4px"><b>备用模型链</b>
        <button class="btn btn-outline btn-sm" @click="toggleFallback('fallback1', !(effective && effective.fallback1_enabled))">
          备1 Kimi-k3 {{ effective && effective.fallback1_enabled ? '停用' : '启用' }}
        </button>
        <button class="btn btn-outline btn-sm" @click="toggleFallback('fallback2', !(effective && effective.fallback2_enabled))">
          备2 Kimi-Code {{ effective && effective.fallback2_enabled ? '停用' : '启用' }}
        </button>
        <span class="muted" style="font-size:12px">（停用后该级不再参与降级，Key 保留不变）</span>
      </p>
    </div>

    <div class="card" style="margin-bottom:12px">
      <h3>🔑 API Key 与参数（空值/掩码提交 = 不修改；保存后即时生效）</h3>
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <tr v-for="f in secretFields" :key="f.key" style="border-top:1px solid #eee">
          <td style="padding:6px 8px;white-space:nowrap">{{ f.label }}</td>
          <td style="padding:6px 8px">
            <code v-if="!f.set" class="muted">未配置</code>
            <code v-else>{{ revealed[f.key] || f.masked }}</code>
          </td>
          <td style="padding:6px 8px">
            <button v-if="f.set" class="btn btn-outline btn-sm" @click="reveal(f)">{{ revealed[f.key] ? '隐藏' : '显示' }}</button>
          </td>
          <td style="padding:6px 8px;width:40%">
            <input v-model="dirty[f.key]" :placeholder="f.set ? '输入新 Key 覆盖' : '输入 Key 启用'" style="width:100%">
          </td>
        </tr>
        <tr v-for="f in plainFields" :key="f.key" style="border-top:1px solid #eee">
          <td style="padding:6px 8px;white-space:nowrap">{{ f.label }}</td>
          <td colspan="2" style="padding:6px 8px"><code>{{ f.value }}</code></td>
          <td style="padding:6px 8px">
            <input v-model="dirty[f.key]" :placeholder="f.value" style="width:100%">
          </td>
        </tr>
        <tr v-for="f in boolFields" :key="f.key" style="border-top:1px solid #eee">
          <td style="padding:6px 8px;white-space:nowrap">{{ f.label }}</td>
          <td colspan="3" style="padding:6px 8px">
            <label style="display:flex;align-items:center;gap:6px">
              <input type="checkbox" :checked="f.value" @change="dirty[f.key] = $event.target.checked ? 'true' : 'false'"> 启用
              <span v-if="dirty[f.key] !== undefined" class="muted">（待保存）</span>
            </label>
          </td>
        </tr>
      </table>
      <button class="btn" style="margin-top:10px" @click="saveAll">💾 保存修改（即时生效）</button>
      <span v-if="configMsg" class="muted" style="margin-left:10px">{{ configMsg }}</span>
    </div>

    <div class="card" style="max-width:460px">
      <h3>修改超级密码</h3>
      <input type="password" v-model="pwOld" placeholder="原超级密码" style="width:100%;margin-bottom:6px">
      <input type="password" v-model="pwNew" placeholder="新超级密码（至少 8 位）" style="width:100%;margin-bottom:6px">
      <input type="password" v-model="pwNew2" placeholder="确认新超级密码" style="width:100%;margin-bottom:6px">
      <button class="btn btn-outline" @click="changePw">修改并注销全部超管会话</button>
      <p v-if="pwMsg" class="muted" style="margin-top:8px">{{ pwMsg }}</p>
    </div>
  </template>
</div>
`,
};
