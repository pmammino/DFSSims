# DFS Lineup Explorer

An interactive Streamlit app to **filter, query, and export** simulated
DraftKings MLB lineups, ranked by ROI.

## Data

The app reads three CSVs from the repo root:

| File | Role |
|------|------|
| `sim_lineups.csv` | The lineup structures — one row per player per lineup (players, salaries, eligible positions, ownership, team). |
| `sim_results.csv` | Simulation results — one row per lineup (ROI, WinRate, ITMRate, Top10Rate, ranks, profit). |
| `DKSalaries.csv` | The DraftKings upload template (roster slots + player IDs) used to build an uploadable lineup file. |

The two sim files join on `LineupNum`. Each lineup is a classic MLB roster:
`P, P, C, 1B, 2B, 3B, SS, OF, OF, OF`.

## Features

- **Filter & query** lineups by:
  - **Stack team(s)** + **minimum stack size** (e.g. lineups with a 5-stack of the Angels)
  - **Stack pattern** (e.g. `5-3`, `4-2`, `2-2-2`)
  - **Included / excluded players** (match *all* or *any*)
  - **Teams in pool** (restrict to a set of teams)
  - **Total salary** range
  - **Minimum ROI / Win % / ITM % / Top 10 %**
- **Results table** sorted by highest ROI. Shows only the four summary stats —
  **ROI, Win Rate, ITM%, Top 10%** — each colour-scaled green (good) → red (bad)
  across the matches, alongside a readable stack label and the full, roster-ordered
  player list.
- **Lineup detail** view (player-by-player, ordered by roster slot, with rows
  tinted by team so stacks stand out at a glance).
- **Upload Lineup Template** button — swap in a fresh `DKSalaries.csv` whenever
  the DraftKings export changes; its player IDs and slots drive the export.
- **Export basket** — check lineups in the results table and **Add checked to
  basket** (or add all filtered). The basket **survives filter changes** so you
  can build a set across multiple queries; remove specific lineups or clear all.
  Only the basket is exported, on demand:
  - **DraftKings upload CSV** — the selected lineups (ROI-sorted, max 500), with
    each (possibly multi-position) player assigned to a roster slot via
    bipartite matching and IDs in DK slot order.
  - **Selected summary CSV** — the selected lineups with their stats.
- **Exposure charts** in the basket — player and team exposure (the % of your
  selected lineups containing each player/team) as sorted bar charts, with an
  adjustable max-exposure target line that highlights anyone you're overweight
  on. Updates live as you add or remove lineups.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open the URL Streamlit prints (default http://localhost:8501).

## Files

- `app.py` — Streamlit UI (sidebar filters, table, detail, export).
- `dfs_data.py` — data loading, stack computation, DK template parsing, and
  slot-assignment / export logic.
