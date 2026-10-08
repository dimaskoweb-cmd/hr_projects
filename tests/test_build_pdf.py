import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_pdf as bp  # noqa: E402

with open(os.path.join(ROOT, "examples", "demo.json"), encoding="utf-8") as _f:
    DEMO = json.load(_f)


def with_text(text):
    d = copy.deepcopy(DEMO)
    d["thesis"] = text
    return d


class LintTests(unittest.TestCase):
    def test_demo_is_clean(self):
        errors, _ = bp.validate(copy.deepcopy(DEMO))
        self.assertEqual(errors, [])

    def test_dorax_rejected(self):
        errors, _ = bp.validate(with_text("Работал в Dorax Investment"))
        self.assertTrue(any("Dorax" in e for e in errors))

    def test_deputy_general_rejected(self):
        errors, _ = bp.validate(with_text("Заместитель генерального директора"))
        self.assertTrue(any("исполнительного" in e for e in errors))

    def test_deputy_executive_allowed(self):
        errors, _ = bp.validate(with_text("Заместитель исполнительного директора"))
        self.assertEqual(errors, [])

    def test_c2_rejected_c1_allowed(self):
        self.assertTrue(bp.validate(with_text("English C2"))[0])
        self.assertEqual(bp.validate(with_text("English C1/B2"))[0], [])


class CliTests(unittest.TestCase):
    def test_creates_missing_output_dir(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "new", "sub", "x.pdf")
            r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "build_pdf.py"),
                                os.path.join(ROOT, "examples", "demo.json"), "-o", out],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.getsize(out) > 1000)


if __name__ == "__main__":
    unittest.main()
