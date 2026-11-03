#!/usr/bin/env python
"""门禁机器检查（Gate Scan）— 唯一可采信的违禁词/受限字段门禁。

修复 1：任何生产 Agent 的"我已自检通过"都不作为门禁依据，本脚本独立重新推导。
修复 2：扫描范围由目录通配决定，不枚举文件名，新增文件自动纳入。

分类采用**正向白名单**（只把明确属于对外物料的文本判为 ad_facing），避免把
"合规自检 / 修订记录 / 禁用说明 / 方法论"里的被引述违禁词误判为违规；
未归入 ad_facing 的命中全部列为 internal 并**逐条输出**，不做静默丢弃。

判定：ad_facing 命中 或 受限字段泄漏 或 读取错误 → FAIL。

用法:
  python gate_scan.py --run <运行目录> --facts <PRODUCT_FACTS.json> [--sku Q2] [--wordlist <md>]
退出码: 0=PASS, 1=FAIL, 2=错误
"""
import argparse
import hashlib
import importlib.util
import json
import re
import sys
from datetime import datetime
from pathlib import Path

SCAN_DIRS = ["01_research", "02_commerce", "03_creative", "04_critic"]
SCAN_EXT = {".md", ".json", ".txt", ".srt", ".ass", ".html"}
FULL_AD_EXT = {".srt", ".ass"}          # 整文件都属对外物料

# JSON 叶键白名单：这些键的字符串值属对外文案
AD_FACING_KEYS = {
    "hook", "hook_text", "title", "title_text", "point", "why_it_converts",
    "text", "subtitle", "subtitles", "cta", "cta_text", "copy", "caption",
    "headline", "selling_point", "voiceover", "screen_text", "first_screen_copy",
    "body", "line", "script_text",
}
# .md/.txt/.html 章节白名单：只有落在这些章节里的行才算对外文案
AD_HEAD_MARK = re.compile(
    r"(首屏|第\s*\d+\s*屏|商品标题|标题库|卖点条|卖点文案|字幕|口播|旁白|CTA|"
    r"主图文案|详情页文案|封面文案|客服话术)",
    re.I,
)
# 章节内部标记：命中即判 internal（优先级高于白名单）
INTERNAL_HEAD_MARK = re.compile(
    r"(自检|合规|红线|禁用|受限|排除|方法论|证据|来源|复核记录|修订|更正记录|"
    r"说明|注意|blocked|exclusion|note|policy|handoff|拆解|分析)",
    re.I,
)

# ---------------------------------------------------------------- 违禁词库定位
# 本技能不自带违禁词表，复用「广告红线卫士」(ad-redline-guardian) 的检查引擎。
# 该技能可能是第三方安装的，路径因机器而异，因此按候选顺序探测，绝不写死绝对路径。
# 优先级：环境变量 > 同级 skills 目录 > 用户工作区 skills 目录 > cwd 下的 skills。
AD_REDLINE_SKILL_NAME = "ad-redline-guardian"


def _candidate_skill_dirs():
    import os
    env = os.environ.get("AD_REDLINE_SKILL_DIR")
    if env:
        yield Path(env)
    here = Path(__file__).resolve().parent           # <skill>/scripts
    yield here.parent.parent / AD_REDLINE_SKILL_NAME  # 与各技能平级的 skills/
    home = Path.home()
    for base in (".workbuddy/skills", ".codebuddy/skills", ".claude/skills"):
        yield home / base / AD_REDLINE_SKILL_NAME
    yield Path.cwd() / "skills" / AD_REDLINE_SKILL_NAME


def resolve_ad_redline():
    """返回 (check_banned.py, banned-words.md)；未找到时返回 (None, None)。"""
    for d in _candidate_skill_dirs():
        script = d / "scripts" / "check_banned.py"
        wordlist = d / "references" / "banned-words.md"
        if script.is_file() and wordlist.is_file():
            return script, wordlist
    return None, None


CHECK_SCRIPT, DEFAULT_WORDLIST = resolve_ad_redline()

# ------------------------------------------------------------ 包内兜底基线
# 增强词库 ad-redline-guardian 是**可选**的。装了就优先用它（词更多），
# 没装则回落到包内 references/banned-words-statutory.md（依据《广告法》条文整理）。
# 这样合规闸门不会因为别人的技能下架/改名而整体失效，同时也不复制第三方代码。
_PKG_ROOT = Path(__file__).resolve().parent.parent
BUILTIN_ENGINE = Path(__file__).resolve().parent / "engine_min.py"
BUILTIN_WORDLIST = _PKG_ROOT / "references" / "banned-words-statutory.md"

if CHECK_SCRIPT is None or DEFAULT_WORDLIST is None:
    if BUILTIN_WORDLIST.is_file():
        DEFAULT_WORDLIST = BUILTIN_WORDLIST
WORDLIST_SOURCE = "ad-redline-guardian" if CHECK_SCRIPT is not None else "builtin-statutory"

_MISSING_HINT = (
    "未找到违禁词库 ad-redline-guardian（需要 scripts/check_banned.py 与 references/banned-words.md），"
    "且包内兜底基线也不可用。\n"
    "修复方式任选其一：\n"
    "  1) 安装该技能：把 ad-redline-guardian 放到 <skills>/ad-redline-guardian/；\n"
    "  2) 设置环境变量 AD_REDLINE_SKILL_DIR 指向其根目录；\n"
    "  3) 确认 references/banned-words-statutory.md 与本脚本同在技能包内（包内兜底）；\n"
    "  4) 用 --wordlist 显式指定词表文件（仍需可用的解析引擎）。"
)


def _load_builtin_engine():
    """加载包内最小引擎；它提供与增强引擎一致的 parse_wordlist/normalize/CATEGORY_RISK。"""
    if not BUILTIN_ENGINE.is_file():
        raise FileNotFoundError(_MISSING_HINT)
    spec = importlib.util.spec_from_file_location("_engine_min", str(BUILTIN_ENGINE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_checker(script_path=None):
    """返回检查引擎模块。

    - 显式传入 script_path：路径不可用即抛错（不静默降级）。
    - 未传入：优先用 ad-redline-guardian 引擎，缺失时用包内 engine_min 兜底。
    """
    if script_path is not None:
        p = Path(script_path)
        if not p.is_file():
            raise FileNotFoundError(_MISSING_HINT)
    else:
        p = CHECK_SCRIPT
        if p is None or not Path(p).is_file():
            return _load_builtin_engine()
    spec = importlib.util.spec_from_file_location("_cb", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def scan_lines(cb, lines, wordlist):
    """返回 [(lineno, hit), ...]，保留真实行号。"""
    categories = cb.parse_wordlist(str(wordlist))
    out = []
    for lineno, raw in enumerate(lines, 1):
        norm = cb.normalize(raw)
        for cat, words in categories.items():
            for w in words:
                wn = cb.normalize(w)
                if not wn:
                    continue
                start = 0
                while True:
                    idx = norm.find(wn, start)
                    if idx == -1:
                        break
                    out.append((lineno, {
                        "word": w, "category": cat,
                        "risk": cb.CATEGORY_RISK.get(cat, "medium"),
                        "line": lineno, "col": idx + 1,
                        "context": raw[max(0, idx - 18): idx + len(w) + 18].strip(),
                    }))
                    start = idx + len(wn)
    return out


def ad_lines_of_file(run: Path, rel: str, cb, wordlist):
    """返回该文件里被判定为 ad_facing 的 (lineno, raw_line)。"""
    p = run / rel
    txt = p.read_text(encoding="utf-8", errors="replace")
    suffix = p.suffix.lower()
    out = []

    if suffix in FULL_AD_EXT:
        return list(enumerate(txt.splitlines(), 1))

    if suffix == ".json":
        try:
            obj = json.loads(txt)
        except json.JSONDecodeError:
            return []
        raw_lines = txt.splitlines()

        def walk(node, path):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, path + [str(k)])
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, path + [str(i)])
            elif isinstance(node, str) and node.strip() and path and path[-1] in AD_FACING_KEYS:
                for seg in node.splitlines():
                    if not seg.strip():
                        continue
                    for i, raw in enumerate(raw_lines, 1):
                        if seg.strip() and seg.strip() in raw:
                            out.append((i, raw))
                            break
        walk(obj, [])
        return out

    # .md / .txt / .html：按章节判定
    head = ""
    for lineno, raw in enumerate(txt.splitlines(), 1):
        m = re.match(r"^#{1,6}\s+(.+)$", raw)
        if m:
            head = m.group(1)
            continue
        if INTERNAL_HEAD_MARK.search(head) or INTERNAL_HEAD_MARK.search(raw):
            continue
        if AD_HEAD_MARK.search(head):
            out.append((lineno, raw))
    return out


def derive_blocked_patterns(facts: dict):
    blocked = (facts.get("ad_copy_gate") or {}).get("blocked_fields") or []
    conflicts = facts.get("conflicts") or {}
    pats = []
    for f in blocked:
        c = conflicts.get(f)
        if not c:
            continue
        for cand in c.get("candidates") or []:
            v = str(cand.get("value", ""))
            m = re.search(r"\d+(?:\.\d+)?\s*(?:\*\s*[\d.]+\s*)*(?:mm|cm|g|kg|oz|in)\b", v)
            if m:
                lit = m.group(0)
                pats.append({"field": f, "literal": lit,
                             "pattern": re.escape(lit).replace(r"\ ", r"\s*")})
    return pats


def classify_all(run: Path, cb, wordlist):
    files, ad_hits, internal_hits, errors = [], [], [], []
    for d in SCAN_DIRS:
        base = run / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in SCAN_EXT:
                continue
            rel = str(p.relative_to(run)).replace("\\", "/")
            files.append(rel)
            try:
                raw_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception as e:
                errors.append({"file": rel, "error": str(e)})
                continue
            ad_set = {ln for ln, _ in ad_lines_of_file(run, rel, cb, wordlist)}
            for ln, h in scan_lines(cb, raw_lines, wordlist):
                rec = dict(h)
                rec["file"] = rel
                (ad_hits if ln in ad_set else internal_hits).append(rec)
    return files, ad_hits, internal_hits, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--facts", required=True)
    ap.add_argument("--wordlist", default=str(DEFAULT_WORDLIST) if DEFAULT_WORDLIST else None)
    ap.add_argument("--sku", default="")
    a = ap.parse_args()

    run = Path(a.run).resolve()
    if not run.is_dir():
        print("运行目录不存在: %s" % run); sys.exit(2)
    facts = json.loads(Path(a.facts).read_text(encoding="utf-8"))
    try:
        cb = load_checker()
    except FileNotFoundError as ex:
        print(str(ex)); sys.exit(2)
    wordlist = a.wordlist or (str(DEFAULT_WORDLIST) if DEFAULT_WORDLIST else None)
    if not wordlist or not Path(wordlist).is_file():
        print("违禁词表不可达: %s\n%s" % (wordlist, _MISSING_HINT)); sys.exit(2)

    t0 = datetime.now()
    files, ad_hits, internal_hits, errors = classify_all(run, cb, wordlist)

    # 受限字段数值泄漏：只在 ad_facing 行里判定
    leak = []
    ad_map = {f: {ln for ln, _ in ad_lines_of_file(run, f, cb, wordlist)} for f in files}
    for pat in derive_blocked_patterns(facts):
        rx = re.compile(pat["pattern"], re.I)
        for f in files:
            for lineno, raw in enumerate((run / f).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if lineno in ad_map.get(f, set()) and rx.search(raw):
                    leak.append({"field": pat["field"], "literal": pat["literal"],
                                 "file": f, "line": lineno, "context": raw.strip()[:90]})

    el = lambda hs: sorted(hs, key=lambda h: (h["file"], h["line"], h["word"]))
    verdict = "FAIL" if (ad_hits or leak or errors) else "PASS"
    elapsed = (datetime.now() - t0).total_seconds()

    report = {
        "gate": "gate_scan", "sku": a.sku or facts.get("sku_id"),
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": round(elapsed, 2),
        "scan_dirs": SCAN_DIRS, "scan_ext": sorted(SCAN_EXT),
        "classification": "positive whitelist (ad-facing keys / ad sections)",
        "files_scanned": len(files), "file_list": files,
        "ad_facing_hits": el(ad_hits), "internal_hits": el(internal_hits),
        "blocked_field_leaks": leak, "errors": errors,
        "counts": {"ad_facing": len(ad_hits), "internal": len(internal_hits),
                   "blocked_leak": len(leak),
                   "high": sum(1 for h in ad_hits if h["risk"] == "high")},
        "verdict": verdict, "self_check_statements_trusted": False,
    }

    out = run / "_gate"; out.mkdir(parents=True, exist_ok=True)
    rp = out / "banned_scan.json"
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    L = ["=" * 74, "门禁机器检查 · gate_scan", "=" * 74,
         "SKU=%s  文件数=%d  用时=%.2fs  判定=%s" % (report["sku"], len(files), elapsed, verdict),
         "ad_facing 命中=%d  internal 命中=%d  受限字段泄漏=%d" % (len(ad_hits), len(internal_hits), len(leak)),
         "-" * 74]
    if ad_hits:
        L.append("【阻断】对外文本违禁词：")
        for h in el(ad_hits):
            L.append("  FAIL  %-46s L%-4d %s [%s]" % (h["file"], h["line"], h["word"], h["risk"]))
            L.append("        上下文: %s" % h["context"])
    if leak:
        L.append("【阻断】受限字段数值泄漏（对外文本）：")
        for h in leak:
            L.append("  FAIL  %-46s L%-4d %-14s %s" % (h["file"], h["line"], h["field"], h["context"]))
    if internal_hits:
        L.append("【不阻断·全量列出备查】内部文本命中（自检/修订/禁用说明/方法论）：")
        for h in el(internal_hits):
            L.append("  INFO  %-46s L%-4d %s" % (h["file"], h["line"], h["word"]))
    if not ad_hits and not leak:
        L.append("  ✅ 对外文本零违禁词、零受限字段泄漏")
    L += ["-" * 74, "判定: %s" % verdict,
          "注: 本结果由编排层独立机器检查产出，生产 Agent 不得修改。", "=" * 74]
    (out / "banned_scan.log").write_text("\n".join(L), encoding="utf-8")
    (out / "SCAN_PROTECTED.txt").write_text(
        "本目录由 gate_scan.py 独占写入。生产 Agent（COMMERCE/COPY/IMAGE/VIDEO/COMPLIANCE）"
        "禁止修改本目录任何文件；修改即视为门禁绕过。\n", encoding="utf-8")

    print("\n".join(L))
    print("日志: %s" % (out / "banned_scan.log"))
    print("SHA256(report): %s" % hashlib.sha256(rp.read_bytes()).hexdigest())
    sys.exit(1 if verdict == "FAIL" else 0)


if __name__ == "__main__":
    main()
