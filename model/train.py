#!/usr/bin/env python3
"""Fit the TD model and write model.json. Run by hand every few weeks, not in the daily refresh.

Usage: train.py data_dir   (needs numpy, pandas and scikit-learn; data is downloaded into data_dir)

Everything reported, and the calibration the app shows, is out of sample: each season is scored
by a model fit only on the other seasons.
"""
import json
import os
import sys

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import features

# A "sqrt_" prefix means the square root of that number: the first few touches matter more than the last few.
FEATURES = ["sqrt_oppg", "sqrt_rzsh", "sqrt_i10sh", "snaplast", "imp", "missrz", "isRB", "isWR", "isTE", "isQB"]
CALIBRATION_QUANTILES = [0, .1, .2, .3, .4, .5, .6, .7, .8, .9, .95, .975, .99, 1]
PLAY_MIN = 0.25         # An any-TD chance at or above this is tracked as a play.
BANDS = {"any": [0, .15, .25, .35, .45, 1], "first": [0, .05, .10, .15, .20, 1]}


def add_inputs(d):
    for f in FEATURES:
        if f.startswith("sqrt_"):
            d[f] = np.sqrt(d[f[5:]])
    return d


def fit(df, target):
    return make_pipeline(StandardScaler(), LogisticRegression(C=.3, max_iter=3000)).fit(df[FEATURES], df[target])


def held_out(m, target):
    """Each season's predictions from a model that never saw that season."""
    p = pd.Series(np.nan, index=m.index)
    for season in sorted(m.season.unique()):
        test = m.season == season
        p[test] = fit(m[~test], target).predict_proba(m.loc[test, FEATURES])[:, 1]
    return p


def calibration(p, y):
    """A rising curve from the model's raw number to how often that happened, as points to draw lines between."""
    edges = np.unique(np.quantile(p, CALIBRATION_QUANTILES))
    bins = pd.cut(p, edges, include_lowest=True)
    g = pd.DataFrame({"p": p, "y": y}).groupby(bins, observed=True).agg(x=("p", "mean"), y=("y", "mean"), n=("y", "size"))
    rising = IsotonicRegression(increasing=True).fit(g.x, g.y, sample_weight=g.n).predict(g.x)
    return {"x": [round(float(v), 4) for v in g.x], "y": [round(float(v), 4) for v in rising]}


def calibrate(curve, p):
    return np.interp(p, curve["x"], curve["y"])


def nested_calibrated(m, target):
    """Calibrated held-out predictions where the curve, too, never saw the season it is applied to."""
    out = pd.Series(np.nan, index=m.index)
    for season in sorted(m.season.unique()):
        rest = m[m.season != season]
        curve = calibration(held_out(rest, target).values, rest[target].values)
        out[m.season == season] = calibrate(curve, fit(rest, target).predict_proba(m.loc[m.season == season, FEATURES])[:, 1])
    return out


def report(p, y, edges):
    """How the probabilities shown by the app did on seasons they weren't built from."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    clipped = np.clip(p, 1e-4, 1 - 1e-4)
    band = pd.cut(p, edges, include_lowest=True, right=False, labels=False)
    bands = [{"min": edges[i], "n": int((band == i).sum()), "predicted": round(float(p[band == i].mean()), 3),
              "actual": round(float(y[band == i].mean()), 3)} for i in range(len(edges) - 1) if (band == i).any()]
    return {"n": len(y), "baseRate": round(float(y.mean()), 4), "auc": round(float(roc_auc_score(y, p)), 3),
            "logLoss": round(float(log_loss(y, clipped)), 4),
            "logLossBaseRate": round(float(log_loss(y, np.full(len(y), y.mean()))), 4), "bands": bands}


def coefficients(model):
    scaler, logit = model[0], model[1]
    coef = logit.coef_[0] / scaler.scale_
    return {"intercept": float(logit.intercept_[0] - (coef * scaler.mean_).sum()),
            **{f: float(c) for f, c in zip(FEATURES, coef)}}


def game_first_slope(d):
    """Chance a team scores the game's first TD, from the point spread alone."""
    g = d.drop_duplicates(["season", "game_id", "team"]).dropna(subset=["scored_first", "margin"])
    # No intercept, so a matchup's two teams add to 100%.
    model = LogisticRegression(fit_intercept=False, C=1000).fit(g[["margin"]], g.scored_first)
    return float(model.coef_[0][0]), g.game_id.nunique()


def main(data_dir):
    features.download(data_dir, refresh_latest="--no-refresh" not in sys.argv)
    d = add_inputs(features.build(data_dir))
    m = features.model_rows(d)
    slope, games = game_first_slope(d)

    raw_any, raw_first = held_out(m, "any"), held_out(m, "tf")
    cal_any, cal_first = nested_calibrated(m, "any"), nested_calibrated(m, "tf")
    # A player scores the game's first TD when they score their team's first and their team scores first.
    team_first = 1 / (1 + np.exp(-slope * m.margin))
    raw_game = cal_first * team_first
    game_scale = float(m.gf.sum() / raw_game.sum())

    latest = int(d[d.season == d.season.max()].week.max())
    model = {
        "features": FEATURES,
        "any": coefficients(fit(m, "any")),
        "first": coefficients(fit(m, "tf")),
        "anyCal": calibration(raw_any.values, m["any"].values),
        "firstCal": calibration(raw_first.values, m.tf.values),
        "gameFirstSlope": slope,
        "gameFirstScale": round(game_scale, 4),
        "playMin": PLAY_MIN,
        "heldOut": {
            "any": report(cal_any, m["any"], BANDS["any"]),
            "first": report(cal_first, m.tf, BANDS["first"]),
            "gameFirst": report(raw_game * game_scale, m.gf, [0, .025, .05, .075, .10, 1]),
        },
        "sample": {"playerGames": len(m), "games": games,
                   "seasons": f"{d.season.min()} to {d.season.max()}, through Week {latest}"},
    }
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "model.json"), "w") as f:
        json.dump(model, f, indent=1)
    print(json.dumps(model, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
