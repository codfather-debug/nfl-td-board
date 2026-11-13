#!/usr/bin/env python3
"""Rebuild index.html from the "2026 NEW TDs allowed" sheet.

Usage: python3 build.py [path/to/export.xlsx]
With no path, downloads the sheet (it must be viewable by anyone with the link).

The player scoring here is repeated in template.html so the page can rescore after its own
Update button. tests/test_build.py checks that the two give the same numbers.
"""
import collections
import csv
import datetime
import functools
import io
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.request
import zoneinfo

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
SEASON = 2026
SHEET_ID = "1fwtkoY2KveC6TJPRyn0pvHPseVw2_4j2Qbud1x0q1pU"
EXPORT_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=xlsx"

NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
SNAPS_URL = f"{NFLVERSE}/snap_counts/snap_counts_{SEASON}.csv"
STATS_URL = f"{NFLVERSE}/stats_player/stats_player_week_{SEASON}.csv"
LAST_STATS_URL = f"{NFLVERSE}/stats_player/stats_player_week_{SEASON - 1}.csv"
LINES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
EASTERN = zoneinfo.ZoneInfo("America/New_York")  # The schedule lists kickoffs in Eastern time.
RECENT_WEIGHTS = (0.6, 0.25, 0.15)  # Team's last game first.
WATCHED_STATUSES = {"Active", "Out", "Doubtful", "Questionable", "Injured Reserve", "Suspension"}
UNAVAILABLE = {"Out", "Doubtful", "Injured Reserve", "Suspension"}
POSITIONS = ("RB", "WR", "TE", "QB")
# What a sportsbook pays an anytime-TD bet on: every score by the player except a pass thrown.
PLAYER_TD_COLUMNS = ("rushing_tds", "receiving_tds", "special_teams_tds", "fumble_recovery_tds")
LEAGUE_AVG_POINTS = 23      # Used for a team with no betting line yet.
TEAM_FIRST_CAP = 0.9        # A team's players can't add up to more than this chance of its first TD.
HISTORY_PATH = os.path.join(HERE, "results", "history.json")


@functools.lru_cache(maxsize=None)
def fetch(url):
    """Download once per build, retrying a source that is briefly down."""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                return resp.read()
        except OSError:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))


def download():
    return io.BytesIO(fetch(EXPORT_URL))


@functools.lru_cache(maxsize=None)
def fetch_csv(url):
    return tuple(csv.DictReader(io.StringIO(fetch(url).decode("utf-8"))))


def season_games():
    return [g for g in fetch_csv(LINES_URL) if g["season"] == str(SEASON)]


def number(value):
    return float(value or 0)


def player_tds(row):
    return int(sum(number(row.get(c)) for c in PLAYER_TD_COLUMNS))


def norm_name(name):
    """Keep in step with normName in template.html and norm_name in model/features.py."""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c)).lower()
    name = re.sub(r"[.'’\-]", "", name)
    name = re.sub(r"\s+(jr|sr|ii|iii|iv|v)$", "", name.strip())
    return re.sub(r"\s+", " ", name)


def kickoff_iso(gameday, gametime):
    """Kickoff with the right Eastern offset, which changes with daylight saving. Same as kickoffIso in template.html."""
    return datetime.datetime.fromisoformat(f"{gameday}T{gametime}").replace(tzinfo=EASTERN).isoformat()


def game_lines(week):
    """Projected points and kickoff by team. Keep in step with parseLines in template.html.

    spread_line is positive when the home team is favored.
    """
    out = {}
    for g in season_games():
        if g["week"] != str(week) or not g["spread_line"] or not g["total_line"]:
            continue
        spread, total = float(g["spread_line"]), float(g["total_line"])
        info = {"total": total, "kickoff": kickoff_iso(g["gameday"], g["gametime"]), "final": bool(g["result"])}
        out[g["home_team"]] = {**info, "margin": spread, "imp": (total + spread) / 2}
        out[g["away_team"]] = {**info, "margin": -spread, "imp": (total - spread) / 2}
    return out


def counted_games(refreshed):
    """(team, week) of games played before the sheet's refresh, which its usage totals include."""
    cutoff = datetime.datetime.strptime(refreshed.split(",")[0], "%m/%d/%Y").date().isoformat()
    out = set()
    for g in season_games():
        if g["game_type"] == "REG" and g["gameday"] < cutoff:
            out |= {(g["home_team"], int(g["week"])), (g["away_team"], int(g["week"]))}
    return out


def recent_usage(usage, counted):
    """Games played, recent snap share and TDs for each player in the usage tab.

    Games and TDs cover the same games as the sheet's usage totals, so per-game rates are right.
    """
    snaps = [r for r in fetch_csv(SNAPS_URL) if r["game_type"] == "REG"]
    stats = [r for r in fetch_csv(STATS_URL) if r["season_type"] == "REG"]
    by_player = collections.defaultdict(dict)
    positions = {}
    team_weeks = collections.defaultdict(set)
    for r in snaps:
        week = int(r["week"])
        key = (r["team"], norm_name(r["player"]))
        team_weeks[r["team"]].add(week)
        by_player[key][week] = number(r["offense_pct"])
        positions[key] = r["position"]
    tds = collections.Counter()
    for r in stats:
        if (r["team"], int(r["week"])) in counted:
            tds[(r["team"], norm_name(r["player_display_name"]))] += player_tds(r)

    out = collections.defaultdict(dict)
    for u in usage:
        team, name = u["team"], norm_name(u["player"])
        key = (team, name)
        if key not in by_player:
            # Same player, different first name in the snap data (Bam/Zonovan Knight).
            same = [k for k, p in positions.items()
                    if k[0] == team and p == u["pos"] and k[1].split()[-1:] == name.split()[-1:]]
            if len(same) != 1:
                continue
            key = same[0]
        weeks = sorted(team_weeks[team])
        last = [by_player[key].get(w, 0) for w in reversed(weeks[-len(RECENT_WEIGHTS):])]
        weights = RECENT_WEIGHTS[: len(last)]
        played = sorted(w for w, v in by_player[key].items() if v > 0)
        out[team][u["player"]] = {
            "gp": sum(1 for w in played if (team, w) in counted),
            "last": last[0] if last else 0,
            "lastPlayed": by_player[key][played[-1]] if played else 0,
            "avgSnap": round(sum(by_player[key][w] for w in played) / len(played), 3) if played else 0,
            "recent": round(sum(a * b for a, b in zip(weights, last)) / sum(weights), 3) if last else 0,
            "tds": tds[(team, name)],
        }
    return out


def qb_prior():
    """Each quarterback's games last season and the games he scored in. Keep in step with qb_prior in model/features.py."""
    out = collections.defaultdict(lambda: [0, 0])
    for r in fetch_csv(LAST_STATS_URL):
        if r["season_type"] == "REG" and r["position"] == "QB" and number(r["attempts"]) + number(r["carries"]) > 0:
            games = out[norm_name(r["player_display_name"])]
            games[0] += 1
            games[1] += player_tds(r) > 0
    return dict(out)


def injuries(nicks):
    """Latest ESPN status by team and player. Keep in step with parseInjuries in template.html."""
    latest = {}
    for team in json.loads(fetch(INJURIES_URL)).get("injuries", []):
        abbr = next((a for a, n in nicks.items() if team.get("displayName", "").endswith(n)), None)
        if not abbr:
            continue
        for item in team.get("injuries", []):
            key = f"{abbr}|{norm_name(item['athlete']['displayName'])}"
            if key not in latest or item.get("date", "") > latest[key][0]:
                latest[key] = (item.get("date", ""), item.get("status"))
    return {k: v[1] for k, v in latest.items() if v[1] in WATCHED_STATUSES}


# ---- Player scoring. model/model.json holds the fitted model; template.html repeats this section. ----

def sigmoid(x):
    return 1 / (1 + math.exp(-x))


def calibrate(curve, p):
    """Turn the model's raw number into how often it came true out of sample. Same as calibrate in template.html."""
    xs, ys = curve["x"], curve["y"]
    if p <= xs[0]:
        return ys[0]
    for i in range(1, len(xs)):
        if p <= xs[i]:
            return ys[i - 1] + (ys[i] - ys[i - 1]) * (p - xs[i - 1]) / (xs[i] - xs[i - 1])
    return ys[-1]


def model_inputs(model, u, x, imp, missrz, qb_games):
    """The numbers the model reads for one player. Same as modelInputs in template.html."""
    prior = model["qbPrior"]
    games, scored = qb_games.get(norm_name(u["player"]), (0, 0))
    base = {"qbprev": (u["pos"] == "QB") * (scored + prior["weight"] * prior["avg"]) / (games + prior["weight"]),
            "oppg": ((u["targets"] or 0) + (u["carries"] or 0)) / x["gp"], "rzsh": u["rz_opp_share"] or 0,
            "i10sh": u["i10_opp_share"] or 0, "snaplast": x["lastPlayed"] or 0, "imp": imp, "missrz": missrz,
            **{"is" + p: int(u["pos"] == p) for p in POSITIONS}}
    return {f: math.sqrt(max(base[f[5:]], 0)) if f.startswith("sqrt_") else base[f] for f in model["features"]}


def raw_chance(model, coefficients, inputs):
    return sigmoid(coefficients["intercept"] + sum(coefficients[f] * inputs[f] for f in model["features"]))


def fair_odds(p):
    """The American odds at which a bet with this chance breaks even. Same as fairOdds in template.html."""
    if not 0 < p < 1:
        return None
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def starter_score(u, x, first):
    """Production per game played, weighted by snaps. Same as starterScore in template.html."""
    rz = (u["rz_targets"] or 0) + (u["rz_carries"] or 0)
    return (rz + 2 * x["tds"] + 3 * first) / x["gp"] * (0.6 * (x["lastPlayed"] or 0) + 0.4 * (x["avgSnap"] or 0))


def choose_pick(sheet_player, position, team_players):
    """The healthy starter at the sheet's position. Same as choosePick in template.html."""
    at_position = [p for p in team_players if p["pos"] == position]
    available = sorted((p for p in at_position if not p["unavailable"]), key=lambda p: -p["starter"])
    if not available:
        return None
    best = available[0]
    sheet = next((p for p in at_position if p["player"] == sheet_player), None)
    if not sheet or sheet["unavailable"]:
        return best
    clear = best is not sheet and best["starter"] >= 1.25 * sheet["starter"] \
        and best["lastPlayed"] >= sheet["lastPlayed"] + 0.10 and best["rzPerGame"] >= sheet["rzPerGame"]
    return best if clear else sheet


def score_players(data):
    """Each player's chance of a TD this week. Same as scorePlayers in template.html."""
    model, rows, players = data["model"], {r["team"]: r for r in data["slate"]}, []
    firsts = collections.Counter((l["Team"], l.get("Team 1st TD Scorer")) for l in data["log"])
    listed = []
    missing = collections.Counter()  # Red-zone share of players who are out, by team and position.
    for u in data["usage"]:
        x = data["recent"].get(u["team"], {}).get(u["player"])
        if u["team"] not in rows or not x or x["gp"] < 2 or u["pos"] not in POSITIONS:
            continue
        status = data["injuries"].get(f"{u['team']}|{norm_name(u['player'])}", "")
        unavailable = status in UNAVAILABLE or (not x["recent"] and status != "Active")
        if unavailable:
            missing[(u["team"], u["pos"])] += u["rz_opp_share"] or 0
        listed.append((u, x, status, unavailable))
    for u, x, status, unavailable in listed:
        line = data["lines"].get(u["team"])
        # A player's chance rises when a teammate at their position is out. Their own absence doesn't count.
        missrz = missing[(u["team"], u["pos"])] - ((u["rz_opp_share"] or 0) if unavailable else 0)
        inputs = model_inputs(model, u, x, line["imp"] if line else LEAGUE_AVG_POINTS, missrz, data.get("qbPrev") or {})
        players.append({"player": u["player"], "team": u["team"], "pos": u["pos"], "status": status,
                        "unavailable": unavailable, "missrz": missrz,
                        "starter": starter_score(u, x, firsts[(u["team"], u["player"])]),
                        "rzPerGame": ((u["rz_targets"] or 0) + (u["rz_carries"] or 0)) / x["gp"],
                        "lastPlayed": x["lastPlayed"] or 0, "hasLine": bool(line),
                        "rawAny": raw_chance(model, model["any"], inputs),
                        "anyRate": calibrate(model["anyCal"], raw_chance(model, model["any"], inputs)),
                        "firstRate": calibrate(model["firstCal"], raw_chance(model, model["first"], inputs))})
    # One team's first TD goes to one player, so a team's chances can't add up to more than the cap.
    totals = collections.Counter()
    for p in players:
        if not p["unavailable"]:
            totals[p["team"]] += p["firstRate"]
    for p in players:
        if totals[p["team"]] > TEAM_FIRST_CAP:
            p["firstRate"] *= TEAM_FIRST_CAP / totals[p["team"]]
        # The game's first TD: the player scores their team's first, and their team scores before the other one.
        line = data["lines"].get(p["team"])
        team_first = sigmoid(model["gameFirstSlope"] * line["margin"]) if line else 0.5
        p["gameRate"] = min(p["firstRate"], p["firstRate"] * team_first * model["gameFirstScale"])
    players.sort(key=lambda p: (-p["anyRate"], -p["rawAny"]))
    return players


def snapshot_plays(data, history, now):
    """Replace the recorded plays for games that haven't kicked off. Started games stay locked."""
    if not data["recent"] or not data["injuriesAt"] or not data["lines"]:
        return
    players = score_players(data)
    week, play_min = data["week"], data["model"]["playMin"]
    for r in data["slate"]:
        team, line = r["team"], data["lines"].get(r["team"])
        if not line or line["final"] or now >= datetime.datetime.fromisoformat(line["kickoff"]):
            continue
        mine = [p for p in players if p["team"] == team]
        available = [p for p in mine if not p["unavailable"]]
        if not available:
            continue
        chosen = choose_pick(r["player"], r["pick"].split()[0], mine)
        pick = chosen["player"] if chosen else None
        entries = {}
        for p in available:
            if p["anyRate"] >= play_min or p["player"] == pick:
                entries[p["player"]] = {k: (round(p[k], 3) if isinstance(p[k], float) else p[k])
                                        for k in ("player", "pos", "status", "anyRate", "firstRate", "gameRate")}
                entries[p["player"]].update(isPlay=p["anyRate"] >= play_min, isPick=p["player"] == pick, isSheet=False)
        sheet = r["player"]
        entries.setdefault(sheet, {"player": sheet, "pos": r["pick"].split()[0], "status": "", "isPlay": False, "isPick": False})["isSheet"] = True
        history["plays"] = [e for e in history["plays"] if not (e["week"] == week and e["team"] == team)]
        history["plays"] += [{"week": week, "team": team, "opp": r["opp"], "imp": line["imp"], "lockedAt": now.strftime("%Y-%m-%dT%H:%MZ"), **e}
                             for e in entries.values()]


def grade_plays(history, log):
    """Fill in results once a game is final and its player data has been published."""
    pending = [e for e in history["plays"] if "anyTd" not in e or "teamFirst" not in e]
    if not pending:
        return
    finals = set()
    for g in season_games():
        if g["result"]:
            finals |= {(g["home_team"], int(g["week"])), (g["away_team"], int(g["week"]))}
    snaps, tds, stat_games = {}, collections.Counter(), set()
    snap_games = set()
    for r in fetch_csv(SNAPS_URL):
        snap_games.add((r["team"], int(r["week"])))
        snaps[(r["team"], norm_name(r["player"]), int(r["week"]))] = number(r["offense_pct"])
    for r in fetch_csv(STATS_URL):
        stat_games.add((r["team"], int(r["week"])))
        tds[(r["team"], norm_name(r["player_display_name"]), int(r["week"]))] += player_tds(r)
    firsts = {(l["Team"], int(l["Week"])): l for l in log}
    for e in pending:
        game, name = (e["team"], e["week"]), norm_name(e["player"])
        if game not in finals:
            continue
        if "anyTd" not in e and game in stat_games and game in snap_games:
            surname = [k for k in snaps if k[0] == e["team"] and k[2] == e["week"] and k[1].split()[-1:] == name.split()[-1:]]
            e["played"] = snaps.get((e["team"], name, e["week"]), 0) > 0 or tds[(e["team"], name, e["week"])] > 0 or (
                len(surname) == 1 and snaps[surname[0]] > 0)
            e["anyTd"] = tds[(e["team"], name, e["week"])] > 0
        row = firsts.get(game)
        if "teamFirst" not in e and row:
            e["teamFirst"] = norm_name(row.get("Team 1st TD Scorer") or "") == name
            e["gameFirst"] = norm_name(row.get("Game 1st TD Scorer") or "") == name


def update_history(data):
    history = {"plays": []}
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH, encoding="utf-8") as f:
            history = json.load(f)
    try:
        snapshot_plays(data, history, datetime.datetime.now(datetime.timezone.utc))
    except Exception as e:
        print(f"Could not record this week's plays: {e}")
    try:
        grade_plays(history, data["log"])
    except Exception as e:
        print(f"Could not grade results: {e}")
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    # Write beside the file and swap it in, so a crash can't leave half a history.
    with open(HISTORY_PATH + ".tmp", "w", encoding="utf-8") as f:
        json.dump(history, f, indent=0, ensure_ascii=False)
    os.replace(HISTORY_PATH + ".tmp", HISTORY_PATH)
    return history


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
        if r[0] in POSITIONS:
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
        if vals and vals[0] in POSITIONS and vals[0] not in out:
            out[vals[0]] = vals[-1]
    return out


def write_chances(data, path=os.path.join(HERE, "chances.json")):
    """Each player's any-TD chance, published beside the page. The NFL Stat Board shows these for its Anytime TD cards."""
    if not data["recent"] or not data["lines"]:
        return  # Without snaps or lines the chances are rough; leave the last good file.
    out = {"season": SEASON, "week": data["week"], "builtAt": data["injuriesAt"], "tested": data["model"]["heldOut"]["any"],
           "players": [{"team": p["team"], "key": norm_name(p["player"]), "player": p["player"], "pos": p["pos"],
                        "anyRate": round(p["anyRate"], 4)} for p in score_players(data)]}
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)
    os.replace(path + ".tmp", path)


def write_page(data, path=os.path.join(HERE, "index.html")):
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    with open(os.path.join(HERE, "template.html"), encoding="utf-8") as f:
        html = f.read()
    with open(path, "w", encoding="utf-8") as f:
        f.write('<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n')
        f.write(html.replace("/*__DATA__*/null", payload))


def parse_sheet(path):
    """Everything the page needs from the sheet. Same as parseWorkbook in template.html; tests check they agree."""
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
    return data


def main(path):
    data = parse_sheet(path)
    week, slate, usage, refreshed = data["week"], data["slate"], data["usage"], data["refreshed"]
    # These sources are extras: if one is down, the page falls back to the sheet's picks.
    try:
        data["recent"] = recent_usage(usage, counted_games(refreshed))
    except Exception as e:
        print(f"Snap data unavailable: {e}")
        data["recent"] = {}
    try:
        data["injuries"] = injuries(data["nicks"])
        data["injuriesAt"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception as e:
        print(f"Injury data unavailable: {e}")
        data["injuries"], data["injuriesAt"] = {}, ""
    try:
        data["qbPrev"] = qb_prior()
    except Exception as e:
        print(f"Last season's stats unavailable: {e}")
        data["qbPrev"] = {}
    try:
        data["lines"] = game_lines(week)
    except Exception as e:
        print(f"Betting lines unavailable: {e}")
        data["lines"] = {}
    with open(os.path.join(HERE, "model", "model.json"), encoding="utf-8") as f:
        data["model"] = json.load(f)
    data["season"] = SEASON
    data["results"] = update_history(data)
    write_page(data)
    write_chances(data)
    print(f"Week {week}: {len(slate)} picks, {len(data['allowed'])} defenses, "
          f"{len(usage)} players, {len(data['log'])} log rows. Refreshed {refreshed}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else download())
