# 更新记录

## 2026-11-03 · v2.1.0

### 修复

- **快速开始第一条命令就崩**：README 让用户 `cp templates/PRODUCT_FACTS.md runs/<SKU>/PRODUCT_FACTS.json`，
  把 Markdown 复制成 .json，`gates.py` 一 `json.loads` 就 `JSONDecodeError`。
  新增机器可读的 `templates/PRODUCT_FACTS.template.json` 与 `templates/BLOCKED_FIELDS.template.json`，
  README 改为复制这两个，并把 `.md` 明确标成「给人读的说明版」。
- **GATE-4 带 traceback 崩掉**：成片/字幕还没产出时，函数第一行就读 `subtitles.srt`，`FileNotFoundError`
  直接把整个进程带走，前面三个闸门的结果一起丢。改为 fail-closed：缺文件/缺依赖/一帧都抽不出
  都返回 `verdict: FAIL` + 具体原因，并照常写 `frames_manifest.json`。
- **退出码没生效**：`main()` 的返回值被忽略（`main()` 而非 `raise SystemExit(main())`），
  输入错误也返回 0。现在输入错误返回 2，闸门有 FAIL 返回 1，全 PASS 返回 0。
- **facts 传错文件时给出可执行的提示**：文件不存在 / 不是合法 JSON，都直接指出
  「JSON 模板是 templates/PRODUCT_FACTS.template.json，不是 .md」，不再甩 traceback。

### 测试

- 23 项通过（新增 6 项：模板契约 3 项 + 闸门健壮性 3 项），含「空模板必须判 FAIL 而不是崩」。

### 文档

- 新增「安装」与「适用用户」两节。

## 2026-11-03 · v2.0.2 依赖解耦与元数据规范

本版**不放宽任何闸门**，只解决「第三方技能不在就整体失效」的单点依赖问题。

修复：
- **GATE-2 不再硬依赖 `ad-redline-guardian`**：新增包内
  `scripts/engine_min.py`（自研最小引擎，提供 `parse_wordlist` / `normalize` /
  `CATEGORY_RISK`）与 `references/banned-words-statutory.md`（112 词，
  依据《广告法》第九条、第十四条、第十六至十八条、第二十三条至二十五条、
  第二十八条条文整理；法条文本依《著作权法》第五条不适用著作权保护）。
  - 装第三方词库 → 优先用它（111 词，检出更广），`wordlist_source=ad-redline-guardian`；
  - 没装 → 用包内基线，`wordlist_source=builtin-statutory`，闸门照常工作；
  - 两者都没有 → 仍判 FAIL，不放行。
  - **没有复制第三方代码**：解析器与归一化是本项目自己实现的，接口对齐只为兼容。
- **GATE-2 输出新增 `wordlist_source`**：判定强度不同，不能让人以为两种来源的 PASS 等价。
- **元数据规范**：`name` 统一为 ASCII slug（= 目录名），中文名移入 `display_name`。
  依据：已发布的 11 个 GitHub 仓库里 `name` 本来就是 ASCII，改本地是**消除**漂移而非制造漂移。
- **LICENSE 补全**：替换为完整 MIT 全文（旧版免责条款被截断，GitHub 识别为 `NOASSERTION`）。

新增测试 4 项（共 17 项）：
- 模拟第三方词库缺失后，模块正确回落到 `builtin-statutory`；
- 回落引擎能真的检出违禁词（≥3 命中）；
- 回落引擎不滥报（「第一步先看基础用法…350ml」必须 0 命中）；
- 显式指定错误路径时仍抛错，不静默降级。

验证：`python -m unittest discover -s tests` → 17 passed。

## 2026-11-03 · v2.0.1 发布整改

本版只做「可发布性」整改，**不改任何闸门判定逻辑**。

修复：
- **移除硬编码本机路径**：`gates.py` 解析相对图片路径时原本写死成某个固定的旧工作目录绝对路径（含用户名），
  改成按「运行目录 → 上级 → cwd」顺序解析（`_resolve_rel`）。这同时修掉了一个真实故障：
  之前 PRODUCT_IMAGE 在正确放置图片时仍然判 FAIL。
- **移除硬编码第三方技能路径**：`gate_scan.py` 原本写死 `ad-redline-guardian` 的绝对路径，
  改为四级探测（`AD_REDLINE_SKILL_DIR` → 同级 skills → 用户工作区 skills → cwd/skills）。
- **GATE-2 改为 fail-closed**：词库不可用时原先会直接抛异常穿透；现在返回整体 FAIL +
  明确修复提示。**门禁不允许因为依赖缺失而静默放行。**

新增：
- `README.md`（含依赖说明、快速开始、已知边界）
- `LICENSE`（MIT）
- `tests/test_gates.py`（13 项离线回归：无硬编码路径、相对路径解析、词库缺失必须 FAIL、
  词库定位支持环境变量覆盖、GATE-1 分支、状态机初始化）

验证：`python -m unittest discover -s tests` → 13 passed。

## 2026-09-13 · v2.0.0

- 废止 v1.0 的 13-Agent 架构（无新素材仍耗时 5801s）。
- 收敛为 4 个 Agent + 4 个机器闸门；判定类工作全部脚本化。
- 全流程 SLA < 300s；超预算判 `PERFORMANCE_FAIL`。
