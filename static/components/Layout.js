// 主框架：侧边栏 + 顶栏 + 内容插槽
const AppLayout = {
  props: { title: { type: String, default: '' }, inline: { type: Boolean, default: false } },
  data() { return { counts: {}, _t: null }; },
  computed: {
    user() { return getUser(); },
    menu() {
      const items = [
        { path: '/', icon: '📈', label: '工作台' },
        { path: '/import', icon: '📥', label: '任务导入' },
        { path: '/monitor', icon: '📡', label: '实时监控' },
        { path: '/textcheck', icon: '✍️', label: '文字核查', badge: 'text' },
        { path: '/refs', icon: '🖼️', label: '实景审图', badge: 'refs' },
        { path: '/review', icon: '📋', label: '任务审核', badge: 'review' },
        { path: '/tasks', icon: '🗂️', label: '任务中心' },
        { path: '/users', icon: '👥', label: '用户管理', admin: true },
        { path: '/admin', icon: '⚙️', label: '系统管理', admin: true },
      ];
      return items.filter(i => !i.admin || (this.user && this.user.role === 'admin'));
    },
  },
  methods: {
    roleName,
    doLogout() { logout(); },
    async loadCounts() {
      try { this.counts = (await api.get('/api/meta/review_counts')); }
      catch (e) { /* 静默 */ }
    },
  },
  mounted() { this.loadCounts(); this._t = setInterval(this.loadCounts, 10000); },
  beforeUnmount() { clearInterval(this._t); },
  template: `
  <div>
    <template v-if="inline"><slot /></template>
    <template v-else>
    <aside class="sidebar">
      <div class="brand"><img src="/static/logo.png" alt="logo" class="brand-logo">图文生产平台</div>
      <div class="menu-label">功能菜单</div>
            <router-link v-for="i in menu" :key="i.path" :to="i.path" class="menu-item" exact-active-class="active">
        <span class="mi">{{ i.icon }}</span>{{ i.label }}
        <span v-if="i.badge && counts[i.badge]" class="menu-badge">{{ counts[i.badge] }}</span>
      </router-link>
      <div class="sidebar-footer">
        <div class="user-box" v-if="user">
          <span class="role-badge">{{ user.role }}</span>
          <span class="name">{{ user.name }} · {{ roleName(user.role) }}</span>
          <router-link to="/settings" class="user-setting" title="我的设置（提示词库/风格库/改密）">⚙️ 设置</router-link>
          <button class="logout" @click="doLogout">退出</button>
        </div>
      </div>
    </aside>
    <div class="topbar"><div class="page-title">{{ title }}</div></div>
    <div class="content"><slot /></div>
    </template>
  </div>`,
};
