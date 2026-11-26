#!/usr/bin/env python3
"""Posts the week's picks and, once graded, its results to a Discord channel.

Compares results/history.json with the version at an earlier commit (the one the
refresh started from), so it only posts when something new was published:
  - picks, the first time a week's plays are recorded
  - results, when the last of a week's plays is graded

    python3 discord_post.py                 # compare with HEAD, post if DISCORD_WEBHOOK_URL is set
    python3 discord_post.py --since <sha>   # compare with that commit
    python3 discord_post.py --dry-run       # print the messages instead of posting
    python3 discord_post.py --test          # post the latest week's picks, marked as a test
    python3 discord_post.py --preview 12    # print week 12's picks and results messages

Standard library only. Posting never fails the refresh: errors are printed, not raised.
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY = "results/history.json"
SITE = "https://codfather-debug.github.io/nfl-td-board/"
TOP = 10


def pct(x):
    return f"{round(x * 100)}%"


def key(e):
    return e["week"]


def weeks(plays):
    return {e["week"] for e in plays}


def graded(e):
    return "anyTd" in e


def week_done(plays, week):
    mine = [e for e in plays if e["week"] == week]
    return bool(mine) and all(graded(e) for e in mine)


def picks_message(plays, week):
    mine = [e for e in plays if e["week"] == week]
    top = sorted((e for e in mine if e.get("isPlay")), key=lambda e: -e["anyRate"])[:TOP]
    first = sorted((e for e in mine if e.get("isPick")), key=lambda e: -e["firstRate"])[:5]
    lines = [f"🏈 **NFL TD Board · Week {week} picks**", "", "**Top anytime TD chances**"]
    lines += [f"{i}. {e['player']} ({e['team']} {e['pos']}, {e['opp']}): **{pct(e['anyRate'])}** any TD, "
              f"{pct(e['firstRate'])} team's first" for i, e in enumerate(top, 1)]
    if first:
        lines += ["", "**Best team first-TD picks**"]
        lines += [f"• {e['player']} ({e['team']}): {pct(e['firstRate'])} team's first, {pct(e['gameRate'])} game's first"
                  for e in first]
    lines += ["", f"Chances can change with late injury news: {SITE}"]
    return "\n".join(lines)


def results_message(plays, week):
    mine = [e for e in plays if e["week"] == week and e.get("played") is not False]
    shown = [e for e in mine if e.get("isPlay")]
    hits = [e for e in shown if e.get("anyTd")]
    lines = [f"🏈 **NFL TD Board · Week {week} results**", ""]
    if shown:
        expected = sum(e["anyRate"] for e in shown) / len(shown)
        lines.append(f"**Anytime TD plays:** {len(hits)} of {len(shown)} scored ({pct(len(hits) / len(shown))}; "
                     f"the board expected {pct(expected)})")
    picks = [e for e in mine if e.get("isPick") and "teamFirst" in e]
    if picks:
        team_hits = [e for e in picks if e["teamFirst"]]
        game_hits = [e for e in picks if e.get("gameFirst")]
        lines.append(f"**Team first-TD picks:** {len(team_hits)} of {len(picks)} hit, "
                     f"{len(game_hits)} also scored the game's first TD")
        if team_hits:
            names = [f"{e['player']} ({e['team']})" for e in team_hits]
            more = f" and {len(names) - 8} more" if len(names) > 8 else ""
            lines.append("✅ " + ", ".join(names[:8]) + more)
    if hits:
        best = sorted(hits, key=lambda e: e["anyRate"])[:5]
        lines.append("**Longest shots that scored:** " + ", ".join(f"{e['player']} ({pct(e['anyRate'])})" for e in best))
    lines += ["", f"Full results: {SITE}"]
    return "\n".join(lines)


def messages(old, new):
    """The posts the change from old to new plays calls for, oldest week first."""
    out = []
    for week in sorted(weeks(new)):
        if week not in weeks(old):
            out.append(picks_message(new, week))
        if week_done(new, week) and not week_done(old, week):
            out.append(results_message(new, week))
    return out


TEST_NOTE = "🧪 *Test post: this channel is connected. Real posts look like this.*\n\n"


def latest_picks(plays):
    return messages([], [e for e in plays if key(e) == max(key(e) for e in plays)])[0]


def load_at(commit):
    try:
        text = subprocess.run(["git", "show", f"{commit}:{HISTORY}"], cwd=HERE, capture_output=True,
                              text=True, check=True).stdout
        return json.loads(text)["plays"]
    except (subprocess.CalledProcessError, ValueError, KeyError):
        return []


def post(url, text):
    body = json.dumps({"content": text[:2000], "allowed_mentions": {"parse": []}}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "User-Agent": "DiscordBot (nfl-td-board, 1.0)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="HEAD")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--preview", type=int)
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()
    with open(os.path.join(HERE, HISTORY), encoding="utf-8") as f:
        new = json.load(f)["plays"]
    if args.preview is not None:
        print(picks_message(new, args.preview), results_message(new, args.preview), sep="\n\n---\n\n")
        return
    todo = messages(load_at(args.since), new)
    if args.test:
        todo = [TEST_NOTE + latest_picks(new)]
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not todo:
        print("Discord: nothing new to post.")
    for text in todo:
        if args.dry_run or not url:
            print(("" if args.dry_run else "Discord: no DISCORD_WEBHOOK_URL, would post:\n") + text + "\n")
            continue
        try:
            print(f"Discord: posted ({post(url, text)}).")
        except Exception as e:  # A Discord outage must not stop the board from publishing.
            print(f"Discord: could not post: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
