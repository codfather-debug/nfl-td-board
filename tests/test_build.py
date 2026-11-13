#!/usr/bin/env python3
"""Checks for build.py, and that the page scores players the same way build.py does.

Run from the project folder: python3 -m unittest discover tests
The page check needs Chrome or Chromium and is skipped without it.
"""
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import build  # noqa: E402

CHROME = next((p for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", shutil.which("google-chrome"),
                           shutil.which("chromium"), shutil.which("chromium-browser")) if p and os.path.exists(p)), None)


def chrome_dom(path, profile):
    return subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", f"--user-data-dir={profile}",
                           "--virtual-time-budget=5000", "--dump-dom", "file://" + path.replace(" ", "%20") + "#selftest"],
                          capture_output=True, text=True, timeout=120).stdout


def local_or_download(variable, url):
    if variable not in os.environ:
        return build.fetch(url)
    with open(os.environ[variable], "rb") as f:
        return f.read()


def unescape(text):
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def differences(a, b, path=""):
    """Where two parsed sheets disagree. Numbers may differ by 0.001: the two round a half differently."""
    if isinstance(a, dict) and isinstance(b, dict):
        return [d for k in sorted(set(a) | set(b), key=str) for d in differences(a.get(k), b.get(k), f"{path}.{k}")]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in differences(x, y, f"{path}[{i}]")]
    numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (a, b))
    if a == b or (numbers and abs(a - b) <= 0.0011):
        return []
    return [f"{path}: build.py {a!r}, page {b!r}"]


def page_data():
    with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
        return json.loads(re.search(r"^let D = (.*);$", f.read(), re.M).group(1))


class Names(unittest.TestCase):
    def test_suffixes_accents_and_punctuation(self):
        self.assertEqual(build.norm_name("Kenneth Walker III"), "kenneth walker")
        self.assertEqual(build.norm_name("D'Andre Swift"), "dandre swift")
        self.assertEqual(build.norm_name("Amon-Ra St. Brown"), "amonra st brown")
        self.assertEqual(build.norm_name("José  Núñez Jr."), "jose nunez")


class Kickoffs(unittest.TestCase):
    def test_offset_follows_daylight_saving(self):
        self.assertTrue(build.kickoff_iso("2026-09-13", "13:00").endswith("-04:00"))
        self.assertTrue(build.kickoff_iso("2026-11-01", "13:00").endswith("-05:00"))
        self.assertTrue(build.kickoff_iso("2027-01-03", "13:00").endswith("-05:00"))


class Chances(unittest.TestCase):
    CURVE = {"x": [0.1, 0.3, 0.6], "y": [0.08, 0.32, 0.5]}

    def test_calibration_draws_lines_between_points_and_stops_at_the_ends(self):
        self.assertEqual(build.calibrate(self.CURVE, 0.01), 0.08)
        self.assertAlmostEqual(build.calibrate(self.CURVE, 0.2), 0.20)
        self.assertEqual(build.calibrate(self.CURVE, 0.99), 0.5)

    def test_fair_odds(self):
        self.assertEqual(build.fair_odds(0.5), -100)
        self.assertEqual(build.fair_odds(0.25), 300)
        self.assertEqual(build.fair_odds(0.6), -150)
        self.assertIsNone(build.fair_odds(0))

    def test_model_curves_rise(self):
        with open(os.path.join(ROOT, "model", "model.json"), encoding="utf-8") as f:
            model = json.load(f)
        for name in ("anyCal", "firstCal"):
            curve = model[name]
            self.assertEqual(curve["x"], sorted(curve["x"]))
            self.assertEqual(curve["y"], sorted(curve["y"]))


class Picks(unittest.TestCase):
    @staticmethod
    def player(name, starter, snaps=0.7, rz=2.0, unavailable=False):
        return {"player": name, "pos": "RB", "starter": starter, "lastPlayed": snaps, "rzPerGame": rz, "unavailable": unavailable}

    def test_keeps_the_sheet_player_unless_another_is_clearly_the_starter(self):
        sheet, close = self.player("Sheet", 1.0), self.player("Close", 1.1)
        self.assertIs(build.choose_pick("Sheet", "RB", [sheet, close]), sheet)
        clear = self.player("Clear", 1.5, snaps=0.85, rz=2.5)
        self.assertIs(build.choose_pick("Sheet", "RB", [sheet, clear]), clear)

    def test_replaces_an_injured_sheet_player(self):
        hurt, backup = self.player("Sheet", 2.0, unavailable=True), self.player("Backup", 0.4)
        self.assertIs(build.choose_pick("Sheet", "RB", [hurt, backup]), backup)
        self.assertIsNone(build.choose_pick("Sheet", "RB", [hurt]))


class BuiltPage(unittest.TestCase):
    """The scoring exists twice, in build.py and in template.html. These fail if the two drift apart."""

    @classmethod
    def setUpClass(cls):
        cls.data = page_data()
        cls.players = build.score_players(cls.data)

    def test_chances_are_sane(self):
        self.assertGreater(len(self.players), 100)
        for p in self.players:
            self.assertTrue(0 < p["gameRate"] <= p["firstRate"] <= p["anyRate"] + 0.05 < 1, p)
        totals = {}
        for p in self.players:
            if not p["unavailable"]:
                totals[p["team"]] = totals.get(p["team"], 0) + p["firstRate"]
        self.assertLessEqual(max(totals.values()), build.TEAM_FIRST_CAP + 1e-9)

    def test_a_teammate_being_out_raises_a_players_chance(self):
        data = json.loads(json.dumps(self.data))
        data["injuries"] = {}
        healthy = {(p["team"], p["player"]): p for p in build.score_players(data)}
        star = max((p for p in healthy.values() if p["pos"] == "RB" and not p["unavailable"]), key=lambda p: p["anyRate"])
        data["injuries"] = {f"{star['team']}|{build.norm_name(star['player'])}": "Out"}
        for p in build.score_players(data):
            before = healthy[(p["team"], p["player"])]
            if p["team"] == star["team"] and p["pos"] == "RB" and p["player"] != star["player"] and not p["unavailable"]:
                self.assertGreater(p["anyRate"], before["anyRate"], p["player"])
            elif p["team"] != star["team"]:
                self.assertEqual(p["anyRate"], before["anyRate"], p["player"])

    @unittest.skipUnless(CHROME, "Chrome is not installed")
    def test_page_matches_build(self):
        with tempfile.TemporaryDirectory() as profile:
            dom = chrome_dom(os.path.join(ROOT, "index.html"), profile)
        found = re.search(r'<pre id="selftest">(.*?)</pre>', dom, re.S)
        self.assertTrue(found, "The page didn't finish loading; it may have a script error.")
        page = json.loads(unescape(found.group(1)))
        self.assertEqual(page["values"], ["Good value +13%", "Poor value −10%", "Fair price", "Try +150 or −120"])

        mine = {(p["team"], p["player"]): p for p in self.players}
        self.assertEqual(len(page["players"]), len(mine))
        for team, name, any_rate, first_rate, game_rate, odds in page["players"]:
            p = mine[(team, name)]
            for theirs, ours in ((any_rate, p["anyRate"]), (first_rate, p["firstRate"]), (game_rate, p["gameRate"])):
                self.assertAlmostEqual(theirs, ours, places=9, msg=f"{name} ({team})")
            self.assertAlmostEqual(odds, build.fair_odds(p["anyRate"]), delta=1, msg=name)

        for row, (team, name) in zip(self.data["slate"], page["picks"]):
            chosen = build.choose_pick(row["player"], row["pick"].split()[0], [p for p in self.players if p["team"] == team])
            self.assertEqual(name, chosen["player"] if chosen else row["player"], team)

        days = ["2026-09-13", "2026-11-01", "2026-11-08", "2027-01-03", "2027-03-14"]
        self.assertEqual([k[-6:] for k in page["kickoffs"]], [build.kickoff_iso(d, "13:00")[-6:] for d in days])


class SheetParsers(unittest.TestCase):
    """The sheet is read twice: by build.py, and by the page's Update now button. This fails if they disagree."""

    @unittest.skipUnless(CHROME, "Chrome is not installed")
    def test_page_reads_the_sheet_the_way_build_does(self):
        with open(os.path.join(ROOT, "template.html"), encoding="utf-8") as f:
            template = f.read()
        url = re.search(r'const XLSX_URL = "(.*?)"', template).group(1)
        sri = re.search(r'const XLSX_SRI = "sha512-(.*?)"', template).group(1)
        try:  # TD_SHEET and TD_XLSX_JS name local copies, for running without a connection.
            sheet = local_or_download("TD_SHEET", build.EXPORT_URL)
            library = local_or_download("TD_XLSX_JS", url)
        except OSError as e:
            self.skipTest(f"Couldn't download the sheet or the spreadsheet reader: {e}")
        self.assertEqual(base64.b64encode(hashlib.sha512(library).digest()).decode(), sri, "The spreadsheet reader changed.")

        script = """<script>
          const bytes = Uint8Array.from(atob("%s"), c => c.charCodeAt(0));
          const out = document.createElement("pre"); out.id = "parsetest";
          try { out.textContent = JSON.stringify(parseWorkbook(bytes.buffer)); } catch (e) { out.textContent = JSON.stringify({ error: String(e) }); }
          document.body.append(out);
        </script>""" % base64.b64encode(sheet).decode()
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "page.html")
            with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f, open(path, "w", encoding="utf-8") as out:
                out.write(f.read() + "<script>" + library.decode("utf-8") + "</script>" + script)
            dom = chrome_dom(path, os.path.join(folder, "profile"))
        found = re.search(r'<pre id="parsetest">(.*?)</pre>', dom, re.S)
        self.assertTrue(found, "The page didn't finish loading; it may have a script error.")
        page = json.loads(unescape(found.group(1)))
        self.assertNotIn("error", page)

        mine = json.loads(json.dumps(build.parse_sheet(io.BytesIO(sheet))))
        found = differences(mine, page)
        self.assertFalse(found, f"{len(found)} differences, starting with: " + "; ".join(found[:8]))


if __name__ == "__main__":
    unittest.main()
