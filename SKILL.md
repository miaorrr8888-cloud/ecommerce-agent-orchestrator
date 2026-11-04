---
name: ecommerce-agent-orchestrator
slug: zhengrong-ecommerce-agent-orchestrator
displayName: "电商内容制作总控"
summary: "4 个 Agent 只做理解与创意，4 个脚本闸门做判定，一个 SKU 走完选题、文案、主图、视频到发布。"
display_name: "电商内容制作总控"
display_name_en: E-commerce 4-Agent Orchestrator
description: 电商内容生产的轻量编排总控：只保留 4 个 Agent（RESEARCH / COMMERCE-COPY / CREATIVE / CRITIC），其余全部改为确定性脚本闸门。当用户要求为某个 SKU 出选题/文案/主图/视频/详情页并发布，或提到"分工""并行 Agent""总控""SKU 内容生产""冠军素材""结果页""按架构跑""4 Agent"时使用。触发词：跑这个SKU、出一套素材、按架构跑、并行生产、上架发布、素材复盘。
description_zh: 4 个专业 Agent + 4 个机器闸门。Agent 只做需要理解、判断、创意的事；凡代码能确定结果的，一律用脚本。全流程 SLA < 300s。
category: operations
author: WorkBuddy
agent_created: true
version: 2.1.0
updated: "2026-11-03"
license: MIT
---
# 电商 4-Agent 编排（v2.0）

> **v1.0 的 13-Agent 架构已废止**：功能正确但效率失败（无新素材仍耗时 5801s，不能用于批量商业生产）。
> v2.0 核心原则：**Agent 只处理需要理解/判断/创意的工作；凡代码能确定结果的工作，禁止调用 Agent。**

---

## 1. 铁律

| # | 铁律 |
|---|---|
| R1 | **只有 4 个 Agent**：RESEARCH / COMMERCE-COPY / CREATIVE / CRITIC。不得再拆分。 |
| R2 | **判定类工作一律脚本化**：合规、事实核对、文件完整性、字幕条数、技术质检、SKU 校验，全部由 `scripts/gates.py` 确定性完成。 |
| R3 | **禁止 Agent 复核机器已 PASS 的项目**。机器 PASS → 直接 PASS。 |
| R4 | **机器命中才叫人**：只有闸门报出命中时，才把**具体文件 + 具体行**交给 Critic 判断，禁止把整个目录丢给 Agent 重看一遍。 |
| R5 | **真并行**：同波次任务必须在**一条消息**内同批派发。`max_concurrency >= 3`，否则直接判 `ORCHESTRATION_FAIL`。 |
| R6 | **CRITIC 独立**：只审最终冠军，禁止参与生产，禁止全帧解码（每镜 ≤3 帧、整片 ≤30 帧）。 |
| R7 | **单一事实源**：`PRODUCT_FACTS.json`。`blocked_fields` / `allowed` / `verification_status` 只从它读，禁止双事实源；`IDENTITY_LOCK.json` 只保留身份字段。 |
| R8 | **同路线首次 + 最多 1 次修正**；再失败立即换工具/路线。禁止无限复核。 |
| R9 | **媒体成本闸**：视频默认先出首帧 + 参数待批注，不默认烧积分。 |
| R10 | **效率一票否决**：超过 SLA 直接 `PERFORMANCE_FAIL`，**不得用"最后全 PASS"掩盖效率失败**。 |
| R11 | 后台子 Agent 会被中止 → 必须前台同批调用，禁止 `run_in_background`。 |
| R12 | 老板只看结果页，禁止倒过程日志。 |

---

## 2. 4 个 Agent

完整角色卡见 `references/agents.md`。

| ID | 角色 | 载体 | 只做 |
|---|---|---|---|
| RESEARCH | 研究 | general-purpose | 产品事实、竞品、用户痛点、市场信息 → 产出 `PRODUCT_FACTS.json` + `BLOCKED_FIELDS.json` |
| COMMERCE-COPY | 运营+文案（合并） | skills `ecommerce-operations` + `ad-copywriter` | 定位、卖点排序、Hook、标题、口播、CTA、字幕 |
| CREATIVE | 创意 | `video-script-dissect` + 图像/视频工具 | 图片方向、视频结构、Storyboard、视觉方案 |
| CRITIC | 终审 | general-purpose | 只审最终冠军：产品真实性、AI 穿帮、视觉吸引力、前 3 秒、购买欲 |

**已取消的 Agent**（全部改为脚本）：COMPLIANCE、FACT CHECK、文件 QA、字幕 QA、技术 QA、Publisher QA。

---

## 3. 4 个机器闸门

统一入口：

```bash
python scripts/gates.py --run runs/<SKU> --facts <PRODUCT_FACTS.json> --sku <SKU> [--gate 1|2|3|4|all]
```

| 闸门 | 检查 | 预算 | 实现 |
|---|---|---|---|
| **GATE-1 SKU_GUARD** | SKU_ID / PRODUCT_NAME / SOURCE_FILE / PRODUCT_IMAGE / OUTPUT_DIR | **<1s** | 路径与字段一致性 |
| **GATE-2 FACT_AND_COMPLIANCE** | `check_banned.py` + blocked_fields 泄漏 + VERIFIED_FACTS 数字对照（对外文本正向白名单，22 文件级） | **<2s** | 复用 `gate_scan.py` 引擎 |
| **GATE-3 MEDIA_TECH** | 可解码 / 分辨率 / 时长 / 帧率 / 音轨 / 字幕条数 / 镜头文件完整性 | **<5s** | ffmpeg 元数据 |
| **GATE-4 FINAL_VISUAL_QA** | 脚本抽帧（每镜 ≤3、整片 ≤30）+ contact sheet → 交 CRITIC 判断 5 项 | **<120s** | 脚本抽帧 + Critic |

结果落盘 `_gate/gates.json` 与 `_gate/gates.log`，含每个闸门耗时与 `within_budget`。
`_gate/` 为闸门独占目录，**生产 Agent 禁止修改**。

---

## 4. 并行拓扑

```
波1（一条消息同批派发，max_concurrency 必须 ≥3）
  ├─ RESEARCH         → 01_research/PRODUCT_FACTS.json, BLOCKED_FIELDS.json, competitors.md, pain_points.md
  ├─ COMMERCE-COPY    → 02_commerce/positioning.md, titles.json, selling_points.json, hooks.json, script_15s.md, subtitles.srt, cta.md
  └─ CREATIVE         → 03_creative/image_direction.md, storyboard.json, visual_plan.md

波2（生产工具，可并行）
  └─ 图像/视频落地工具（有既有素材则直接复用，不重新生成）

波3（机器闸门并行，纯脚本）
  └─ GATE-1 ∥ GATE-2 ∥ GATE-3

波4（只有冠军进入）
  └─ GATE-4 抽帧 → CRITIC 终审
```

`max_concurrency` 由编排层从各 Agent 自报的 `_timing.json`（start/end epoch）实测，写入 `run_state.metrics.MAX_CONCURRENCY`。

---

## 5. 效率 SLA

无新素材情况下：

| 阶段 | 预算 |
|---|---|
| GATE-1 SKU Guard | <1s |
| GATE-2 Compliance | <2s |
| GATE-3 Media Tech | <5s |
| GATE-4 Final Visual QA | <120s |
| **全流程** | **<300s** |

超预算 → `PERFORMANCE_FAIL`。`ORCHESTRATION_FAIL`（并发 <3）与 `PERFORMANCE_FAIL` 都直接判 **NOT_READY**。

---

## 6. 失败策略

- 同一路线：首次 + 1 次修正 → 再失败换工具/路线。
- 机器已 PASS 的项目，禁止 Agent 重新研究。
- 机器命中的项目，只把命中的**文件+行**交给 Critic，附机上改写建议。

---

## 7. 事实源

- `PRODUCT_FACTS.json` 唯一事实源；四级标记 `VERIFIED / USER_STATED / INFERRED / BLOCKED`。
- 缺的字段进 `BLOCKED_FIELDS.json`，禁止任何 Agent 自行补参数。
- `IDENTITY_LOCK.json` 只保留身份字段，不得保存广告门禁状态。

---

## 8. 结果页

`runs/<SKU>/result_panel.html`（`state.py render` 生成）。显示：
SKU / 冠军 Hook / 冠军主图 / 冠军视频 / 机器 Gate / Critic QA / 发布状态 / BLOCKED，
以及**效率区**：`TOTAL_TIME`、`AGENT_TIME`、`MACHINE_GATE_TIME`、`WAIT_TIME`、`MAX_CONCURRENCY`。

写入指标：

```bash
python scripts/state.py metrics --sku <SKU> --root runs \
  --total-time 250 --agent-time 180 --machine-gate-time 8 \
  --wait-time 20 --max-concurrency 3 --critic-qa-time 90 \
  --orchestration OK --performance PERFORMANCE_OK
```

---

## 9. 目录约定

```
runs/<SKU>/
├─ run_state.json          # 状态 + metrics
├─ PRODUCT_FACTS.json      # 唯一事实源
├─ BLOCKED_FIELDS.json
├─ 01_research/            # RESEARCH
├─ 02_commerce/            # COMMERCE-COPY
├─ 03_creative/            # CREATIVE
├─ 04_critic/              # CRITIC（frames/ + contact_sheet.png + critic_qa.json）
├─ 07_media/composited/    # 成片
├─ _gate/                  # 闸门独占：gates.json gates.log
└─ result_panel.html
```
