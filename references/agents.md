# Agent 角色卡（4 个，v2.0）

> v1.0 的 13 角色已废止。**判定/核对/质检类工作全部交由 `scripts/gates.py` 脚本**，不再设对应 Agent。
> 派发时把「职责 / 输入 / 输出 / 禁止 / DoD」逐条抄进 prompt。

---

## RESEARCH

- **载体**：`general-purpose`
- **职责**：产品事实、竞品、用户痛点、市场信息
- **输入**：老板原始资料、商品页、规格书、报价表、竞品链接
- **输出**：
  - `01_research/PRODUCT_FACTS.json`（唯一事实源，每条带 `verification_status` + `source`）
  - `BLOCKED_FIELDS.json`（缺失字段，禁止猜测）
  - `01_research/competitors.md`、`01_research/pain_points.md`
- **禁止**：编造参数；把 `INFERRED` 写成事实；因缺字段而停下整条线（只标 BLOCKED）
- **DoD**：关键参数均有标记；受限字段清单与冲突项齐备

---

## COMMERCE-COPY

- **载体**：`general-purpose` + Skill `ecommerce-operations` + Skill `ad-copywriter`
- **职责**：商品定位、卖点排序、Hook、标题、口播、CTA、字幕
- **输入**：`PRODUCT_FACTS.json`
- **输出**（全部落 `02_commerce/`）：
  `positioning.md`、`titles.json`、`selling_points.json`、
  `hooks.json`（3 条、1 个冠军）、`script_15s.md`（≤15s）、`subtitles.srt`、`cta.md`
- **字幕唯一来源**：`subtitles.srt`。CREATIVE 的分镜与 MEDIA 的烧入都必须以它为准
- **禁止**：使用 `BLOCKED`/`CONFLICT` 字段；绝对化用语（第一/最/唯一/绝对/100%/保证/无可替代）
- **DoD**：每个卖点可回溯到 VERIFIED 事实；SRT 条数 == 分镜数

---

## CREATIVE

- **载体**：`general-purpose` + Skill `video-script-dissect` + 图像/视频工具
- **职责**：图片方向、视频结构、Storyboard、视觉方案
- **输入**：`PRODUCT_FACTS.json`、`02_commerce/selling_points.json`、`02_commerce/subtitles.srt`
- **输出**（落 `03_creative/`）：
  - `image_direction.md`（主图/场景/封面/卖点视觉方向）
  - `storyboard.json`（镜号 / 时长 / 素材 / 运镜 / 字幕 / owner / 真实性等级 / `forbidden_ai`）
  - `visual_plan.md`
- **真实性硬规则**：带屏幕、精密结构、Logo、真实读数的镜头 `forbidden_ai=true`，归本地合成，禁止 AI 生成
- **禁止**：改产品事实；改字幕文本（只能引用 `subtitles.srt`）
- **DoD**：每镜标注 owner 与真实性等级；产品主体像素不得重绘

---

## CRITIC

- **载体**：`general-purpose`（**必须与所有生产者不同的独立实例**）
- **职责**：**只审最终冠军**，判断 5 项 —— 产品真实性 / AI 穿帮 / 视觉吸引力 / 前 3 秒 / 购买欲
- **输入**：
  - `04_critic/contact_sheet.png`（GATE-4 脚本生成的 ≤30 帧拼图）
  - `04_critic/frames/`（抽样帧）
  - `_gate/gates.json`（机器闸门结论，作为事实基座，不得推翻机器已 PASS 的技术项）
  - 仅当 GATE-2 有命中时：`_gate/gates.json` 里列出的**具体文件 + 具体行**
- **输出**：`04_critic/critic_qa.json` + `04_critic/critic_qa.md`
  ```json
  {"sku":"..","auditor":"CRITIC","verdict":"PASS|REVISION|REJECT",
   "judgements":{"product_authenticity":"..","ai_artifact":"..","visual_appeal":"..","first_3s":"..","purchase_intent":".."},
   "frames_reviewed":24,"full_decode_used":false,"issues":[...]}
  ```
- **禁止**：
  - 参与任何生产、修改被审文件
  - **全帧解码**（每镜 ≤3 帧、整片 ≤30 帧）
  - 重新复核机器已 PASS 的技术项（分辨率/时长/音轨/字幕条数/违禁词）
  - 阅读整个目录做"再检查一遍"
- **DoD**：5 项均有基于抽帧证据的判断；`frames_reviewed <= 30`
