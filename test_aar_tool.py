"""
aar_tool.py のロジック単体テスト（Windows不要部分のみ）
Windows OCR / pyautogui / tkinter は使わずにコアロジックだけ検証する。
"""
import difflib
import datetime
import os
import sys
import tempfile
import unittest

# ---------------------------------------------------------------------------
# テスト対象のモジュールを、Windows依存インポートをスタブして読み込む
# ---------------------------------------------------------------------------
import types, unittest.mock as mock

# Windows専用モジュールをスタブ化してインポートエラーを回避
for stub in ('pyautogui', 'pygetwindow', 'winocr', 'ollama', 'tkinter',
             'tkinter.ttk', 'tkinter.scrolledtext', 'tkinter.filedialog',
             'tkinter.messagebox'):
    sys.modules.setdefault(stub, types.ModuleType(stub))

# tkinter.Tk / ttk.* をスタブ
tk_mod = sys.modules['tkinter']
tk_mod.Tk = mock.MagicMock
tk_mod.StringVar = mock.MagicMock
sys.modules['tkinter.ttk'] = types.ModuleType('tkinter.ttk')
sys.modules['tkinter.scrolledtext'] = types.ModuleType('tkinter.scrolledtext')

import importlib.util, pathlib
spec_path = pathlib.Path(__file__).parent / "aar_tool.py"
spec = importlib.util.spec_from_file_location("aar_tool", spec_path)
aar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aar)


# ---------------------------------------------------------------------------
# 1. 差分チェックロジック
# ---------------------------------------------------------------------------
class TestIsNewContent(unittest.TestCase):
    def _make_worker(self):
        w = object.__new__(aar.CaptureWorker)
        w._last_text = ""
        return w

    def test_empty_text_is_ignored(self):
        w = self._make_worker()
        self.assertFalse(w._is_new_content(""))
        self.assertFalse(w._is_new_content("   \n  "))

    def test_first_nonempty_text_is_new(self):
        w = self._make_worker()
        self.assertTrue(w._is_new_content("ターン1 攻撃"))

    def test_identical_text_is_rejected(self):
        w = self._make_worker()
        w._is_new_content("ターン1 攻撃")
        self.assertFalse(w._is_new_content("ターン1 攻撃"))

    def test_similar_text_above_threshold_is_rejected(self):
        base = "ターン1 攻撃 HP:100 MP:50 敵HP:80"
        # 1文字だけ変えて類似度を閾値付近にする
        similar = base[:-1] + "9"
        ratio = difflib.SequenceMatcher(None, base, similar).ratio()
        w = self._make_worker()
        w._is_new_content(base)
        if ratio >= aar.SIMILARITY_THRESHOLD:
            self.assertFalse(w._is_new_content(similar))
        else:
            self.assertTrue(w._is_new_content(similar))

    def test_very_different_text_is_accepted(self):
        w = self._make_worker()
        w._is_new_content("ターン1 攻撃")
        self.assertTrue(w._is_new_content("ターン5 勝利！ゴールド+120 経験値+300"))

    def test_threshold_is_95_percent(self):
        self.assertEqual(aar.SIMILARITY_THRESHOLD, 0.95)


# ---------------------------------------------------------------------------
# 2. save_aar
# ---------------------------------------------------------------------------
class TestSaveAar(unittest.TestCase):
    def test_creates_file_with_correct_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            content = "# AAR\n## テスト"
            path = aar.save_aar(content, save_dir=tmpdir)
            self.assertTrue(os.path.exists(path))
            fname = os.path.basename(path)
            self.assertTrue(fname.startswith("AAR_"))
            self.assertTrue(fname.endswith(".md"))
            # YYYYMMDD_HHMMSS の桁数チェック
            ts = fname[4:-3]  # "AAR_" と ".md" を除く
            self.assertEqual(len(ts), 15)  # 20260510_112233

    def test_file_content_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            content = "# AAR レポート\n## 良かった点\n- 防衛成功\n"
            path = aar.save_aar(content, save_dir=tmpdir)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), content)

    def test_creates_dir_if_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = os.path.join(tmpdir, "sub", "ゲームAAR")
            aar.save_aar("test", save_dir=nested)
            self.assertTrue(os.path.isdir(nested))


# ---------------------------------------------------------------------------
# 3. AAR プロンプト整形
# ---------------------------------------------------------------------------
class TestAarPrompt(unittest.TestCase):
    def test_prompt_contains_log(self):
        log = "[10:00]\nターン1 敵に攻撃\n[10:10]\nターン2 回復"
        prompt = aar.AAR_PROMPT_TEMPLATE.format(log=log)
        self.assertIn(log, prompt)

    def test_prompt_has_all_sections(self):
        prompt = aar.AAR_PROMPT_TEMPLATE.format(log="dummy")
        for section in ["セッション概要", "主要なイベント", "良かった点", "改善点", "次回への教訓"]:
            self.assertIn(section, prompt)

    def test_prompt_is_japanese(self):
        prompt = aar.AAR_PROMPT_TEMPLATE.format(log="x")
        self.assertIn("Markdown形式で日本語で", prompt)


# ---------------------------------------------------------------------------
# 4. 設定値の仕様適合確認
# ---------------------------------------------------------------------------
class TestConstants(unittest.TestCase):
    def test_capture_interval(self):
        self.assertEqual(aar.CAPTURE_INTERVAL, 10)

    def test_similarity_threshold(self):
        self.assertEqual(aar.SIMILARITY_THRESHOLD, 0.95)

    def test_ocr_lang_is_japanese(self):
        self.assertEqual(aar.OCR_LANG, "ja")

    def test_save_dir_is_on_desktop(self):
        self.assertIn("Desktop", aar._SAVE_DIR)
        self.assertIn("ゲームAAR", aar._SAVE_DIR)

    def test_aar_filename_format(self):
        # save_aar が返すパスのファイル名が仕様通りか
        with tempfile.TemporaryDirectory() as d:
            path = aar.save_aar("x", save_dir=d)
            import re
            self.assertRegex(os.path.basename(path), r"^AAR_\d{8}_\d{6}\.md$")


# ---------------------------------------------------------------------------
# 5. CaptureWorker の初期化
# ---------------------------------------------------------------------------
class TestCaptureWorkerInit(unittest.TestCase):
    def test_default_window_is_fullscreen(self):
        w = aar.CaptureWorker()
        self.assertEqual(w._window_title, aar.WINDOW_ALL)

    def test_log_initially_empty(self):
        w = aar.CaptureWorker()
        self.assertEqual(w.get_log(), "")

    def test_custom_window_title(self):
        w = aar.CaptureWorker(window_title="Slay the Spire")
        self.assertEqual(w._window_title, "Slay the Spire")


if __name__ == "__main__":
    unittest.main(verbosity=2)
