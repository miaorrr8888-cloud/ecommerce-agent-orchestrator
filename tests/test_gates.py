"""编排器确定性闸门测试。

只测「不依赖网络、不依赖本机绝对路径」的确定性行为：
  - 相对路径解析不写死机器路径
  - 违禁词库缺失时必须 FAIL（不得静默放行）
  - 违禁词库定位支持环境变量覆盖
  - GATE-1 SKU 守卫的 PASS / FAIL 分支
  - state.py 状态机初始写入

运行：
  python -m unittest discover -s tests
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_json(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


class TestNoHardcodedMachinePath(unittest.TestCase):
    """回归：脚本里不得出现写死的本机绝对路径。"""

    def test_scripts_have_no_absolute_user_path(self):
        import re
        bad = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+|/c/Users/", re.I)
        offenders = []
        for p in SCRIPTS.rglob("*.py"):
            txt = p.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(txt.splitlines(), 1):
                if bad.search(line):
                    offenders.append("%s:%d %s" % (p.name, i, line.strip()[:80]))
        self.assertEqual(offenders, [], "发现硬编码本机路径：\n" + "\n".join(offenders))


class TestResolveRel(unittest.TestCase):
    def setUp(self):
        self.gates = load("gates", SCRIPTS / "gates.py")

    def test_absolute_kept_as_is(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            target = run / "a.png"
            self.assertEqual(self.gates._resolve_rel(str(target), run), target)

    def test_relative_resolved_against_run(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            (run / "img").mkdir()
            (run / "img" / "p.jpg").write_bytes(b"x")
            got = self.gates._resolve_rel("img/p.jpg", run)
            self.assertTrue(got.is_file())
            self.assertEqual(got.resolve(), (run / "img" / "p.jpg").resolve())

    def test_missing_relative_does_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            got = self.gates._resolve_rel("nope/x.jpg", Path(d))
            self.assertFalse(got.exists())


class TestGate2FailsClosedWithoutWordlist(unittest.TestCase):
    """核心安全语义：词库取不到 → FAIL，不能 PASS。"""

    def setUp(self):
        self.gates = load("gates2", SCRIPTS / "gates.py")

    def test_missing_checker_returns_fail(self):
        """把 gate_scan 换成「load_checker 直接抛错」的替身，验证 except 分支给出 FAIL。"""
        class StubGS:
            CHECK_SCRIPT = None
            DEFAULT_WORDLIST = None

            @staticmethod
            def load_checker(*a, **k):
                raise FileNotFoundError("AD_REDLINE_SKILL_DIR 未设置，词库不可用")

        with mock.patch.object(self.gates, "_load", lambda name, path: StubGS):
            with tempfile.TemporaryDirectory() as d:
                res = self.gates.gate2_fact_compliance(Path(d), {}, "")
        self.assertEqual(res["verdict"], "FAIL")
        self.assertTrue(all(v == "FAIL" for v in res["checks"].values()))
        self.assertTrue(any("不可用" in n for n in res["notes"]))

    def test_wordlist_path_missing_also_fails(self):
        """引擎在、词表路径是 None，也必须 FAIL。"""
        class StubGS:
            CHECK_SCRIPT = SCRIPTS / "gate_scan.py"
            DEFAULT_WORDLIST = None

            @staticmethod
            def load_checker(*a, **k):
                return object()

        with mock.patch.object(self.gates, "_load", lambda name, path: StubGS):
            with tempfile.TemporaryDirectory() as d:
                res = self.gates.gate2_fact_compliance(Path(d), {}, "")
        self.assertEqual(res["verdict"], "FAIL")


class TestWordlistResolution(unittest.TestCase):
    def setUp(self):
        self.gs = load("gate_scan", SCRIPTS / "gate_scan.py")

    def test_env_override_wins(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "fake-ad-redline"
            (base / "scripts").mkdir(parents=True)
            (base / "references").mkdir(parents=True)
            (base / "scripts" / "check_banned.py").write_text("X=1\n", encoding="utf-8")
            (base / "references" / "banned-words.md").write_text("# w\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"AD_REDLINE_SKILL_DIR": str(base)}):
                script, wordlist = self.gs.resolve_ad_redline()
            self.assertEqual(Path(script), base / "scripts" / "check_banned.py")
            self.assertEqual(Path(wordlist), base / "references" / "banned-words.md")

    def test_unresolvable_returns_none_pair(self):
        with mock.patch.dict(os.environ, {"AD_REDLINE_SKILL_DIR": "/definitely/not/here"}):
            with mock.patch.object(self.gs, "_candidate_skill_dirs", lambda: iter([])):
                self.assertEqual(self.gs.resolve_ad_redline(), (None, None))

    def test_load_checker_raises_clear_error_when_missing(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            self.gs.load_checker("/definitely/not/here/check_banned.py")
        self.assertIn("ad-redline-guardian", str(ctx.exception))


class TestBuiltinWordlistFallback(unittest.TestCase):
    """P2-2 回归：第三方词库不在时，闸门必须仍能独立工作（不是整体瘫痪）。

    做法是把模块级的 resolve_ad_redline() 调用替换为 (None, None) 后再执行源码，
    模拟「机器上没装 ad-redline-guardian」。这样测的是真实的回退分支，
    不是 mock 掉被测函数。
    """

    def _load_without_third_party(self):
        src = (SCRIPTS / "gate_scan.py").read_text(encoding="utf-8")
        fallback_src = src.replace(
            "CHECK_SCRIPT, DEFAULT_WORDLIST = resolve_ad_redline()",
            "CHECK_SCRIPT, DEFAULT_WORDLIST = None, None",
        )
        self.assertNotEqual(src, fallback_src, "替换点已失效，测试需同步更新")
        mod = type(sys)("gate_scan_fallback")
        mod.__file__ = str(SCRIPTS / "gate_scan.py")
        exec(compile(fallback_src, str(SCRIPTS / "gate_scan.py"), "exec"), mod.__dict__)
        return mod

    def test_falls_back_to_statutory_wordlist(self):
        gs = self._load_without_third_party()
        self.assertIsNone(gs.CHECK_SCRIPT)
        self.assertEqual(gs.WORDLIST_SOURCE, "builtin-statutory")
        self.assertEqual(Path(gs.DEFAULT_WORDLIST), ROOT / "references" / "banned-words-statutory.md")
        self.assertTrue(Path(gs.DEFAULT_WORDLIST).is_file())

    def test_fallback_engine_detects_banned_words(self):
        gs = self._load_without_third_party()
        cb = gs.load_checker()          # 不传路径 → 回退到 engine_min
        cats = cb.parse_wordlist(str(gs.DEFAULT_WORDLIST))
        self.assertGreater(sum(len(v) for v in cats.values()), 50)
        hits = gs.scan_lines(cb, ["全网最低价，销量第一，国家级认证"], gs.DEFAULT_WORDLIST)
        self.assertGreaterEqual(len(hits), 3)

    def test_fallback_engine_ignores_harmless_copy(self):
        """高精度基线不能滥报：正常表述必须无命中。"""
        gs = self._load_without_third_party()
        cb = gs.load_checker()
        hits = gs.scan_lines(cb, ["第一步先看基础用法，这款杯子容量 350ml。"], gs.DEFAULT_WORDLIST)
        self.assertEqual(hits, [])

    def test_explicit_bad_path_still_raises(self):
        """回退只针对「没指定路径」，显式给错路径仍要报错，不许静默降级。"""
        gs = self._load_without_third_party()
        with self.assertRaises(FileNotFoundError):
            gs.load_checker("/definitely/not/here/check_banned.py")


class TestGate1SkuGuard(unittest.TestCase):
    def setUp(self):
        self.gates = load("gates3", SCRIPTS / "gates.py")
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name) / "run"
        (self.run / "02_commerce").mkdir(parents=True)
        (self.run / "03_creative").mkdir(parents=True)
        (self.run / "02_commerce" / "titles.json").write_text(
            json.dumps({"sku_id": "SKU-1", "title": "t"}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _facts(self, **kw):
        base = {"sku_id": "SKU-1", "product_name": "商品", "source_file": "a.xlsx",
                "output_dir": "out"}
        base.update(kw)
        return base

    def test_pass_when_image_exists_relative(self):
        (self.run / "ref.png").write_bytes(b"x")
        facts = self._facts(identity_reference_image={"value": "ref.png"})
        res = self.gates.gate1_sku_guard(self.run, facts, "SKU-1")
        self.assertEqual(res["checks"]["PRODUCT_IMAGE"], "PASS")

    def test_fail_when_image_missing(self):
        facts = self._facts(identity_reference_image={"value": "missing.png"})
        res = self.gates.gate1_sku_guard(self.run, facts, "SKU-1")
        self.assertEqual(res["checks"]["PRODUCT_IMAGE"], "FAIL")
        self.assertEqual(res["verdict"], "FAIL")

    def test_fail_when_output_dirs_missing(self):
        import shutil
        shutil.rmtree(self.run / "02_commerce")
        facts = self._facts(identity_reference_image={"value": "ref.png"})
        res = self.gates.gate1_sku_guard(self.run, facts, "SKU-1")
        self.assertEqual(res["checks"]["OUTPUT_DIR"], "FAIL")


class TestStateMachine(unittest.TestCase):
    def setUp(self):
        self.state = load("state", SCRIPTS / "state.py")

    def test_init_creates_state_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fn = getattr(self.state, "cmd_init", None)
            if fn is None:
                self.skipTest("state.py 没有 cmd_init 可调用入口，改由 CLI 覆盖")
            fn(type("A", (), {"sku": "SKU-1", "root": str(root), "platform": "douyin"})())
            found = list(root.rglob("*.json"))
            self.assertTrue(found, "init 未写出任何状态文件")


if __name__ == "__main__":
    unittest.main()
