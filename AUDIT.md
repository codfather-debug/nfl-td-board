# Audit, 12 November 2026

Four reviews of the NFL TD Board, what each found, and what was changed. The copy from before
these changes is in `../nfl-td-app-backup-2026-11-12`.

## 1. Statistics and probability

| Finding | Evidence | Change |
| --- | --- | --- |
| The page showed six rounded tiers, not each player's chance, and the top tiers were overstated. | Players shown "60%" had raw scores averaging 68%; the 50 to 60 tier scored 42% when tested. A rule that forced higher tiers never to show a lower rate pushed numbers up. | Each player now gets their own chance from a calibration curve built on out-of-sample predictions. Tested: shown 53.3%, scored 54.2%; shown 29.6%, scored 29.7%. |
| The lowest tracked tier said 29% and has hit 18% live (13 of 71). | Results history, Weeks 8 and 9. | Same fix. The tier label was the average of a wide band, not those players' chance. |
| A team's first-TD chances could add to more than 100%. | Up to 134% for one team in testing. | A team's players are scaled down if they pass 90%. |
| The game's-first-TD chance ignored which team was favored. | It was a tier lookup, the same for a 10-point favorite and underdog. | Now the team's-first chance times the spread-based chance the team scores first. Tested: shown 13.7%, scored 14.7% in the top band. |
| Results gave hit rates with no sense of luck. | "3 of 32" reads as failure; 5.5 were expected and 2 to 10 is normal. | Results now show expected hits, actual hits, and the normal range. |
| No price to compare against. | A 45% chance is a good bet at +150 and a bad one at +100. | Every chance now shows its fair (break-even) odds. |
| Kickoffs were stamped with winter time all season. | In September and October a game "started" an hour late, so plays could be re-recorded during the first hour of a game. | Kickoffs use the real Eastern offset. |
| Grading missed return and fumble-recovery TDs. | 3 of 668 TDs this season. | Graded the way a sportsbook pays. |

## 2. Data and modeling

| Finding | Evidence | Change |
| --- | --- | --- |
| The model was trained on one definition of red-zone share and fed another. | Training divided by team plays in games the player appeared in; the sheet divides by all team games. Training shares averaged 23%, the sheet's 20%. | Training now uses the sheet's definition, and counts games played by snaps as the build does. |
| Shares were used straight; the first few red-zone chances matter more than the last few. | Square-root inputs: log loss 0.4354 to 0.4306, AUC 0.734 to 0.741, across three held-out seasons. | Model uses square roots of touches and shares. |
| Two seasons of data, validated by swapping them. | 5,963 player games. | Three seasons (2024 to 2026), 11,083 player games, each scored by a model that never saw it. |
| Other inputs were worth checking. | TD history, last-three-game trends, home field, opponent's defense by position, team TD rate and a boosted-tree model: none beat the simple model out of sample. | Left out. Simple stays. |
| Training needed files placed by hand and stopped at Week 8. | `features.py` took a folder nobody documented. | `train.py` downloads what it needs. Retrained through Week 9. |

## 3. Programming

| Finding | Change |
| --- | --- |
| Scoring is written twice (Python and the page) with only "keep in step" comments. They had drifted: Python skipped a team with no betting line, the page didn't. | Brought back in step, and a test now loads the built page in Chrome and compares every player's numbers to Python's. The refresh won't publish if they differ. |
| No tests, no README. | Nine tests; README. |
| `features.py` was unreadable one-line code with dead variables. | Rewritten. |
| Each build downloaded the same files up to three times, with no retry. | One download per source, three tries. |
| The results history was written in place; a crash mid-write would corrupt it. | Written to a side file and swapped in. |
| The season year was typed in eight places. | One `SEASON` setting. |
| The spreadsheet reader script loaded from a CDN unchecked. | Pinned with an integrity hash. |
| Two refreshes could run at once and both try to publish. | The workflow runs one at a time and rebases before pushing. |

## 4. Graphic design

| Finding | Change |
| --- | --- |
| The number that matters most was the smallest thing in its row. | The chance is now the largest figure in each row, with fair odds beside it. |
| Each tab opened with a long grey paragraph. | One or two lines, with the detail behind "How to read this". |
| The first tab was blank once every game had kicked off. | Started games show automatically when none are left. |
| "Started" was a bright green badge on every row. | Started rows are dimmed with a quiet label. |
| Bar labels in Matchups were struck through by the league-average line. | Labels sit on a solid chip above the line. |
| Results tables ran text against the border, and their headers looked sortable but weren't. | Padded; plain headers. |
| Heat shading on TDs Allowed reused the running-back orange. | Uses the page's green accent. |
| On a phone, filter buttons were small and the tab strip gave no hint it scrolled. | Larger buttons under 860px; the strip fades at the edge and keeps the current tab in view. |
| Tabs didn't change the address, so a tab couldn't be linked or bookmarked. | Each tab has its own address; arrow keys move between tabs. |
| No icon or description for the browser tab or a shared link. | Added. |

## Follow-up, 13 November 2026: the known limits

| Limit | Evidence | Change |
| --- | --- | --- |
| No sportsbook prices, so the app couldn't say which bets were good value. | | Each play has a box for the sportsbook's odds. The app shows the bet's value: average profit per dollar staked if the app's chance is right. First-TD picks have the same box in their detail. Prices stay in your browser for the week. |
| When a starter was out, teammates' chances didn't rise. | In games where a teammate at the same position holding 15% or more of the red-zone work sat out, players scored 23.5% of the time; the model said 17.7% (1,729 player games, all held out). | New input: the red-zone share of teammates at the player's position who are out. With it the model says 23.5%. Log loss 0.4229 to 0.4213 and AUC 0.738 to 0.741 across six held-out seasons. Affected players are flagged "Bigger role". |
| The top of the scale stopped at about 60%. | With six seasons, players the raw model rated 60 to 65% scored 51%, and those rated above 65% scored 67% (120 player games). | Trained on six seasons (2021 to 2026, 25,259 player games) with a finer top step. The top is now 62%. That ceiling is what the data supports, not a shortage of data; players tied at the top are ordered by the model's raw number. |
| The sheet is parsed twice with no test. | | A test has Chrome parse the live sheet with the page's code and compares every value to `build.py`'s. They agree. Eleven tests in all. |

## Known limits, not fixed

- Prices are typed in by hand. Pulling them automatically needs a paid odds feed and an API key.
- Only teammates at the same position get the "starter out" bump, and only for players listed Out,
  Doubtful, on injured reserve or suspended. A Questionable player who ends up inactive isn't counted
  until the injury report says so.
- Live record so far is two weeks: 35 of 119 any-TD plays against 41 expected, 3 of 32 first-TD
  picks against 5.5 expected. Both are inside the range luck alone gives. Only more weeks settle it.
