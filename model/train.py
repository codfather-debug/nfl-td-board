#!/usr/bin/env python3
"""Fit the TD model and write model.json. Run by hand between seasons, not in the daily refresh.

Usage: train.py features.pkl data_dir  (features.pkl comes from features.py)
data_dir holds nflverse pbp/stats/snap files; needs numpy, pandas and scikit-learn.
Tier hit rates are out of sample only: fit on one season, graded on the other.
"""
import json
import os
import sys

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline

FEATURES = ["oppg", "rzsh", "i10sh", "snaplast", "imp", "isRB", "isWR", "isTE", "isQB"]
ANY_EDGES = [0, .2, .3, .4, .5, .6, 1]
FIRST_EDGES = [0, .1, .2, .3, .4, 1]


def fit(df, target):
    return make_pipeline(StandardScaler(), LogisticRegression(C=.3, max_iter=2000)).fit(df[FEATURES], df[target])


def tiers(scores, edges, outcomes):
    frame = pd.DataFrame({"p": scores, **outcomes})
    frame["b"] = pd.cut(frame.p, edges, labels=False, include_lowest=True)
    out = []
    best = {k: 0 for k in outcomes}
    for i in range(len(edges) - 1):
        part = frame[frame.b == i]
        row = {"min": edges[i], "n": len(part)}
        for k in outcomes:
            best[k] = max(best[k], float(part[k].mean()))  # Higher tiers never show a lower rate.
            row[k] = round(best[k], 3)
        out.append(row)
    return out


def coefficients(model):
    scaler, logit = model[0], model[1]
    coef = logit.coef_[0] / scaler.scale_
    return {"intercept": float(logit.intercept_[0] - (coef * scaler.mean_).sum()),
            **{f: float(c) for f, c in zip(FEATURES, coef)}}


def game_first_slope(data_dir):
    rows = []
    for suffix in ("25", ""):
        p = pd.read_csv(f"{data_dir}/pbp{suffix}.csv.gz", low_memory=False,
                        usecols=["game_id", "play_id", "season_type", "home_team", "touchdown", "td_team", "td_player_id", "spread_line"])
        p = p[p.season_type == "REG"]
        td = p[(p.touchdown == 1) & p.td_player_id.notna()].sort_values(["game_id", "play_id"])
        first = td.groupby("game_id").td_team.first()
        for game, g in p.groupby("game_id"):
            if game in first.index:
                rows.append((g.spread_line.iloc[0], int(first[game] == g.home_team.iloc[0])))
    x = pd.DataFrame(rows, columns=["m", "home"]).dropna()
    # Both sides of each game, no intercept, so a matchup's two teams add to 100%.
    both = np.r_[x.m, -x.m].reshape(-1, 1)
    y = np.r_[x.home, 1 - x.home]
    return float(LogisticRegression(fit_intercept=False, C=1000).fit(both, y).coef_[0][0]), len(x)


def main(path, data_dir):
    d = pd.read_pickle(path)
    m = d[d.played & (d.week >= 4) & d.pos.isin(["RB", "WR", "TE", "QB"])].copy()
    m[FEATURES[:5]] = m[FEATURES[:5]].apply(pd.to_numeric).fillna(0)
    for pos in ("RB", "WR", "TE", "QB"):
        m["is" + pos] = (m.pos == pos).astype(int)
    held_out = []
    for train, test in ((2025, 2026), (2026, 2025)):
        tr, te = m[m.season == train], m[m.season == test]
        held_out.append(te.assign(p_any=fit(tr, "any").predict_proba(te[FEATURES])[:, 1],
                                  p_tf=fit(tr, "tf").predict_proba(te[FEATURES])[:, 1]))
    h = pd.concat(held_out)
    slope, games = game_first_slope(data_dir)
    model = {
        "features": FEATURES,
        "any": coefficients(fit(m, "any")),
        "first": coefficients(fit(m, "tf")),
        "anyTiers": tiers(h.p_any.values, ANY_EDGES, {"hit": h["any"].values}),
        "firstTiers": tiers(h.p_tf.values, FIRST_EDGES, {"hit": h.tf.values, "game": h.gf.values}),
        "gameFirstSlope": slope,
        "sample": {"playerGames": len(m), "games": games, "seasons": "2025 and 2026 through Week 8"},
    }
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "model.json"), "w") as f:
        json.dump(model, f, indent=1)
    print(json.dumps({k: model[k] for k in ("any", "first", "anyTiers", "firstTiers", "gameFirstSlope", "sample")}, indent=0))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
