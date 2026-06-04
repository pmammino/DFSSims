"""Slate context derived from the DraftKings template + lineups.

From DKSalaries.csv we can identify, for the current slate:
  * the games / matchups and their start times (the `Game Info` column),
  * each team's probable starting pitcher (highest-salaried SP) with projection,
  * projection-based team and game totals (a proxy for a Vegas over/under, built
    from DK's AvgPointsPerGame — not a real betting line),
and we tie those to how often each team / pitcher appears in the sim lineups.

True Vegas over/unders and confirmed probable pitchers would require a live
odds/MLB feed; `live_odds_available()` is a hook for wiring that up later.
"""

from __future__ import annotations

import os
import re

import pandas as pd

from nl_filter import _MLB_TEAMS

PITCHER_POS = {"SP", "RP", "P"}

# "SF@MIL 06/02/2026 07:40PM ET" -> away, home, date, time
_GAME_INFO_RE = re.compile(r"^\s*([A-Za-z]+)@([A-Za-z]+)\s+([\d/]+)\s+(.*?)\s*$")


def _abbrev_to_nickname() -> dict[str, str]:
    """Map team abbreviations (LAD, CWS, ATH, …) to canonical nicknames."""
    out: dict[str, str] = {}
    for nick, aliases in _MLB_TEAMS.items():
        for alias in aliases:
            if alias.isalpha() and len(alias) <= 4:
                out[alias.upper()] = nick
    return out


def parse_game_info(text: str):
    m = _GAME_INFO_RE.match(str(text))
    if not m:
        return None
    return m.group(1).upper(), m.group(2).upper(), m.group(3), m.group(4)


def build_slate(dk_players: pd.DataFrame, lineups: pd.DataFrame | None = None,
                n_lineups: int | None = None):
    """Return (games_df, pitchers_df, meta) describing the slate.

    games_df    — one row per game (matchup, time, both SPs, projections, total).
    pitchers_df — probable starter per team, with salary, projection, exposure.
    meta        — dict with slate-level summary (date, counts).
    """
    df = dk_players.copy()
    df["Proj"] = pd.to_numeric(df["AvgPointsPerGame"], errors="coerce").fillna(0.0)
    df["Sal"] = pd.to_numeric(df["Salary"], errors="coerce").fillna(0).astype(int)
    a2n = _abbrev_to_nickname()
    df["Nick"] = df["TeamAbbrev"].map(lambda a: a2n.get(str(a).upper(), str(a)))

    is_pitcher = df["Position"].isin(PITCHER_POS) | df["Roster Position"].eq("P")
    hitters = df[~is_pitcher]
    starters = df[df["Position"].eq("SP")]

    # Top-8 hitter projection per team (offense proxy).
    team_proj = {
        team: round(g.sort_values("Proj", ascending=False)["Proj"].head(8).sum(), 1)
        for team, g in hitters.groupby("Nick")
    }
    # Probable starter per team = highest-salaried SP.
    team_sp = {}
    for team, g in starters.groupby("Nick"):
        team_sp[team] = g.sort_values("Sal", ascending=False).iloc[0]

    # Lineup exposure (share of sim lineups containing the team / pitcher).
    team_expo, pitcher_expo = {}, {}
    if lineups is not None and n_lineups:
        team_expo = (
            lineups.groupby("Team")["LineupNum"].nunique() / n_lineups * 100
        ).round(1).to_dict()
        sp_rows = lineups[lineups["Position"].eq("SP")]
        pitcher_expo = (
            sp_rows.groupby("FullName")["LineupNum"].nunique() / n_lineups * 100
        ).round(1).to_dict()

    def _sp_cell(team):
        sp = team_sp.get(team)
        return ("—", 0.0, 0) if sp is None else (sp["Name"], float(sp["Proj"]), int(sp["Sal"]))

    games = []
    for gi in pd.Series(df["Game Info"].dropna().unique()):
        parsed = parse_game_info(gi)
        if not parsed:
            continue
        away_ab, home_ab, date, time = parsed
        away, home = a2n.get(away_ab, away_ab), a2n.get(home_ab, home_ab)
        ap, hp = team_proj.get(away, 0.0), team_proj.get(home, 0.0)
        away_sp = _sp_cell(away)
        home_sp = _sp_cell(home)
        games.append({
            "Matchup": f"{away} @ {home}",
            "Time": time,
            "Date": date,
            "Away SP": away_sp[0],
            "Home SP": home_sp[0],
            "Away proj": ap,
            "Home proj": hp,
            "Game proj total": round(ap + hp, 1),
            "_away": away, "_home": home,
        })

    games_df = (
        pd.DataFrame(games).sort_values("Game proj total", ascending=False)
        .reset_index(drop=True)
    )

    pitcher_rows = []
    for team, sp in sorted(team_sp.items()):
        pitcher_rows.append({
            "Team": team,
            "Probable SP": sp["Name"],
            "Salary": int(sp["Sal"]),
            "Proj pts": round(float(sp["Proj"]), 1),
            "Lineup %": pitcher_expo.get(sp["Name"], 0.0),
        })
    pitchers_df = (
        pd.DataFrame(pitcher_rows).sort_values("Proj pts", ascending=False)
        .reset_index(drop=True)
    )

    meta = {
        "n_games": len(games_df),
        "n_teams": df["Nick"].nunique(),
        "date": games_df["Date"].iloc[0] if not games_df.empty else "",
        "team_expo": team_expo,
    }
    return games_df, pitchers_df, meta


def live_odds_available() -> bool:
    """Hook for a real odds feed — wire up an odds API key here later."""
    return bool(os.environ.get("ODDS_API_KEY"))


def slate_label(dk_players: pd.DataFrame) -> tuple[str, float]:
    """Human-readable slate label + a sortable key, from the template's games.

    e.g. ("Jun 04 · 2:10 PM ET first pitch · 4 games (8 teams)", <epoch-ish>).
    Distinguishes an early/afternoon slate from a main/evening one by start time.
    """
    import datetime as _dt

    infos = dk_players["Game Info"].dropna().map(parse_game_info)
    starts, date_str = [], ""
    for parsed in infos:
        if not parsed:
            continue
        _, _, date_str, time_str = parsed
        clean = re.sub(r"\s*[A-Z]{2,3}\s*$", "", time_str).strip()  # drop "ET"
        try:
            starts.append(_dt.datetime.strptime(f"{date_str} {clean}",
                                                "%m/%d/%Y %I:%M%p"))
        except ValueError:
            continue

    n_games = dk_players["Game Info"].nunique()
    n_teams = dk_players["TeamAbbrev"].nunique()
    if not starts:
        return (f"{date_str or 'Slate'} · {n_games} games ({n_teams} teams)", 0.0)

    first = min(starts)
    # Build the time portably (%-I / %#I are platform-specific — avoid them).
    hour12 = first.hour % 12 or 12
    time_str = f"{hour12}:{first.minute:02d} {first.strftime('%p')}"
    label = (f"{first.strftime('%b %d')} · {time_str} ET first pitch · "
             f"{n_games} games ({n_teams} teams)")
    return label, first.timestamp()

