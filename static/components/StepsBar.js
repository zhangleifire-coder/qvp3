// 13 节点步骤条
const StepsBar = {
  props: {
    nodes: { type: Array, default: () => [] },      // [{name,label}]
    completed: { type: Array, default: () => [] },  // [name]
    current: { type: String, default: null },
    failed: { type: Boolean, default: false },
  },
  methods: {
    state(n) {
      if (this.completed.includes(n.name)) return 'done';
      if (n.name === this.current) return this.failed ? 'fail' : 'doing';
      return 'todo';
    },
  },
  template: `
  <div class="steps">
    <div v-for="(n, i) in nodes" :key="n.name" class="step" :class="state(n)">
      <div class="dot">{{ state(n) === 'done' ? '✓' : (state(n) === 'fail' ? '✗' : i + 1) }}</div>
      <div class="step-label">{{ n.label }}</div>
    </div>
  </div>`,
};
