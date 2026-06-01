"""Data loading and processing for the DFS Lineup Explorer.

Handles three inputs:
  * sim_lineups.csv  - one row per player per lineup (the lineup structures)
  * sim_results.csv  - one row per lineup (ROI / WinRate / ITM etc.)
  * DKSalaries.csv   - DraftKings upload template (player table + roster slots)

The two sim files join on LineupNum within a (SlateID, Site, ContestType) slate.
"""

from __future__ import annotations

import io
from collections import Counter

import pandas as pd

# Positions that are pitchers and therefore excluded from hitter "stack" counts.
PITCHER_POSITIONS = {"SP", "RP", "P"}

# How a sim_lineups assigned Position maps onto a DraftKings roster slot.
POSITION_TO_SLOT = {"SP": "P", "RP": "P", "P": "P"}

# Roster-slot ordering used to sort players for readable display.
SLOT_DISPLAY_ORDER = {
    "SP": 0, "RP": 0, "P": 0, "C": 1, "1B": 2, "2B": 3, "3B": 4, "SS": 5, "OF": 6,
}

# Classic MLB roster slots, and the disambiguated column labels (P1/P2, OF1..).
CLASSIC_SLOTS = ["P", "P", "C", "1B", "2B", "3B", "SS", "OF", "OF", "OF"]


def slot_labels(slots: list[str]) -> list[str]:
    """Disambiguate repeated slots: ['P','P','OF',...] -> ['P1','P2','OF1',...]."""
    counts = Counter(slots)
    seen: dict[str, int] = {}
    out = []
    for s in slots:
        if counts[s] > 1:
            seen[s] = seen.get(s, 0) + 1
            out.append(f"{s}{seen[s]}")
        else:
            out.append(s)
    return out


SLOT_LABELS = slot_labels(CLASSIC_SLOTS)


def _read_csv(source) -> pd.DataFrame:
    """Read a CSV from a path or an uploaded file-like object, tolerating a BOM."""
    return pd.read_csv(source, encoding="utf-8-sig", dtype=str, keep_default_na=False)


# --------------------------------------------------------------------------- #
# sim_lineups + sim_results
# --------------------------------------------------------------------------- #
def load_lineups(source) -> pd.DataFrame:
    """Load sim_lineups.csv into a tidy player-level frame.

    The raw file has duplicate SlateID / PlayerID columns; pandas suffixes the
    repeats (SlateID.1 etc.). We keep only the columns we need.
    """
    raw = _read_csv(source)
    df = pd.DataFrame(
        {
            "LineupNum": raw["LineupNum"].astype(int),
            "PlayerID": raw["PlayerID"].astype(str),
            "PlayerContestID": raw["PlayerContestID"].astype(str),
            "Salary": pd.to_numeric(raw["Salary"], errors="coerce").fillna(0).astype(int),
            "Position": raw["Position"].astype(str).str.strip(),
            "Ownership": pd.to_numeric(raw["Ownership"], errors="coerce").fillna(0.0),
            "FullName": raw["FullName"].astype(str).str.strip(),
            "Team": raw["Team"].astype(str).str.strip(),
        }
    )
    return df


def load_results(source) -> pd.DataFrame:
    """Load sim_results.csv into a numeric per-lineup frame."""
    raw = _read_csv(source)
    numeric_cols = [
        "AvgProfit", "AvgPayout", "ROI", "WinRate", "Top10Rate",
        "ITMRate", "AvgRank", "MedianRank", "BestRank", "WorstRank",
    ]
    out = pd.DataFrame({"LineupNum": raw["LineupNum"].astype(int)})
    for col in numeric_cols:
        if col in raw.columns:
            out[col] = pd.to_numeric(raw[col], errors="coerce")
    return out


def _stack_info(team_counts: Counter) -> dict:
    """Derive stack descriptors from a Counter of hitters-per-team."""
    ordered = sorted(team_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    primary_team, primary_size = (ordered[0] if ordered else ("", 0))
    secondary_team, secondary_size = (ordered[1] if len(ordered) > 1 else ("", 0))
    # Stack pattern: counts of 2+ joined with '-', e.g. "5-3".
    pattern = "-".join(str(c) for _, c in ordered if c >= 2) or "no stack"
    # Teams that form a stack of 2+ (used by the team filter).
    stacked = {t for t, c in team_counts.items() if c >= 2}
    return {
        "PrimaryStackTeam": primary_team,
        "PrimaryStackSize": primary_size,
        "SecondaryStackTeam": secondary_team,
        "SecondaryStackSize": secondary_size,
        "StackPattern": pattern,
        "StackedTeams": stacked,
    }


def build_lineup_table(lineups: pd.DataFrame, results: pd.DataFrame) -> pd.DataFrame:
    """Collapse player rows into one row per lineup, with stack + result stats.

    Returns a frame indexed by row with helper columns (sets / lists) used for
    filtering, plus display-friendly result columns. Sorted by ROI descending.
    """
    rows = []
    for lineup_num, grp in lineups.groupby("LineupNum", sort=False):
        hitters = grp[~grp["Position"].isin(PITCHER_POSITIONS)]
        team_counts = Counter(hitters["Team"])
        info = _stack_info(team_counts)

        # Order players by roster slot (P, C, 1B, 2B, 3B, SS, OF) for readable
        # display; multi-position players sort by their first eligible slot.
        ordered = grp.assign(
            _o=grp["Position"].map(
                lambda p: SLOT_DISPLAY_ORDER.get(str(p).split("/")[0], 9)
            )
        ).sort_values("_o")

        # Assign each (possibly multi-eligible) player to a concrete roster slot
        # so the lineup can be shown one player per position column.
        eligs = [(idx, _eligible_slots(r["Position"])) for idx, r in grp.iterrows()]
        assigned = _match_to_slots(eligs, CLASSIC_SLOTS)
        name_by_idx = grp["FullName"].to_dict()
        team_by_idx = grp["Team"].to_dict()
        slot_players = {
            label: (name_by_idx.get(key) if key is not None else None)
            for label, key in zip(SLOT_LABELS, assigned)
        }
        # Parallel team-per-slot columns ("<label>__team") for cell colouring.
        slot_teams = {
            f"{label}__team": (team_by_idx.get(key) if key is not None else None)
            for label, key in zip(SLOT_LABELS, assigned)
        }

        rows.append(
            {
                "LineupNum": lineup_num,
                "TotalSalary": int(grp["Salary"].sum()),
                "Players": list(ordered["FullName"]),
                "PlayerSet": set(grp["FullName"]),
                "TeamSet": set(grp["Team"]),
                "TotalOwnership": round(float(grp["Ownership"].sum()), 2),
                "TeamCounts": dict(team_counts),
                **slot_players,
                **slot_teams,
                **info,
            }
        )

    table = pd.DataFrame(rows)
    merged = table.merge(results, on="LineupNum", how="left")
    if "ROI" in merged.columns:
        merged = merged.sort_values("ROI", ascending=False, na_position="last")
    return merged.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# DraftKings template (DKSalaries.csv)
# --------------------------------------------------------------------------- #
def parse_dk_template(source) -> tuple[list[str], pd.DataFrame]:
    """Parse a DKSalaries.csv upload template.

    Returns:
        slots:   ordered roster slots from the template header, e.g.
                 ['P','P','C','1B','2B','3B','SS','OF','OF','OF'].
        players: DataFrame with the player table (Name, ID, Roster Position,
                 Salary, TeamAbbrev, ...).
    """
    if hasattr(source, "read"):
        content = source.read()
        if isinstance(content, bytes):
            content = content.decode("utf-8-sig")
    else:
        with open(source, "r", encoding="utf-8-sig") as fh:
            content = fh.read()

    lines = content.splitlines()

    # Row 0 holds the roster-slot template in the leading columns.
    slots = [c.strip() for c in lines[0].split(",") if c.strip() and c.strip() != "Instructions"]

    # Find the player-table header row ("Position,Name + ID,Name,ID,...").
    header_idx = None
    for i, line in enumerate(lines):
        if "Name + ID" in line and "Roster Position" in line:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not locate the player table header in the DK template.")

    # The player table lives in the trailing columns; re-parse from the header row.
    table = pd.read_csv(
        io.StringIO("\n".join(lines[header_idx:])),
        dtype=str,
        keep_default_na=False,
    )
    table.columns = [c.strip() for c in table.columns]
    # Drop the empty leading columns from the instruction layout (pandas names
    # blank headers "Unnamed: N").
    keep = [c for c in table.columns if c and not c.startswith("Unnamed")]
    table = table.loc[:, keep]
    table = table[table["ID"].astype(str).str.strip() != ""].reset_index(drop=True)
    return slots, table


def _eligible_slots(position: str) -> set[str]:
    """Convert a sim_lineups Position (eligibility) to the DK roster slots it fills.

    Positions can be multi-eligible, e.g. "1B/3B" -> {"1B", "3B"}; pitchers map
    to the generic "P" slot.
    """
    slots = set()
    for part in str(position).split("/"):
        part = part.strip()
        if part:
            slots.add(POSITION_TO_SLOT.get(part, part))
    return slots


def _match_to_slots(eligs: list[tuple[str, set[str]]], slots: list[str]) -> list[str | None]:
    """Assign players to roster slots via maximum bipartite matching.

    `eligs` is a list of (player_id, eligible_slots). Returns, for each slot
    index, the assigned player_id (or None if the slot can't be filled).
    """
    elig_of = dict(eligs)
    slot_to_player: list[str | None] = [None] * len(slots)

    def augment(pid: str, seen: set[int]) -> bool:
        for si, slot in enumerate(slots):
            if slot in elig_of[pid] and si not in seen:
                seen.add(si)
                current = slot_to_player[si]
                if current is None or augment(current, seen):
                    slot_to_player[si] = pid
                    return True
        return False

    for pid, _ in eligs:
        augment(pid, set())
    return slot_to_player


def build_dk_upload(
    lineups: pd.DataFrame,
    lineup_nums: list[int],
    slots: list[str],
    valid_ids: set[str] | None = None,
) -> tuple[pd.DataFrame, list[int]]:
    """Build a DraftKings-uploadable CSV (one row per lineup, IDs in slot order).

    Players (which may be multi-position eligible) are assigned to roster slots
    via bipartite matching. Returns the upload frame and the list of lineup
    numbers that could not be mapped cleanly (unfillable slot, or IDs absent
    from the current template).
    """
    upload_rows = []
    skipped = []

    for num in lineup_nums:
        grp = lineups[lineups["LineupNum"] == num]
        eligs = [(str(p["PlayerContestID"]), _eligible_slots(p["Position"]))
                 for _, p in grp.iterrows()]

        assigned = _match_to_slots(eligs, slots)
        ok = all(pid is not None for pid in assigned)
        if valid_ids is not None and any(
            pid is not None and pid not in valid_ids for pid in assigned
        ):
            ok = False

        upload_rows.append({i: (pid or "") for i, pid in enumerate(assigned)})
        if not ok:
            skipped.append(num)

    upload = pd.DataFrame(upload_rows, columns=range(len(slots)))
    # Restore DK's plain slot headers (duplicates like P,P / OF,OF,OF are expected).
    upload.columns = slots
    return upload, skipped
