# 电商内容 4-Agent 编排（ecommerce-agent-orchestrator v2.0）

给一个 SKU，出一整套可发布素材（选题 / 文案 / 主图方向 / 视频 / 详情页），
**4 个 Agent 只做需要理解与创意的事，所有判定交给确定性脚本闸门**。

> **v1.0 的 13-Agent 架构已废止**：功能正确但效率失败——无新素材仍耗时 5801s，
> 不能用于批量商业生产。v2.0 的结论很简单：**凡代码能确定结果的工作，禁止调用 Agent。**

---

## 一、它解决什么问题

| 老板的问题 | 这套东西的回答 |
|---|---|
| "这套素材能不能发？" | 4 个机器闸门逐项判，PASS 就是 PASS，不靠 Agent 自评 |
| "文案有没有踩广告法？" | GATE-2 用真实违禁词库扫，命中位置精确到文件+行 |
| "数字是不是编的？" | 对外文本里的每个数字都要能在 `VERIFIED` 事实里找到，找不到就 FAIL |
| "跑一套要多久？" | 无新素材全流程 SLA **<300s**，超时直接 `PERFORMANCE_FAIL` |
| "为什么以前那么慢？" | 因为让 Agent 去做脚本能做的事。现在判定类工作全脚本化 |

---

## 二、目录结构

```
ecommerce-agent-orchestrator/
├── SKILL.md                   # 编排规范（Agent 读这个）
├── README.md                  # 本文件
├── LICENSE                    # MIT
├── CHANGELOG.md
├── references/
│   └── agents.md              # 4 个 Agent 的完整角色卡
├── scripts/
│   ├── gates.py               # 4 个机器闸门统一入口
│   ├── gate_scan.py           # 违禁词 / 受限字段 / 事实数字扫描引擎
│   └── state.py               # 运行状态机 + metrics + 结果页渲染
├── templates/
│   ├── PRODUCT_FACTS.md       # 事实源模板（四级标注）
│   └── result_panel.html      # 结果页模板
└── tests/
    └── test_gates.py          # 13 项离线回归测试
```

---

## 安装

```sh
# 纯标准库即可运行；GATE-3/GATE-4 涉及图片与视频时才需要额外装
python -m pip install Pillow imageio-ffmpeg   # 可选
python -m unittest discover -s tests -t tests  # 17 项测试
```

作为技能使用时，把整个目录放进 `%USERPROFILE%\.workbuddy\skills\`。

## 三、快速开始

```bash
cd <本技能根目录>

# 0. 环境自检（必须先过）
python scripts/gates.py --help
python -m unittest discover -s tests      # 应全部 OK

# 1. 建运行
python scripts/state.py init --sku Q2-001 --root runs --platform douyin

# 2. 建事实源（唯一事实源；缺的字段只能标 BLOCKED）
cp templates/PRODUCT_FACTS.template.json runs/Q2-001/PRODUCT_FACTS.json
cp templates/BLOCKED_FIELDS.template.json runs/Q2-001/BLOCKED_FIELDS.json
#    → 填入真实信息，四级标注：VERIFIED / USER_STATED / INFERRED / BLOCKED
#    → 两个模板都是 JSON：以前这里让你把 .md 复制成 .json，闸门一读就 JSONDecodeError，已改
#    → 空字段一律判 FAIL，这是有意的 fail-closed，不要为了变绿而编数

# 3. 跑闸门
python scripts/gates.py --run runs/Q2-001 --facts runs/Q2-001/PRODUCT_FACTS.json --gate all
```

---

## 四、4 个闸门

| 闸门 | 检查 | 预算 |
|---|---|---|
| **GATE-1 SKU_GUARD** | SKU_ID / PRODUCT_NAME / SOURCE_FILE / PRODUCT_IMAGE / OUTPUT_DIR | <1s |
| **GATE-2 FACT_COMPLIANCE** | 违禁词 + 受限字段泄漏 + VERIFIED 事实数字对照 | <2s |
| **GATE-3 MEDIA_TECH** | 可解码 / 分辨率 / 时长 / 帧率 / 音轨 / 字幕条数 | <5s |
| **GATE-4 FRAME_EXTRACT** | 每镜 ≤3 帧、整片 ≤30 帧 + contact sheet（交 CRITIC） | <120s |

结果落盘 `_gate/gates.json` 与 `_gate/gates.log`，含每个闸门耗时与 `within_budget`。

### 设计原则：fail-closed

**取不到证据就是 FAIL，绝不是 PASS。** 具体表现：

- `PRODUCT_IMAGE` 路径不可达 → FAIL（相对路径按运行目录解析，不写死本机路径）
- 缺 `BLOCKED_FIELDS.json` → `blocked_fields` FAIL
- **违禁词库不可用 → GATE-2 整体 FAIL**，并在 note 里给出修复方式（不允许因为依赖缺失而静默放行）

---

## 五、依赖

### 必需

| 依赖 | 说明 |
|---|---|
| Python ≥ 3.10 | 仅标准库即可跑 GATE-1 / GATE-2 / state |
| **`ad-redline-guardian` 技能** | GATE-2 的违禁词库来源（`check_banned.py` + `banned-words.md`）。**这是外部技能，不在本包内** |

词库定位顺序（不写死任何绝对路径）：

1. 环境变量 `AD_REDLINE_SKILL_DIR` 指向其根目录
2. 与各技能平级的 `skills/ad-redline-guardian/`
3. `~/.workbuddy/skills/`、`~/.codebuddy/skills/`、`~/.claude/skills/` 下的同名技能
4. 当前目录的 `skills/ad-redline-guardian/`

四者都找不到时 GATE-2 判 FAIL 并提示修复方式——**不会假装通过**。

### 可选

| 依赖 | 用于 |
|---|---|
| `Pillow` | GATE-4 抽帧拼 contact sheet |
| `imageio-ffmpeg` / 系统 `ffmpeg` | GATE-3 媒体元数据、GATE-4 抽帧 |

```bash
pip install Pillow imageio-ffmpeg
```

---

## 六、测试

```bash
python -m unittest discover -s tests -v
```

覆盖 13 项：脚本内无硬编码本机路径、相对路径解析、词库缺失必须 FAIL、词库定位支持环境变量覆盖、
GATE-1 的 PASS/FAIL 分支、状态机初始化。

> 测试全部离线、不访问网络、不读真实业务数据。

---

## 适用用户

**适合：** 有内容团队或外包协作的中小品牌 / 代运营公司——核心痛点不是「没工具」，
而是「Agent 自己给自己打满分」；需要一套可审计的门禁流程与状态机。

**不适合：** 单人做一条视频就想立刻出片的场景（直接用 shortvideo-pipeline 更快）；
不愿意按模板填状态文件的人。

## 七、已知边界

- **GATE-2 的检出力取决于外部词库**。词库是初筛快照，不代替人工判断；命中需人工复核，
  但**禁止生产 Agent 自行绕过闸门**。
- **GATE-3 / GATE-4 需要真实媒体文件**，无成片时不参与判定。
- 本技能只管编排与判定，**不包含**图像/视频生成能力，也不自动发布到任何平台。
- 平台规则会变。规则快照超过 1 个月必须重新核对。

---

## 八、许可证

MIT，见 `LICENSE`。

## 与其他技能的关系

本技能的骨架（4 个 Agent + 确定性机器闸门、fail-closed 判定、SLA 预算）
继承自 [`ecommerce-agent-orchestrator`](../ecommerce-agent-orchestrator)，
在其之上叠加了**视频号专属**的部分：账号主体校验（GATE-0）、
带货身份与关注数门槛、AI 标注合规（GATE-3）、视频号违禁词扩展层。

**为什么分成两个技能而不是一个**：通用编排讲的是「怎么不让 Agent 自己给自己打分」，
视频号讲的是「具体平台的资格与合规红线」。拆开写，两个场景都能独立安装、
独立演进；合在一起会让只想用通用闸门的人被迫读一堆平台细节。
两者共用同一套判定哲学的代码结构，接口一致，不冲突。

## 运行环境

| 项 | 状态 |
|---|---|
| Windows 11 + Python 3.12 / 3.13 | ✅ 实测通过 |
| Linux（含 WSL） | ⚠️ **未实测**。代码用 `pathlib`，没有硬编码分隔符，理论上可跑；但测试只在 Windows 跑过，不声称支持 |
| macOS | ⚠️ 未实测 |

诚实标注：不要在没有实跑过的平台上对外声称「已支持」。
