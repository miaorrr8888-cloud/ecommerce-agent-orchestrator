#!/usr/bin/env python3
"""电商 Agent 编排 · 状态机与结果页生成器

总控专用。所有命令都是幂等的文件操作，不派子 Agent。

用法：
  python state.py init    --sku SKU-001 --platform 抖店 [--root runs]
  python state.py set     --sku SKU-001 --agent FACTS --status DONE --artifact 01_facts/PRODUCT_FACTS.md
  python state.py gate    --sku SKU-001 --qa PASS --reason "7/7 通过"
  python state.py champion--sku SKU-001 --hook "..." --image 04_image/final/a.png
  python state.py publish --sku SKU-001 --status DONE --evidence 09_publish/evidence/
  python state.py perf    --sku SKU-001 --metrics metrics.json
  python state.py blocked --sku SKU-001 --field 电池容量 --needed-by COPY --severity hard
  python state.py render  --sku SKU-001
  python state.py status  --sku SKU-001
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROLES = ["ORCH", "RESEARCH", "COMMERCE-COPY", "CREATIVE", "CRITIC", "PUB", "PERF"]
QA_ITEMS = [
    "sku_consistency", "product_authenticity", "fact_correctness",
    "compliance", "first_3s", "purchase_intent", "technical_quality",
]
QA_LABEL = {
    "sku_consistency": "SKU 一致",
    "product_authenticity": "产品真实性",
    "fact_correctness": "事实正确",
    "compliance": "合规",
    "first_3s": "前 3 秒",
    "purchase_intent": "购买欲",
    "technical_quality": "技术质量",
}
BADGE = {
    "PASS": "PASS", "REVISION": "REVISION", "REJECT": "REJECT",
    "BLOCKED": "BLOCKED", "PENDING": "PENDING",
}
TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "result_panel.html"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_dir(root: str, sku: str) -> Path:
    return Path(root).resolve() / sku


def load(root: str, sku: str) -> dict:
    p = run_dir(root, sku) / "run_state.json"
    if not p.exists():
        sys.exit(f"[ERR] 未找到运行状态：{p}\n请先执行 init --sku {sku}")
    return json.loads(p.read_text(encoding="utf-8"))


def save(root: str, sku: str, st: dict) -> Path:
    st["updated"] = now()
    p = run_dir(root, sku) / "run_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def esc(s) -> str:
    return (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def media_tag(path_str: str, kind: str, empty_text: str) -> str:
    """存在则内嵌预览，否则返回提示文本。"""
    if not path_str:
        return esc(empty_text)
    p = Path(path_str)
    if not p.exists():
        return esc(f"{empty_text}（未找到：{path_str}）")
    try:
        uri = p.resolve().as_uri()
    except ValueError:
        return esc(empty_text)
    if kind == "image":
        return f'<img src="{uri}" alt="champion">'
    suffix = p.suffix.lower()
    if suffix in (".mp4", ".webm", ".mov"):
        return f'<video src="{uri}" controls style="max-width:100%;border-radius:10px"></video>'
    return f'<img src="{uri}" alt="first-frame"><div style="font-size:12px">首帧占位 · 待批注渲染</div>'


def cmd_init(a):
    d = run_dir(a.root, a.sku)
    for sub in ["01_research", "02_commerce", "03_creative", "04_critic",
                "07_media/composited", "_gate"]:
        (d / sub).mkdir(parents=True, exist_ok=True)
    st = {
        "sku": a.sku,
        "run_id": datetime.now().strftime("%Y%m%d-%H%M%S"),
        "platform": a.platform,
        "created": now(),
        "updated": now(),
        "status": "PENDING",
        "wave": 1,
        "agents": {r: {"status": "PENDING", "artifact": "", "attempts": 0, "note": "", "ts": ""} for r in ROLES},
        "qa": {"verdict": "PENDING", "reason": "", "checks": {k: "PENDING" for k in QA_ITEMS}, "ts": ""},
        "champion": {"hook": "", "hook_alts": "", "image": "", "image_path": "",
                     "video": "", "video_path": "", "video_state": "未渲染"},
        "publish": {"status": "HOLD", "binding": "", "evidence": ""},
        "performance": {"metrics": [], "ranking": []},
        "metrics": {"TOTAL_TIME": 0, "AGENT_TIME": 0, "MACHINE_GATE_TIME": 0,
                    "WAIT_TIME": 0, "MAX_CONCURRENCY": 0, "CRITIC_QA_TIME": 0,
                    "ORCHESTRATION": "PENDING", "PERFORMANCE": "PENDING"},
        "blocked": [],
    }
    print(f"[OK] 初始化 {save(a.root, a.sku, st)}")


def cmd_set(a):
    st = load(a.root, a.sku)
    if a.agent not in st["agents"]:
        if a.agent in ROLES:      # 兼容旧 run_state（角色表升级后自动补位）
            st["agents"][a.agent] = {"status": "PENDING", "artifact": "",
                                     "attempts": 0, "note": "", "ts": ""}
        else:
            sys.exit(f"[ERR] 未知角色 {a.agent}，可选：{', '.join(ROLES)}")
    rec = st["agents"][a.agent]
    if a.status == "RUNNING":
        rec["attempts"] += 1
        if rec["attempts"] > 2:
            print(f"[WARN] {a.agent} 已尝试 {rec['attempts']} 次 —— 铁律 R8：换工具/路线，禁止继续修")
    rec.update({"status": a.status, "ts": now()})
    if a.artifact:
        rec["artifact"] = a.artifact
    if a.note:
        rec["note"] = a.note
    ws = [r["status"] for r in st["agents"].values()]
    if any(s == "FAILED" for s in ws):
        st["status"] = "REVISION"
    save(a.root, a.sku, st)
    print(f"[OK] {a.agent} -> {a.status} (尝试 {rec['attempts']} 次)")


def cmd_gate(a):
    st = load(a.root, a.sku)
    verdict = a.qa.upper()
    if verdict not in ("PASS", "REVISION", "REJECT"):
        sys.exit("[ERR] --qa 只能是 PASS / REVISION / REJECT")
    st["qa"] = {"verdict": verdict, "reason": a.reason or "", "ts": now(),
                "checks": st["qa"].get("checks", {k: "PENDING" for k in QA_ITEMS})}
    st["status"] = verdict
    if verdict == "PASS":
        st["agents"]["QA"]["status"] = "DONE"
        st["publish"]["status"] = "READY"
    save(a.root, a.sku, st)
    print(f"[OK] QA = {verdict}" + (" → PUBLISHER 已放行" if verdict == "PASS" else " → 禁止发布（铁律 R7）"))


def cmd_champion(a):
    st = load(a.root, a.sku)
    c = st["champion"]
    for k in ("hook", "hook_alts", "image", "image_path", "video", "video_path", "video_state"):
        v = getattr(a, k, None)
        if v:
            c[k] = v
    save(a.root, a.sku, st)
    print(f"[OK] 冠军素材已更新")


def cmd_publish(a):
    st = load(a.root, a.sku)
    if a.status.upper() == "DONE" and st["qa"]["verdict"] != "PASS":
        sys.exit(f"[BLOCKED] QA = {st['qa']['verdict']}，禁止发布（铁律 R7）")
    st["publish"].update({"status": a.status.upper(), "evidence": a.evidence or st["publish"]["evidence"]})
    if a.binding:
        st["publish"]["binding"] = a.binding
    if a.evidence:
        st["publish"]["evidence"] = a.evidence
    st["agents"]["PUB"]["status"] = "DONE" if a.status.upper() == "DONE" else "PENDING"
    save(a.root, a.sku, st)
    print(f"[OK] 发布状态 = {a.status.upper()}")


def cmd_perf(a):
    st = load(a.root, a.sku)
    if a.metrics:
        payload = json.loads(Path(a.metrics).read_text(encoding="utf-8"))
        st["performance"]["metrics"] = payload.get("metrics", [])
        st["performance"]["ranking"] = payload.get("ranking", [])
    st["agents"]["PERF"]["status"] = "DONE"
    save(a.root, a.sku, st)
    print(f"[OK] 表现数据已回填：{len(st['performance']['metrics'])} 项指标 / "
          f"{len(st['performance']['ranking'])} 条素材排名")


def cmd_blocked(a):
    st = load(a.root, a.sku)
    st["blocked"].append({"field": a.field, "needed_by": a.needed_by.split(",") if a.needed_by else [],
                          "severity": a.severity, "reason": a.reason or ""})
    if a.severity == "hard" and st["status"] not in ("PASS",):
        st["status"] = "BLOCKED"
    save(a.root, a.sku, st)
    print(f"[OK] 已登记 BLOCKED 字段：{a.field}")


def cmd_metrics(a):
    st = load(a.root, a.sku)
    m = st.setdefault("metrics", {})
    for k in ("TOTAL_TIME", "AGENT_TIME", "MACHINE_GATE_TIME", "WAIT_TIME",
              "MAX_CONCURRENCY", "CRITIC_QA_TIME"):
        v = getattr(a, k.lower(), None)
        if v not in (None, ""):
            try:
                m[k] = float(v) if "." in str(v) else int(v)
            except (TypeError, ValueError):
                m[k] = v
    for k in ("ORCHESTRATION", "PERFORMANCE"):
        v = getattr(a, k.lower(), None)
        if v:
            m[k] = v
    if m.get("TOTAL_TIME"):
        m["SLA_MET"] = ("PASS" if m["TOTAL_TIME"] < 300 else "FAIL")
    if m.get("MAX_CONCURRENCY"):
        m["CONCURRENCY_OK"] = ("PASS" if m["MAX_CONCURRENCY"] >= 3 else "ORCHESTRATION_FAIL")
    save(a.root, a.sku, st)
    print("[OK] 效率指标: " + ", ".join("%s=%s" % (k, m[k]) for k in
          ("TOTAL_TIME", "AGENT_TIME", "MACHINE_GATE_TIME", "WAIT_TIME",
           "MAX_CONCURRENCY", "CRITIC_QA_TIME", "SLA_MET", "CONCURRENCY_OK") if k in m))


def cmd_status(a):
    st = load(a.root, a.sku)
    print(f"SKU {st['sku']} · {st['platform']} · 状态 {st['status']} · QA {st['qa']['verdict']} · 发布 {st['publish']['status']}")
    for r in ROLES:
        rec = st["agents"][r]
        flag = {"DONE": "✓", "RUNNING": "~", "FAILED": "✗", "BLOCKED": "!", "PENDING": "·"}.get(rec["status"], "?")
        extra = f"  [{rec['artifact']}]" if rec["artifact"] else ""
        print(f"  {flag} {r:<11} {rec['status']:<8} attempts={rec['attempts']}{extra}")


def cmd_render(a):
    st = load(a.root, a.sku)
    if not TEMPLATE.exists():
        sys.exit(f"[ERR] 结果页模板缺失：{TEMPLATE}")
    tpl = TEMPLATE.read_text(encoding="utf-8")
    c, qa, pub = st["champion"], st["qa"], st["publish"]

    checks = "".join(
        f'<div class="chk"><span class="dot '
        f'{"ok" if qa["checks"].get(k) == "PASS" else ("bad" if qa["checks"].get(k) == "FAIL" else "warn" if qa["checks"].get(k) == "REVISION" else "na")}'
        f'"></span>{esc(QA_LABEL[k])}<span style="color:var(--muted)">· {esc(qa["checks"].get(k, "PENDING"))}</span></div>'
        for k in QA_ITEMS
    ) if qa.get("checks") else '<div class="chk">尚未执行 QA</div>'

    perf_rows = "".join(
        f'<tr><td>{esc(m.get("name"))}</td><td>{esc(m.get("value"))}</td>'
        f'<td>{esc(m.get("delta", "—"))}</td><td><code>{esc(m.get("source", "未确认"))}</code></td></tr>'
        for m in st["performance"]["metrics"]
    ) or '<tr><td colspan="4" style="color:var(--muted)">暂无真实数据（禁止填估算值）</td></tr>'

    rank_rows = ""
    for i, r in enumerate(st["performance"]["ranking"], 1):
        win = " class=\"win\"" if r.get("champion") else ""
        rank_rows += (f'<tr{win}><td>{i}</td><td>{esc(r.get("asset"))}</td><td>{esc(r.get("type"))}</td>'
                      f'<td>{esc(r.get("ctr", "—"))}</td><td>{esc(r.get("cvr", "—"))}</td>'
                      f'<td>{esc(r.get("verdict", "—"))}</td></tr>')
    if not rank_rows:
        rank_rows = '<tr><td colspan="6" style="color:var(--muted)">暂无排名数据</td></tr>'

    blocked_rows = "".join(
        f'<li><b>{esc(b.get("field"))}</b> — 需要它的角色：{esc(",".join(b.get("needed_by", [])) or "—")}'
        f'（{esc(b.get("severity"))}）{esc(b.get("reason", ""))}</li>'
        for b in st["blocked"]
    ) or "<li style='color:var(--muted)'>无</li>"

    video_state = c.get("video_state") or ("已渲染" if c.get("video_path") else "未渲染")
    out = (tpl
           .replace("{{SKU}}", esc(st["sku"]))
           .replace("{{PLATFORM}}", esc(st["platform"]))
           .replace("{{RUN_ID}}", esc(st["run_id"]))
           .replace("{{RUN_TIME}}", esc(st["updated"]))
           .replace("{{STATUS}}", BADGE.get(st["status"], "PENDING"))
           .replace("{{CHAMPION_HOOK}}", esc(c.get("hook")) or "—")
           .replace("{{HOOK_ALTS}}", esc(c.get("hook_alts")) or "—")
           .replace("{{CHAMPION_IMAGE}}", media_tag(c.get("image_path") or c.get("image"), "image", "主图待产出"))
           .replace("{{CHAMPION_IMAGE_PATH}}", esc(c.get("image_path") or c.get("image")) or "—")
           .replace("{{CHAMPION_VIDEO}}", media_tag(c.get("video_path") or c.get("video"), "video", "视频待产出"))
           .replace("{{VIDEO_STATE}}", esc(video_state))
           .replace("{{CHAMPION_VIDEO_PATH}}", esc(c.get("video_path") or c.get("video")) or "—")
           .replace("{{QA_CHECKS}}", checks)
           .replace("{{QA_VERDICT}}", esc(qa["verdict"]) + (f' · {esc(qa["reason"])}' if qa.get("reason") else ""))
           .replace("{{PUBLISH_STATUS}}", esc(pub["status"]))
           .replace("{{PUBLISH_BINDING}}", esc(pub.get("binding")) or "—")
           .replace("{{PUBLISH_EVIDENCE}}", esc(pub.get("evidence")) or "—")
           .replace("{{PERF_ROWS}}", perf_rows)
           .replace("{{RANK_ROWS}}", rank_rows)
           .replace("{{BLOCKED_ROWS}}", blocked_rows))
    m = st.get("metrics") or {}
    out = (out
           .replace("{{TOTAL_TIME}}", esc(m.get("TOTAL_TIME", "—")))
           .replace("{{AGENT_TIME}}", esc(m.get("AGENT_TIME", "—")))
           .replace("{{MACHINE_GATE_TIME}}", esc(m.get("MACHINE_GATE_TIME", "—")))
           .replace("{{WAIT_TIME}}", esc(m.get("WAIT_TIME", "—")))
           .replace("{{MAX_CONCURRENCY}}", esc(m.get("MAX_CONCURRENCY", "—")))
           .replace("{{CRITIC_QA_TIME}}", esc(m.get("CRITIC_QA_TIME", "—")))
           .replace("{{SLA_MET}}", esc(m.get("SLA_MET", "—")))
           .replace("{{CONCURRENCY_OK}}", esc(m.get("CONCURRENCY_OK", "—")))
           .replace("{{PERFORMANCE}}", esc(m.get("PERFORMANCE", "—"))))
    dst = run_dir(a.root, a.sku) / "result_panel.html"
    dst.write_text(out, encoding="utf-8")
    print(f"[OK] 结果页已生成：{dst}")


def main():
    ap = argparse.ArgumentParser(description="电商 Agent 编排状态机")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p):
        p.add_argument("--sku", required=True)
        p.add_argument("--root", default="runs")

    p = sub.add_parser("init"); add_common(p); p.add_argument("--platform", default="未指定"); p.set_defaults(f=cmd_init)

    p = sub.add_parser("set"); add_common(p)
    p.add_argument("--agent", required=True)
    p.add_argument("--status", required=True,
                   choices=["PENDING", "RUNNING", "DONE", "FAILED", "BLOCKED", "CANDIDATE"])
    p.add_argument("--artifact", default=""); p.add_argument("--note", default="")
    p.set_defaults(f=cmd_set)

    p = sub.add_parser("gate"); add_common(p)
    p.add_argument("--qa", required=True); p.add_argument("--reason", default="")
    p.set_defaults(f=cmd_gate)

    p = sub.add_parser("champion"); add_common(p)
    for k in ("hook", "hook-alts", "image", "image-path", "video", "video-path", "video-state"):
        p.add_argument(f"--{k}", dest=k.replace("-", "_"), default="")
    p.set_defaults(f=cmd_champion)

    p = sub.add_parser("publish"); add_common(p)
    p.add_argument("--status", default="DONE"); p.add_argument("--binding", default="")
    p.add_argument("--evidence", default="")
    p.set_defaults(f=cmd_publish)

    p = sub.add_parser("perf"); add_common(p)
    p.add_argument("--metrics", default=""); p.set_defaults(f=cmd_perf)

    p = sub.add_parser("blocked"); add_common(p)
    p.add_argument("--field", required=True); p.add_argument("--needed-by", dest="needed_by", default="")
    p.add_argument("--severity", default="hard", choices=["hard", "soft"]); p.add_argument("--reason", default="")
    p.set_defaults(f=cmd_blocked)

    p = sub.add_parser("metrics"); add_common(p)
    for k in ("total-time", "agent-time", "machine-gate-time", "wait-time",
              "max-concurrency", "critic-qa-time", "orchestration", "performance"):
        p.add_argument("--%s" % k, dest=k.replace("-", "_"), default="")
    p.set_defaults(f=cmd_metrics)

    p = sub.add_parser("status"); add_common(p); p.set_defaults(f=cmd_status)
    p = sub.add_parser("render"); add_common(p); p.set_defaults(f=cmd_render)

    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
