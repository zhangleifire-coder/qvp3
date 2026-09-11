# 生图能力升级规划 · 借鉴 awesome-gpt-image-2

> 编写日期：2026-09-10 ｜ 状态：规划（未开工）
> 借鉴对象：`freestylefly/awesome-gpt-image-2`（29.9k stars，MIT）
> https://github.com/freestylefly/awesome-gpt-image-2
> 关键资产：`data/cases.json`（541 案例）/ `data/style-library.json`（19 风格标签 + 22 模板，
> 模板 schema = useWhen/guidance/pitfalls/exampleCases 中英双语）/
> `agents/skills/gpt-image-2-style-library/SKILL.md` / `scripts/generate-style-skill.mjs`（数据→skill 生成管线）
> 版权口径：**用户决策 2026-09-10——程序仅内部测试使用，不引入商用合规门槛**，可直接改编其模板/案例文本。
> 前置阅读：《技术交接文档-20260907.md》§5.3（提示词多路径纪律）、§11（服务器红线）、
> 《风格库训练方法总结.md》（风格条目录入流程）、《生图学习-借鉴图片丰富度训练-20260824.md》。

---

## 1. 目标与非目标

**目标**：用开源库的「结构化模板 schema + 单一数据源生成管线」补充现有生图体系，
同时修正下列已知缺陷。

**非目标（本轮不做）**：
- 不动 `shared_style.txt` / `constraints_en.txt` 跨风格铁律底座（3:4、逐字渲染、80-130 字
  字数契约归 `page-split/contract.txt` 管，有守卫断言锁定）；
- 不动 6 页排版轮换机制（`layouts_cn/en.txt` + fragment_list 按页取模）——只允许「内容级」
  蒸馏增强，且必须过 `verify_skill_equivalence.py`；
- 不动配额/记账/三道人工关卡/MOCK 开关；
- 不引入 GPT Image 2.5（Sunburst/Flare）——四通道中转商是否支持未验证，单独立项。

## 2. 现有缺陷清单（本规划要修的）

| # | 缺陷 | 现状证据 | 由哪个工作流修 |
|---|---|---|---|
| D1 | 风格库规模小、描述是散文段，选型靠 keywords 子串硬猜 | `style_keywords` 公共库 ~2 条种子+运行期条目，代码兜底 10 条（combo.py 硬编码）；description 无结构 | WS1/WS3 |
| D2 | 风格专属避坑知识没有随风格走 | 散落在 docs 血泪史、守卫断言、底座全局条款；「坚韧被画成石缝发芽」类教训只能靠人记 | WS1 |
| D3 | 风格知识四处副本，阴阳口径风险 | DB 条目 / `combo.py IMAGE_STYLE_LIBRARY` / 迁移 019 种子 / docs 四处各一份 | WS3 |
| D4 | 生图通道无备胎 | backlog 明示：`.env` 的 `IMAGE_GEN_CHANNELS` 仅 fusion，主通道挂了全失败（代码已支持 fusion/linkai/moacode/openox 轮询） | P0 |
| D5 | 版式指导粒度粗，无「模板层」 | 6 页只有页角色轮换，无按题材类型的版式/防坑模板 | WS2 |
| D6 | 改提示词/风格后缺固定质量基准，好坏靠单次人工审图 | 无基准集，每次回归凭感觉 | WS4 |

## 3. 总体设计：四条工作流

```
WS3 单一事实来源（先做，地基）
  data/styles.json（唯一手编的公共风格权威源）
     ├─生成→ init_db 幂等同步公共基线（owner_id IS NULL）
     ├─生成→ combo.py 内置兜底（改为读 JSON，删除硬编码 10 条）
     └─生成→ docs 风格总表（可选）
  守卫：tests/unit/test_style_ssot.py（重生成→diff 必须为空）

WS1 风格条目结构升级（迁移 021）
  style_keywords += use_when / pitfalls / source 三列
  → style_select 选型打分吃 use_when
  → build_style_block / Agent 注入段拼「适用/忌讳」
  → 管理页与 CSV 导入扩字段

WS2 模板蒸馏（素材工程，不改架构）
  从开源 22 模板 + 541 案例中蒸馏「信息图表/科普海报/文档出版物」
  三类与小红书科普卡片相邻的 guidance/pitfalls 写法
  → 产出 5-8 条新公共风格 + 既有条目的 use_when/pitfalls 内容

WS4 案例基准集（质量门）
  从开源案例 + 历史交付任务抽 20 题固定基准
  → 每次改风格/提示词后本地 8005 小样批跑 + 人工快审
```

依赖关系：**WS3 → WS1 → WS2 → WS4**（WS4 可与 WS2 并行）。

## 4. 分阶段实施

### P0 快赢 + 安全网（0.5 天）

1. **D4 修复**：`.env` 的 `IMAGE_GEN_CHANNELS=fusion,linkai,moacode`
   （代码已支持轮询，纯配置）。验收：mock 断 fusion 通道自动切 linkai。
2. **版本安全网**：当前 `code/` 无 git 仓库。开工前做一次快照备份
   （`git init && git add -A && git commit` 或 zip 快照），此后每个 Phase 一提交。
   （交接文档口径：工作副本无 git，版本以文档为准——本轮改动跨迁移+多处源码，
   没有版本控制风险不可接受。）
3. **拉取素材**：`git clone --depth 1 https://github.com/freestylefly/awesome-gpt-image-2`
   到 `code/.vendor/awesome-gpt-image-2/`（加进 .gitignore，仅作素材池，不进镜像）。

### P1 WS3 单一事实来源管线（1 天）

**权威源文件** `data/styles.json`（仓库根新建，schema 即 WS1 目标结构，字段先空着）：

```json
{
  "version": 1,
  "styles": [
    {
      "style_name": "新中式史料卡",
      "keywords": ["历史", "文物", "古籍"],
      "use_when": "题材含历史/传统/史料时优先",
      "description": "（现有 019 种子的整段描述迁入）",
      "pitfalls": "",
      "source": "seed_019"
    }
  ]
}
```

**改动点**：
- 新增 `scripts/sync_styles.py`：读 `data/styles.json`，对 `owner_id IS NULL` 的公共条目
  做 `ON CONFLICT (style_name) DO UPDATE` 幂等同步；`init_db.py` 末尾调用。
  **纪律：公共库改动一律改 styles.json，管理页只管个人库与临时停用（enabled=false）**，
  否则重跑同步会冲掉管理页改动（本技术最大的坑，先立规矩）。
- `combo.py` 的 `IMAGE_STYLE_LIBRARY` 硬编码 10 条删除，改为启动时读
  `data/styles.json`（读不到才用内联最小兜底 1 条）。
- 迁移 019 种子保留不动（历史幂等迁移不回改），由 sync 覆盖为其 json 版本。

**守卫**：新增 `tests/unit/test_style_ssot.py`——
①调用 sync 的纯函数生成期望态，与 `data/styles.json` 断言一致；
②`combo.py` 兜底列表 == json 中 enabled 公共条目；③json schema 校验（必备字段/长度上限）。

验收：四处副本归一；`pytest tests/unit/test_style_ssot.py` 绿；本地起后端公共库条目与 json 一致。

### P2 WS1 字段化（1-2 天）

**迁移 `migrations/021_style_use_when_pitfalls.sql`**（幂等，沿 020 风格）：

```sql
-- 021: 风格条目结构升级（2026-09-10）
-- use_when=适用题材条件（喂选型），pitfalls=风格专属避坑（喂提示词负面约束）
-- source=条目来源（manual/seed_019/oss_distilled），便于审计与回滚
ALTER TABLE style_keywords ADD COLUMN IF NOT EXISTS use_when  TEXT NOT NULL DEFAULT '';
ALTER TABLE style_keywords ADD COLUMN IF NOT EXISTS pitfalls  TEXT NOT NULL DEFAULT '';
ALTER TABLE style_keywords ADD COLUMN IF NOT EXISTS source    TEXT NOT NULL DEFAULT 'manual';
```

**代码改动点**（全部小改）：
- `src/models/styles.py`：三个新列映射。
- `src/services/style_select.py`：
  - 打分函数：keywords 命中数 + use_when 命中数（use_when 权重减半，避免长文本淹没 keywords）；
  - `build_style_block`：拼为「（本篇视觉风格：{name}）{description}。适用：{use_when}。
    本风格忌讳：{pitfalls}。本篇全部页面必须严格统一…」（统一条款原文不动）。
- `src/api/styles.py`：`style_library_text()` 每条带 use_when/pitfalls，
  Agent 才能带着完整信号自选；upsert/CSV 导入扩三字段。
- `src/pipeline/agent_production.py`：`_AGENT_INSTRUCTIONS` 2b 步加一句
  「结合各风格的适用条件与忌讳条款选择；选中风格的忌讳必须原样保留进 image_template」。
- 管理页（`static/views/Admin.js` + 相关 API）：风格编辑表单加两个文本域；
  展示 `source` 徽标。
- **措辞纪律**：pitfalls ≤3 条、单条 ≤30 字、能正向说的不正说反
  （「用宣纸质感」优于「不要塑料感」）——LLM 对长否定列表有粉红大象效应。

**回滚设计**：三列均有默认空值——置空即退化为现状行为，无需回滚迁移。

验收：
- 旧条目（字段空）行为与升级前逐字节一致（`verify_skill_equivalence.py` 思路做前后对照）；
- `test_page_balance` + `test_absorb_8002` + `test_agent_pipeline` 全绿（5.3 纪律：
  改提示词注入点必跑）；
- 本地 8005 起一条真实任务全链，风格注入段在 SSE/日志中可见「适用/忌讳」。

### P3 WS2 模板蒸馏 + WS4 基准集（2-3 天，可部分并行）

**素材蒸馏（人工+LLM 辅助，产出即数据）**：
- 范围：开源库 12 分类中取「图表与信息可视化(53 例)/海报与排版(90 例)/
  文档与出版物(11 例)/历史与古风(16 例)」四个相邻分类 + 22 模板中
  `infographic-engine / poster-layout-system / nature-science-poster /
  document-publishing / history-classical-themes` 五个模板；
- 蒸馏物 A：**5-8 条新公共风格**进 `data/styles.json`（source=oss_distilled），
  每条按《风格库训练方法总结》口径写全四件套（keywords/use_when/description/pitfalls）；
- 蒸馏物 B：既有条目（新中式史料卡/政务办事指南卡等）补齐 use_when/pitfalls；
- 蒸馏物 C：可直接改编的开源 prompt 骨架句（竖版/大标题/信息分区等与现有底座不冲突的）
  追加进 `layouts_cn.txt` 对应页角色——**此项必须先跑
  `scripts/verify_skill_equivalence.py --baseline`，改完 `--verify` 零差异 + 守卫断言绿**
  （0903 文档 §5.3 纪律；若守卫冲突则放弃 C，只落 A/B）。

**基准集（WS4）**：
- `data/bench/bench_set.json`：20 题 = 开源相邻分类蒸馏 8 题 + 历史真实交付任务 12 题，
  每题固定 task 参数（mode/style/query），落 task 快照保证可重跑；
- `scripts/bench_image_quality.py`：批量投递到本地 8005 → 等任务 approved →
  导出缩略图拼版 `docs/生图基准-<日期>.md` 供人工快审对比；
- 机制：**每次 P2/P3 落地后跑一轮**，新旧拼版对比，人工 10 分钟快审
  （通过率不达基线 → 该条 enabled=false 回滚，数据层回滚，零代码回滚）。

**成本预算**：基准批每轮约 20 题 × 6-8 张 × ¥0.4 ≈ ¥50-65/轮；
开发期联调一律 `MOCK_IMAGE_GEN=true`（0 成本），真实批控制在 ≤4 轮。

### P4 文档与发版（0.5 天）

1. 更新 `docs/技术交接文档-20260907.md`（§3 技术栈表加一行、§12 加 021 迁移与
   style 新列、§15 改动史）→ 跑 `build_handover.py` 重新生成在线版；
2. 本地验证清单：pytest 全量（tests/ + dsh_serve/tests，裸跑 `pytest -q`）→
   基准集一轮通过 → 本地 8005 冒烟（`docker compose -f
   deploy/compose/qvp3.docker-compose.yml --project-directory . up -d`）；
3. **发版门禁（铁律）**：用户确认后，才走 §9 镜像本地化流程更新服务器 8005；
   8003/8001/8002/8000 一律不碰；迁移 021 随启动幂等应用，无需手工执行。

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| 提示词膨胀推高 token/超时 | use_when ≤40 字、pitfalls ≤3×30 字；超限由 test_style_ssot 的 schema 校验拦截 |
| 负面条款粉红大象效应 | 措辞纪律（正向优先）+ 基准批对比把关 |
| 管理页改动被 sync 冲掉 | P1 立纪律：公共库只改 json；管理页改公共条目时提示「改仓库文件」 |
| 直连/Agent 两路径注入不同步 | 两路都从 style_select/风格库文本单点取，改后必跑 test_agent_pipeline + 直连集成 |
| 开源模板与小红书竖版卡片垂直不匹配 | 只取相邻分类；蒸馏不照抄；基准批人工快审否决权 |
| 选择算法回归（同一任务换了风格） | task 快照幂等机制不变：已定风格的任务重生成不受影响；新任务走新打分 |

## 6. 验收总清单（Definition of Done）

- [ ] D1-D6 六项缺陷全部关闭或有数据层回滚开关
- [ ] `pytest -q` 全量绿（含新 test_style_ssot、既有守卫断言）
- [ ] `verify_skill_equivalence.py` 零差异（若动了 layouts 片段）
- [ ] 基准批 ≥1 轮，人工快审通过率 ≥ 现有基线
- [ ] 四处风格副本归一为 `data/styles.json` 一处
- [ ] 交接口文档已更新并重建 handover.html
- [ ] 服务器 8005 发版完成且健康检查通过（`curl :8005/api/tasks?limit=1`），
      其余端口零接触
