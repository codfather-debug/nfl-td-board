# NFL TD Board

A one-page board that gives each NFL player's chance of a touchdown this week: any TD, their
team's first TD, and the game's first TD. It is rebuilt from the "2026 NEW TDs allowed" Google
Sheet and published by GitHub Actions.

## How it fits together

| File | What it does |
| --- | --- |
| `build.py` | Reads the sheet, adds snap counts, injuries and betting lines, scores every player, records this week's plays, grades finished games, and writes `index.html`. |
| `template.html` | The page. `build.py` drops the data into it. |
| `model/model.json` | The fitted model: its weights, and the curve that turns its raw number into a tested chance. |
| `model/features.py`, `model/train.py` | Rebuild `model.json` from nflverse play-by-play. Run by hand. |
| `results/history.json` | Every play, recorded before kickoff and graded after the final. |
| `tests/test_build.py` | Checks `build.py`, and that the page scores players exactly as `build.py` does. |
| `.github/workflows/refresh.yml` | Rebuilds daily, and again before kickoffs. Publishes only if the tests pass and something changed. |

## Everyday commands

    pip install -r requirements.txt
    python3 build.py                      # rebuild index.html from the live sheet
    python3 -m unittest discover tests    # run the checks

## Retraining the model

Do this every few weeks during the season, so the model has seen the latest games.

    pip install -r model/requirements.txt
    python3 model/train.py model/data     # downloads six seasons, writes model/model.json
    python3 build.py && python3 -m unittest discover tests

`train.py` prints how the model did on seasons it wasn't built from. The same numbers appear on
the page's Results tab.

## The model in one paragraph

Two logistic regressions, one for any TD and one for the team's first TD, read seven things about a
player: touches per game, share of the team's red-zone chances, share of chances inside the 10,
snap share in the last game played, the team's projected points from the betting line, the
red-zone share of teammates at the same position who are out, and position. A calibration curve then turns the raw output into the rate at which that output came
true in seasons the model wasn't fit on. The chance of the game's first TD is the team's-first
chance times the chance, from the point spread, that the team scores first.

## Two places to keep in step

The scoring is written twice: in `build.py`, so plays can be recorded and graded, and in
`template.html`, so the Update now button can rescore in the browser. Change both together. The
test `test_page_matches_build` fails if they disagree. The same goes for the sheet parser
(`picks`, `this_week`, `position_tds` in `build.py`; `parseWorkbook` in `template.html`).

## New season

Change `SEASON` in `build.py` and `SEASONS` in `model/features.py`, point `SHEET_ID` at the new
sheet, and retrain.
