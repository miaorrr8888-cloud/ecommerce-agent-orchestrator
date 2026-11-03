#!/usr/bin/env python
"""4 个机器闸门（确定性脚本）— 取代 COMPLIANCE / FACT CHECK / 文件QA / 字幕QA / 技术QA Agent。

GATE-1 SKU_GUARD         <1s   SKU_ID / PRODUCT_NAME / SOURCE_FILE / PRODUCT_IMAGE / OUTPUT_DIR
GATE-2 FACT_COMPLIANCE   <2s   check_banned.py（复用 gate_scan 引擎）+ blocked_fields + VERIFIED 对照
GATE-3 MEDIA_TECH        <5s   解码/分辨率/时长/音轨/字幕条数/镜头文件完整性（ffmpeg + Python）
GATE-4 帧抽取（供 Critic）     每镜 ≤3 帧、整片 ≤30 帧 + 拼一张 contact sheet

用法:
  python gates.py --run <运行目录> --facts <PRODUCT_FACTS.json> [--gate 1|2|3|4|all] [--video <mp4>]
退出码: 0=全 PASS, 1=有 FAIL, 2=错误
"""
import argparse
import importlib.util
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATE_BUDGET_S = {1: 1.0, 2: 2.0, 3: 5.0, 4: 120.0}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def srt_texts(p: Path):
    out = []
    for ln in p.read_text(encoding="utf-8").replace("\r\n", "\n").splitlines():
        t = ln.strip()
        if not t or t.isdigit() or "-->" in t:
            continue
        out.append(t)
    return out


def _resolve_rel(p: str, run: Path) -> Path:
    """把 facts 里的相对路径解析成真实路径。

    不写死任何本机绝对路径：先按原样（可能是绝对路径）判断，再依次尝试
    运行目录、运行目录上一级、当前工作目录。全部落空时返回运行目录下的候选，
    由调用方按 exists() 判 FAIL。
    """
    path = Path(p)
    if path.is_absolute():
        return path
    for base in (run, run.parent, Path.cwd()):
        cand = base / p
        if cand.exists():
            return cand
    return run / p


# ---------------------------------------------------------------- GATE 1
def gate1_sku_guard(run: Path, facts: dict, sku: str):
    chk, notes = {}, []
    sku_id = str(facts.get("sku_id", "")).strip()

    # SKU_ID
    arts = {}
    for rel, keys in [("02_commerce/titles.json", ["sku_id", "sku"]),
                      ("02_commerce/selling_points.json", ["sku_id", "sku"]),
                      ("03_creative/storyboard.json", ["sku_id", "sku"]),
                      ("02_commerce/hooks.json", ["sku_id", "sku"]),
                      ("05_copy/hooks.json", ["sku_id", "sku"])]:
        p = run / rel
        if p.exists():
            try:
                o = json.loads(p.read_text(encoding="utf-8"))
                arts[rel] = str(next((o[k] for k in keys if k in o), "")).strip()
            except Exception:
                arts[rel] = ""
    mismatch = {k: v for k, v in arts.items() if v and v.upper() != sku_id.upper()}
    chk["SKU_ID"] = "PASS" if (sku_id and not mismatch and sku.upper() == sku_id.upper()) else "FAIL"
    if mismatch:
        notes.append("SKU_ID 不一致: %s" % mismatch)

    # PRODUCT_NAME
    name = str(facts.get("product_name", "")).strip()
    chk["PRODUCT_NAME"] = "PASS" if name else "FAIL"

    # SOURCE_FILE / PRODUCT_IMAGE
    sf = facts.get("source_file")
    src_ok = True
    sup = facts.get("source_file_supporting") or []
    if isinstance(sup, list) and sup:
        src_ok = any((Path(str(x)).exists() or str(x).startswith("http")) for x in sup if x)
    chk["SOURCE_FILE"] = "PASS" if (src_ok or sf) else "FAIL"

    img = str((facts.get("identity_reference_image") or {}).get("value", ""))
    img_p = _resolve_rel(img, run) if img else None
    chk["PRODUCT_IMAGE"] = "PASS" if (img and img_p and img_p.exists()) else "FAIL"
    if chk["PRODUCT_IMAGE"] == "FAIL":
        notes.append("PRODUCT_IMAGE 不可达: %s" % img)

    # OUTPUT_DIR
    need = ["02_commerce", "03_creative"]
    missing = [d for d in need if not (run / d).is_dir()]
    chk["OUTPUT_DIR"] = "PASS" if not missing else "FAIL"
    if missing:
        notes.append("缺目录: %s" % missing)

    return {"gate": "GATE-1_SKU_GUARD", "checks": chk, "artifacts_checked": arts,
            "notes": notes, "verdict": "PASS" if all(v == "PASS" for v in chk.values()) else "FAIL"}


# ---------------------------------------------------------------- GATE 2
def gate2_fact_compliance(run: Path, facts: dict, sku: str):
    gs = _load("_gs", HERE / "gate_scan.py")

    # 违禁词库是外部依赖（ad-redline-guardian）。取不到时**不能放行**：
    # 门禁的语义是「没有证据 = 不通过」，绝不允许因依赖缺失而静默 PASS。
    try:
        cb = gs.load_checker()
        wl = str(gs.DEFAULT_WORDLIST)
        if not Path(wl).is_file():
            raise FileNotFoundError(wl)
    except Exception as ex:
        return {"gate": "GATE-2_FACT_COMPLIANCE",
                "checks": {"banned_words": "FAIL", "blocked_fields": "FAIL",
                           "verified_facts": "FAIL", "file_errors": "FAIL"},
                "files_scanned": 0, "ad_facing_hits": [], "internal_hits_count": 0,
                "blocked_field_leaks": [], "unverified_numbers": [],
                "notes": ["违禁词库不可用，按 FAIL 处理（不放行）: %s" % ex,
                          "修复：设置 AD_REDLINE_SKILL_DIR，或安装 ad-redline-guardian 技能"],
                "verdict": "FAIL"}

    files, ad_hits, internal_hits, errors = gs.classify_all(run, cb, wl)

    # 受限字段泄漏（只判对外文本）
    ad_map = {f: {ln for ln, _ in gs.ad_lines_of_file(run, f, cb, wl)} for f in files}
    leak = []
    for pat in gs.derive_blocked_patterns(facts):
        rx = re.compile(pat["pattern"], re.I)
        for f in files:
            for ln, raw in enumerate((run / f).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if ln in ad_map.get(f, set()) and rx.search(raw):
                    leak.append({"field": pat["field"], "file": f, "line": ln, "context": raw.strip()[:80]})

    # VERIFIED_FACTS 对照：对外文本中的数字必须能在 VERIFIED 值里找到
    verified = []

    def walk(n):
        if isinstance(n, dict):
            if "value" in n and n.get("verification_status") == "VERIFIED":
                verified.append(str(n["value"]))
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(facts)
    allowed = set()
    for v in verified:
        allowed |= set(re.findall(r"\d+(?:\.\d+)?", v))
    allowed |= {"0", "1", "2", "3", "4"}
    unverified = []
    for f in files:
        # 只核对平台面产物（.json 文案字段 / .srt 字幕）；.md/.txt/.html 属生产文档，
        # 其文案已在 .json/.srt 中镜像，避免把 v1.0.0、S7、语速区间等元数据误判为断言
        if not (f.endswith(".json") or f.endswith(".srt")):
            continue
        for ln, raw in enumerate((run / f).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if ln not in ad_map.get(f, set()):
                continue
            t = raw.strip()
            if f.endswith(".srt") and (t.isdigit() or "-->" in t):
                continue
            for n in re.findall(r"\d+(?:\.\d+)?", raw):
                if n not in allowed:
                    unverified.append({"file": f, "line": ln, "number": n, "context": raw.strip()[:70]})

    bfj = {}
    bp = run / "BLOCKED_FIELDS.json"
    if bp.exists():
        bfj = json.loads(bp.read_text(encoding="utf-8"))
    blist = bfj.get("blocked") or bfj.get("blocked_fields") or []
    blocked_ok = len(blist) > 0

    chk = {
        "banned_words": "FAIL" if ad_hits else "PASS",
        "blocked_fields": "PASS" if (blocked_ok and not leak) else "FAIL",
        "verified_facts": "FAIL" if unverified else "PASS",
        "file_errors": "FAIL" if errors else "PASS",
    }
    return {"gate": "GATE-2_FACT_COMPLIANCE", "checks": chk,
            "files_scanned": len(files), "ad_facing_hits": ad_hits[:20],
            "internal_hits_count": len(internal_hits),
            "blocked_field_leaks": leak[:10], "unverified_numbers": unverified[:10],
            # 词库来源要写明：用增强词库还是包内法条基线，判定强度不同，不能混为一谈
            "wordlist_source": getattr(gs, "WORDLIST_SOURCE", "unknown"),
            "notes": ["机器命中 %d 条，需人工/Critic 判断" % len(ad_hits)] if ad_hits else
                     (["无命中，直接 PASS（禁止 Agent 全目录复核）"] if not leak else []),
            "verdict": "PASS" if all(v == "PASS" for v in chk.values()) else "FAIL"}


# ---------------------------------------------------------------- GATE 3
def gate3_media_tech(run: Path, video: Path, storyboard: Path, srt: Path):
    import imageio_ffmpeg
    FF = imageio_ffmpeg.get_ffmpeg_exe()
    chk, notes = {}, []
    r = subprocess.run([FF, "-hide_banner", "-i", str(video)], capture_output=True)
    err = r.stderr.decode("utf-8", "replace")
    decodable = "Video:" in err and r.returncode in (0, 1)
    chk["decodable"] = "PASS" if decodable else "FAIL"

    m = re.search(r"Video: .*?, (\d+)x(\d+)", err)
    res = "%sx%s" % (m.group(1), m.group(2)) if m else None
    chk["resolution"] = "PASS" if res == "1080x1080" else "FAIL"
    if res != "1080x1080":
        notes.append("分辨率 %s ≠ 1080x1080" % res)

    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = round(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)), 3) if m else None
    chk["duration"] = "PASS" if (dur and 10 <= dur <= 15) else "FAIL"

    m = re.search(r"(\d+(?:\.\d+)?) fps", err)
    fps = float(m.group(1)) if m else None
    chk["fps"] = "PASS" if fps and abs(fps - 24) < 0.1 else "FAIL"

    has_audio = "Audio:" in err
    chk["audio_track"] = "PASS" if has_audio else "FAIL"
    if not has_audio:
        notes.append("无音轨")

    n_srt = len(srt_texts(srt)) if srt.exists() else 0
    n_shot = 0
    if storyboard.exists():
        n_shot = len(json.loads(storyboard.read_text(encoding="utf-8")).get("shots", []))
    chk["subtitle_count"] = "PASS" if (n_srt and n_srt == n_shot) else "FAIL"

    shots_dir = run / "07_media" / "shots"
    shot_files = sorted(shots_dir.glob("shot_*.mp4")) if shots_dir.is_dir() else []
    chk["shot_files"] = "PASS" if (not shot_files or len(shot_files) == n_shot) else "FAIL"

    return {"gate": "GATE-3_MEDIA_TECH", "checks": chk,
            "measured": {"resolution": res, "duration_s": dur, "fps": fps,
                         "has_audio": has_audio, "srt_count": n_srt, "shot_count": n_shot},
            "notes": notes, "verdict": "PASS" if all(v == "PASS" for v in chk.values()) else "FAIL"}


# ---------------------------------------------------------------- GATE 4 (帧抽取)
def gate4_extract_frames(run: Path, video: Path, srt: Path, max_frames: int = 30, per_shot: int = 3):
    import imageio_ffmpeg
    from PIL import Image
    FF = imageio_ffmpeg.get_ffmpeg_exe()
    out = run / "04_critic" / "frames"
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.png"):
        f.unlink()

    texts = srt_texts(srt)
    n_shot = len(texts) or 8
    shot_d = 14.67 / n_shot
    picks = []
    for i in range(n_shot):
        for k in range(per_shot):
            t = (i + (k + 0.5) / per_shot) * shot_d
            if len(picks) >= max_frames:
                break
            picks.append((i + 1, round(t, 3)))
        if len(picks) >= max_frames:
            break

    files = []
    for n, (shot, t) in enumerate(picks, 1):
        f = out / ("s%02d_%05.2fs.png" % (shot, t))
        subprocess.run([FF, "-ss", "%.3f" % t, "-i", str(video), "-frames:v", "1",
                        "-vf", "scale=480:480", "-y", str(f)], capture_output=True)
        if f.exists():
            files.append(f)

    sheet = run / "04_critic" / "contact_sheet.png"
    cols = 6
    rows = (len(files) + cols - 1) // cols
    tw = 480
    sheet_img = Image.new("RGB", (cols * tw, rows * tw), (18, 18, 22))
    for idx, f in enumerate(files):
        im = Image.open(f).convert("RGB")
        sheet_img.paste(im, ((idx % cols) * tw, (idx // cols) * tw))
    sheet_img.save(sheet)

    manifest = {"frames_extracted": len(files), "max_frames": max_frames, "per_shot_max": per_shot,
                "shots": n_shot, "contact_sheet": str(sheet),
                "frame_files": [str(f) for f in files],
                "sampling_policy": "每镜 ≤3 帧、整片 ≤30 帧（禁止全帧解码）"}
    (run / "04_critic" / "frames_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--facts", required=True)
    ap.add_argument("--sku", default="")
    ap.add_argument("--gate", default="all")
    ap.add_argument("--video", default="")
    a = ap.parse_args()

    run = Path(a.run).resolve()
    facts = json.loads(Path(a.facts).read_text(encoding="utf-8"))
    sku = a.sku or facts.get("sku_id", "")
    video = Path(a.video) if a.video else run / "07_media" / "composited" / "vid_final_fixed.mp4"
    storyboard = run / "03_creative" / "storyboard.json"
    if not storyboard.exists():
        storyboard = run / "05_video" / "storyboard.json"
    srt = run / "02_commerce" / "subtitles.srt"

    want = ["1", "2", "3", "4"] if a.gate == "all" else [a.gate]
    results, timings = [], {}
    for g in want:
        t0 = time.time()
        if g == "1":
            res = gate1_sku_guard(run, facts, sku)
        elif g == "2":
            res = gate2_fact_compliance(run, facts, sku)
        elif g == "3":
            res = gate3_media_tech(run, video, storyboard, srt)
        else:
            res = {"gate": "GATE-4_FRAME_EXTRACT",
                   "manifest": gate4_extract_frames(run, video, srt), "verdict": "PASS"}
        el = time.time() - t0
        timings["gate%s" % g] = round(el, 3)
        res["elapsed_s"] = round(el, 3)
        res["budget_s"] = GATE_BUDGET_S[int(g)]
        res["within_budget"] = el <= GATE_BUDGET_S[int(g)]
        results.append(res)

    overall = "PASS" if all(r["verdict"] == "PASS" for r in results) else "FAIL"
    total = round(sum(timings.values()), 3)
    perf = "PERFORMANCE_OK"
    if total > 128:
        perf = "PERFORMANCE_FAIL"
    out = run / "_gate"
    out.mkdir(parents=True, exist_ok=True)
    rep = {"checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "sku": sku,
           "gates": results, "gate_timings_s": timings, "machine_gate_total_s": total,
           "performance": perf, "verdict": overall}
    (out / "gates.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")

    L = ["=" * 70, "机器闸门 · 4 GATES", "=" * 70]
    for r in results:
        L.append("%-26s %-5s  %.3fs / 预算 %.1fs  %s" % (
            r["gate"], r["verdict"], r["elapsed_s"], r["budget_s"],
            "OK" if r["within_budget"] else "超预算"))
        for k, v in (r.get("checks") or {}).items():
            L.append("      %-18s %s" % (k, v))
        for n in r.get("notes", []):
            L.append("      · %s" % n)
    L += ["-" * 70, "机器闸门总耗时: %.3fs   性能标记: %s   总判定: %s" % (total, perf, overall),
          "=" * 70]
    (out / "gates.log").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    sys.exit(0 if overall == "PASS" else 1)


if __name__ == "__main__":
    main()
