#!/usr/bin/env python3
"""Build the training table: one row per player per game, using only what was known before kickoff.

Every feature is defined the way build.py computes it on game day from the sheet's USAGE DATA
tab, so the model is trained on the same numbers it is later shown:

  gp        games the player took an offensive snap in, before this one
  oppg      targets plus carries per game played
  rzsh      share of the team's red-zone targets and carries, over all the team's games so far
  i10sh     the same, inside the 10
  snaplast  offensive snap share in the last game the player played
  imp       the team's projected points from the betting line
  missrz    red-zone share of teammates at the player's position who are sitting this game out

Usage: features.py data_dir out.pkl   (needs pandas; files are downloaded into data_dir if missing)
"""
import os
import re
import sys
import unicodedata
import urllib.request

import numpy as np
import pandas as pd

SEASONS = (2021, 2022, 2023, 2024, 2025, 2026)
POSITIONS = ("RB", "WR", "TE", "QB")
NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
SOURCES = {
    "pbp_{s}.csv.gz": NFLVERSE + "/pbp/play_by_play_{s}.csv.gz",
    "stats_{s}.csv": NFLVERSE + "/stats_player/stats_player_week_{s}.csv",
    "snaps_{s}.csv": NFLVERSE + "/snap_counts/snap_counts_{s}.csv",
}
PBP_COLUMNS = ["game_id", "play_id", "season_type", "week", "posteam", "home_team", "away_team", "yardline_100",
               "play_type", "two_point_attempt", "rusher_player_id", "receiver_player_id", "touchdown",
               "td_team", "td_player_id", "total_line", "spread_line"]


def norm_name(name):
    """Keep in step with norm_name in build.py."""
    name = unicodedata.normalize("NFKD", str(name))
    name = "".join(c for c in name if not unicodedata.combining(c)).lower()
    name = re.sub(r"[.'’\-]", "", name)
    name = re.sub(r"\s+(jr|sr|ii|iii|iv|v)$", "", name.strip())
    return re.sub(r"\s+", " ", name)


def download(data_dir, seasons=SEASONS, refresh_latest=True):
    """Fetch any missing files. The latest season changes weekly, so it is fetched again."""
    os.makedirs(data_dir, exist_ok=True)
    for season in seasons:
        for name, url in SOURCES.items():
            path = os.path.join(data_dir, name.format(s=season))
            if os.path.exists(path) and not (refresh_latest and season == max(seasons)):
                continue
            print(f"Downloading {os.path.basename(path)}")
            urllib.request.urlretrieve(url.format(s=season), path)


def match_snap_names(snaps, known):
    """Point snap-count names at the play-by-play name when only the first name differs (Bam/Zonovan Knight)."""
    by_surname = {}
    for (team, name), pos in known.items():
        by_surname.setdefault((team, pos, name.split()[-1]), []).append(name)
    def fix(row):
        if (row.team, row.k) in known:
            return row.k
        same = by_surname.get((row.team, row.pos, row.k.split()[-1]), [])
        return same[0] if len(same) == 1 else row.k
    return snaps.apply(fix, axis=1)


def missing_share(d):
    """By team, week and position: the red-zone share of players with two earlier games who sat this one out."""
    team_weeks = d[["team", "week", "prior_t_rz"]].drop_duplicates(["team", "week"])
    grid = d[["team", "k", "pos"]].drop_duplicates(["team", "k"]).merge(team_weeks, on="team")
    grid = grid.merge(d[["team", "k", "week", "rz"]].assign(played=1), on=["team", "k", "week"], how="left")
    grid = grid.fillna({"rz": 0, "played": 0}).sort_values(["team", "k", "week"])
    player = grid.groupby(["team", "k"])
    grid["gp"] = player.played.cumsum() - grid.played
    grid["share"] = (player.rz.cumsum() - grid.rz) / grid.prior_t_rz.clip(lower=1)
    out = grid[(grid.played == 0) & (grid.gp >= 2)]
    return out.groupby(["team", "week", "pos"], as_index=False).share.sum().rename(columns={"share": "missrz"})


def season_table(data_dir, season):
    pbp = pd.read_csv(os.path.join(data_dir, f"pbp_{season}.csv.gz"), usecols=PBP_COLUMNS, low_memory=False)
    pbp = pbp[pbp.season_type == "REG"]
    stats = pd.read_csv(os.path.join(data_dir, f"stats_{season}.csv"), low_memory=False)
    stats = stats[stats.season_type == "REG"]
    snaps = pd.read_csv(os.path.join(data_dir, f"snaps_{season}.csv"), low_memory=False)
    snaps = snaps[(snaps.game_type == "REG") & snaps.position.isin(POSITIONS) & (snaps.offense_snaps > 0)]

    key_of = stats.groupby("player_id").player_display_name.last().map(norm_name)
    known = {(t, norm_name(n)): p for t, n, p in zip(stats.team, stats.player_display_name, stats.position)}

    # Who played: one row per player per game with an offensive snap.
    rows = snaps.rename(columns={"position": "pos", "offense_pct": "snap"})[["week", "team", "player", "pos", "snap"]].copy()
    rows["k"] = rows.player.map(norm_name)
    rows["k"] = match_snap_names(rows, known)
    rows = rows.drop_duplicates(["week", "team", "k"])

    # Targets and carries, by field position.
    plays = pbp[(pbp.two_point_attempt != 1) & pbp.play_type.isin(["run", "pass"])].copy()
    plays["pid"] = np.where(plays.play_type == "run", plays.rusher_player_id, plays.receiver_player_id)
    plays = plays[plays.pid.notna()].rename(columns={"posteam": "team"})
    plays["k"] = plays.pid.map(key_of)
    plays["opp"] = 1
    plays["rz"] = (plays.yardline_100 <= 20).astype(int)
    plays["i10"] = (plays.yardline_100 <= 10).astype(int)
    usage = plays.groupby(["week", "team", "k"], as_index=False)[["opp", "rz", "i10"]].sum()
    team_usage = plays.groupby(["week", "team"], as_index=False)[["rz", "i10"]].sum().rename(columns={"rz": "t_rz", "i10": "t_i10"})

    # Touchdowns a bettor is paid on: any score by the player, passing excluded.
    td = pbp[(pbp.touchdown == 1) & pbp.td_player_id.notna()].sort_values(["game_id", "play_id"]).rename(columns={"td_team": "team"})
    td["k"] = td.td_player_id.map(key_of)
    tds = td.groupby(["week", "team", "k"]).size().rename("tds").reset_index()
    team_first = td.groupby(["game_id", "team"], as_index=False).first()[["week", "team", "k"]].assign(tf=1)
    game_first = td.groupby("game_id", as_index=False).first()[["week", "team", "k"]].assign(gf=1)

    d = rows
    for part in (usage, tds, team_first, game_first):
        d = d.merge(part, how="left", on=["week", "team", "k"])
    d = d.fillna({c: 0 for c in ("opp", "rz", "i10", "tds", "tf", "gf")})

    # The betting line gives each team's projected points.
    games = pbp.groupby("game_id").agg(week=("week", "first"), home=("home_team", "first"), away=("away_team", "first"),
                                       total=("total_line", "first"), spread=("spread_line", "first")).reset_index()
    sides = pd.concat([
        games.assign(team=games.home, is_home=1, imp=(games.total + games.spread) / 2, margin=games.spread),
        games.assign(team=games.away, is_home=0, imp=(games.total - games.spread) / 2, margin=-games.spread),
    ])[["game_id", "week", "team", "is_home", "imp", "margin"]]
    first_team = td.groupby("game_id").team.first()
    sides["scored_first"] = (sides.game_id.map(first_team) == sides.team).where(sides.game_id.isin(first_team.index)).astype(float)
    d = d.merge(sides, on=["week", "team"])

    # Team totals over every earlier game, whether or not the player was in it. This is the sheet's definition.
    team_usage = team_usage.sort_values("week")
    for c in ("t_rz", "t_i10"):
        team_usage["prior_" + c] = team_usage.groupby("team")[c].cumsum() - team_usage[c]
    d = d.merge(team_usage[["week", "team", "prior_t_rz", "prior_t_i10"]], on=["week", "team"], how="left")

    # The player's own totals over earlier games on this team.
    d = d.sort_values(["team", "k", "week"])
    player = d.groupby(["team", "k"])
    d["gp"] = player.cumcount()
    for c in ("opp", "rz", "i10", "tds"):
        d["prior_" + c] = player[c].cumsum() - d[c]
    d["snaplast"] = player.snap.shift(1)
    games_played = d.gp.clip(lower=1)
    d["oppg"] = d.prior_opp / games_played
    d["tdg"] = d.prior_tds / games_played
    d["rzsh"] = d.prior_rz / d.prior_t_rz.clip(lower=1)
    d["i10sh"] = d.prior_i10 / d.prior_t_i10.clip(lower=1)
    d = d.merge(missing_share(d), on=["team", "week", "pos"], how="left").fillna({"missrz": 0})
    d["any"] = (d.tds > 0).astype(int)
    d["season"] = season
    return d


def build(data_dir, seasons=SEASONS):
    d = pd.concat([season_table(data_dir, s) for s in seasons], ignore_index=True)
    for pos in POSITIONS:
        d["is" + pos] = (d.pos == pos).astype(int)
    return d


def model_rows(d):
    """The games the model is fit on: enough history to trust a share, and a player the sheet would list."""
    return d[(d.week >= 4) & (d.gp >= 2) & (d.prior_opp > 0)].copy()


if __name__ == "__main__":
    download(sys.argv[1])
    table = build(sys.argv[1])
    table.to_pickle(sys.argv[2])
    print(table.groupby("season").agg(rows=("any", "size"), scored=("any", "mean"), last_week=("week", "max"), games=("game_id", "nunique")))
