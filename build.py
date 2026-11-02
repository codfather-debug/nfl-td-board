#!/usr/bin/env python3
"""Rebuild index.html from the "2026 NEW TDs allowed" sheet.

Usage: python3 build.py [path/to/export.xlsx]
With no path, downloads the sheet (it must be viewable by anyone with the link).
"""
import collections
import datetime
import io
import json
import os
import re
import sys
import urllib.request

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
SHEET_ID = "1fwtkoY2KveC6TJPRyn0pvHPseVw2_4j2Qbud1x0q1pU"
EXPORT_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=xlsx"


def download():
    with urllib.request.urlopen(EXPORT_URL, timeout=120) as resp:
        return io.BytesIO(resp.read())


def validate(data):
    """Fail the build, leaving the last good page live, if the sheet looks wrong."""
    slate = data["slate"]
    problems = []
    if not 2 <= len(slate) <= 32 or len(slate) % 2:
        problems.append(f"expected an even number of slate rows, got {len(slate)}")
    if not data["top"]:
        problems.append("no top plays")
    if len(data["allowed"]) != 32:
        problems.append(f"expected 32 defenses, got {len(data['allowed'])}")
    if not data["usage"] or not data["log"] or not data["off"]:
        problems.append("usage, log or position data is empty")
    for r in slate:
        if not r.get("pick") or not r.get("player") or "%" not in str(r.get("conf")):
            problems.append(f"incomplete pick for {r.get('team')}")
    if problems:
        sys.exit("Sheet check failed: " + "; ".join(problems))


def clean(v):
    if isinstance(v, float):
        return int(v) if v.is_integer() else round(v, 3)
    if isinstance(v, datetime.time):
        h = v.hour % 12 or 12
        return f"{h}:{v.minute:02d} {'AM' if v.hour < 12 else 'PM'}"
    if isinstance(v, str):
        return v.strip()
    return v


def rows(ws, **kw):
    return [[clean(c) for c in r] for r in ws.iter_rows(values_only=True, **kw)]


def table(ws, min_col=1, max_col=None):
    """Header row plus records, skipping rows with an empty first cell."""
    all_rows = rows(ws, min_col=min_col, max_col=max_col)
    head = all_rows[0]
    return [
        {h: v for h, v in zip(head, r) if h}
        for r in all_rows[1:]
        if r[0] not in (None, "")
    ]


def find_header(all_rows, first):
    return next(i for i, r in enumerate(all_rows) if r[0] == first)


def picks(ws):
    all_rows = rows(ws)
    top_i = find_header(all_rows, "#")
    top_head = all_rows[top_i]
    top = []
    for r in all_rows[top_i + 1 :]:
        if not isinstance(r[0], int):
            break
        top.append(dict(zip(top_head, r)))

    slate_i = find_header(all_rows, "Day")
    keys = ["day", "kick", "team", "opp", "teamPos", "venuePos", "oppPos", "oppVenue", "pick",
            "conf", "player", "rz", "tds", "first", "alt", "gameFirst", "noTd"]
    slate, day, kick = [], None, None
    for r in all_rows[slate_i + 1 :]:
        if not r[2]:
            continue
        day, kick = r[0] or day, r[1] or kick
        slate.append(dict(zip(keys, [day, kick] + r[2:])))

    title = all_rows[0][0] or ""
    week = int(re.search(r"WEEK (\d+)", title).group(1))
    return week, all_rows[1][0], top, slate


def this_week(ws, nick_to_abbr):
    all_rows = rows(ws)
    i = find_header(all_rows, "Slot")
    keys = ["slot", "kick", "team", "opp", "pick", "lean", "teamTd", "oppTd", "top", "share",
            "rzShare", "i10Share", "second", "secondShare", "rzRun", "i10Run", "oppRzRun"]
    out, slot, kick = [], None, None
    for r in all_rows[i + 1 :]:
        if not r[2]:
            continue
        slot, kick = r[0] or slot, r[1] or kick
        rec = dict(zip(keys, [slot, kick] + r[2:]))
        rec["abbr"] = nick_to_abbr.get(rec["team"], rec["team"])
        out.append(rec)
    return out


def position_tds(ws):
    """Rush/rec/pass TD totals by team and position, for and against."""
    off = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0]))
    dfn = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0]))
    nick, refreshed = {}, ""
    for r in ws.iter_rows(min_row=1, values_only=True):
        for c in r:
            if isinstance(c, str) and c.startswith("Last refreshed"):
                refreshed = c.replace("Last refreshed:", "").strip()
        if len(r) > 11 and r[9] and r[9] != "Nick" and r[11]:
            nick[r[9]] = r[11]
        if r[0] in ("QB", "RB", "WR", "TE"):
            pa, ru, re_ = r[4] or 0, r[5] or 0, r[6] or 0
            for bucket, team in ((off, r[1]), (dfn, r[2])):
                t = bucket[team][r[0]]
                t[0] += int(ru)
                t[1] += int(re_)
                t[2] += int(pa)
    return off, dfn, nick, refreshed


def nfl_avg(ws):
    out = {}
    for r in rows(ws):
        vals = [v for v in r if v is not None]
        if vals and vals[0] in ("QB", "RB", "WR", "TE") and vals[0] not in out:
            out[vals[0]] = vals[-1]
    return out


def main(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    week, note, top, slate = picks(wb["1st TD Picks"])
    off, dfn, nick, refreshed = position_tds(wb["POS DATA"])

    usage_rows = rows(wb["USAGE DATA"], max_col=18)
    usage_head = usage_rows[0]
    usage = [dict(zip(usage_head, r)) for r in usage_rows[1:] if r[1]]

    data = {
        "week": week,
        "note": note,
        "refreshed": refreshed,
        "top": top,
        "slate": slate,
        "thisWeek": this_week(wb["This Weeks TDs"], nick),
        "allowed": table(wb["TDs Allowed by Pos"]),
        "firstScored": table(wb["1st TD Scored"]),
        "firstAllowed": table(wb["1st TD Allowed"]),
        "log": table(wb["1st TD Log"]),
        "usage": usage,
        "off": off,
        "dfn": dfn,
        "nflAvg": nfl_avg(wb["Matchup TDs"]),
        "nicks": {v: k for k, v in nick.items()},
    }
    validate(data)
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    with open(os.path.join(HERE, "template.html"), encoding="utf-8") as f:
        html = f.read()
    with open(os.path.join(HERE, "index.html"), "w", encoding="utf-8") as f:
        f.write('<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n')
        f.write(html.replace("/*__DATA__*/null", payload))
    print(f"Week {week}: {len(slate)} picks, {len(data['allowed'])} defenses, "
          f"{len(usage)} players, {len(data['log'])} log rows. Refreshed {refreshed}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else download())
