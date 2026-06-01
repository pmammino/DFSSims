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
- **Results table** sorted by highest ROI, showing Win Rate, ITM Rate, ROI, salary, stack, and players.
- **Lineup detail** view (player-by-player, sorted by roster slot, with ownership).
- **Upload Lineup Template** button — swap in a fresh `DKSalaries.csv` whenever
  the DraftKings export changes; its player IDs and slots drive the export.
- **Export**:
  - **DraftKings upload CSV** — the top-ROI matching lineups, with each
    (possibly multi-position) player assigned to a roster slot via bipartite
    matching, IDs in DK slot order (max 500 lineups per file).
  - **Filtered summary CSV** — the filtered lineups with their stats.

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
