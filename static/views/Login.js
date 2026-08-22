// 登录 / 注册
const LoginView = {
  data() {
    return { tab: 'login', username: '', password: '', role: 'A', error: '', loading: false };
  },
  methods: {
    async submit() {
      this.error = ''; this.loading = true;
      try {
        const isLogin = this.tab === 'login';
        const r = await api.post(isLogin ? '/api/auth/login' : '/api/auth/register',
          isLogin ? { username: this.username, password: this.password }
                  : { username: this.username, password: this.password, role: this.role });
        if (!r.ok) throw new Error(r.error || '操作失败');
        const u = { name: r.name || this.username, role: r.role || this.role };
        setUser(u);
        if (u.role === 'admin') this.$router.push('/admin');
        else if (['A', 'B', 'C'].includes(u.role)) this.$router.push('/review');
        else this.$router.push('/');
      } catch (e) { this.error = e.message; }
      finally { this.loading = false; }
    },
  },
  template: `
  <div class="login-page">
    <div class="card login-card">
      <img src="/static/logo.png" alt="logo" class="login-logo">
      <h1 class="login-title">图文生产平台</h1>
      <p class="muted login-sub">Query 智能生产 · 审核 · 产能验证</p>
      <div class="tabs">
        <button class="tab" :class="{on: tab==='login'}" @click="tab='login'; error=''">登录</button>
        <button class="tab" :class="{on: tab==='register'}" @click="tab='register'; error=''">注册</button>
      </div>
      <form @submit.prevent="submit">
        <label>用户名</label>
        <input v-model.trim="username" required placeholder="请输入用户名">
        <label>密码</label>
        <input v-model="password" type="password" required placeholder="请输入密码">
        <template v-if="tab==='register'">
          <label>审核角色</label>
          <select v-model="role">
            <option value="A">A · 文案事实</option>
            <option value="B">B · 图片版权</option>
            <option value="C">C · 合规交付</option>
          </select>
        </template>
        <p v-if="error" class="form-error">{{ error }}</p>
        <button class="btn btn-primary btn-block" :disabled="loading">{{ loading ? '提交中…' : (tab==='login' ? '登 录' : '注 册') }}</button>
      </form>
    </div>
  </div>`,
};
