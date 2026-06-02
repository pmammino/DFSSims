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

The app has three top-level tabs: **🔍 Explore & Build** (search, select, and
export lineups), **📊 Summary Stats** (aggregate breakdowns across a chosen
population of lineups), and **🗓️ Slate** (matchup / pitcher context).

### Explore & Build

- **💬 Conversational filtering** — describe your slate in plain English
  (*"Stack the Dodgers 5-man with Mookie Betts, I'm high on Bobby Witt Jr., fade
  the Yankees and avoid the Reds"*) and the app sets the filters for you, showing
  an editable summary of what it decided. An always-on **offline parser** knows
  all 30 MLB teams (nicknames, cities, abbreviations) and your player pool, plus
  intent cues (high on / love / stack / fade / avoid / no …). If
  `ANTHROPIC_API_KEY` is set (and `anthropic` is installed), it can optionally use
  **Claude** for richer, open-ended understanding — otherwise it stays fully local.
- **Filter & query** lineups by:
  - **Stack team(s)** + **minimum stack size** (e.g. lineups with a 5-stack of the Angels)
  - **Stack pattern** (e.g. `5-3`, `4-2`, `2-2-2`)
  - **Included / excluded players** (match *all* or *any*)
  - **Fade team(s)** (drop any lineup containing those teams)
  - **Teams in pool** (restrict to a set of teams)
  - **Total salary** range
  - **Minimum ROI / Win % / ITM % / Top 10 %**
- **Filters across the top** of the page (in a collapsible panel, collapsed by
  default) so the lineup table can use the full window width.
- **Results table** sorted by highest ROI. Each lineup is shown with **one column
  per roster position** (P, P, C, 1B, 2B, 3B, SS, OF, OF, OF — players assigned to
  slots via bipartite matching), followed by the four summary stats —
  **ROI, Win Rate, ITM%, Top 10%** — each colour-scaled green (good) → red (bad),
  plus a readable stack label. Each player cell is tinted by its team colour so
  stacks stand out across the row. To stay responsive, only the top **N** rows
  (default 250, adjustable) are rendered with colouring; the full filtered set
  still drives "Add all filtered" and export.
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

### Summary Stats

Aggregate breakdowns over a chosen population — **Top N by ROI** (e.g. the best
200 lineups), the **current filter**, or **all lineups** — across three sub-tabs:

- **Players** — player frequency (how many of the population's lineups each
  player appears in, and the %), with avg ownership, a bar chart, and a CSV export.
- **Stacks** — stack-pattern frequency (`5-2`, `4-3`, …), primary-stack-team
  frequency, primary-stack-size distribution, and how often each team is
  stacked (2+ hitters).
- **Teams** — total roster spots (player appearances) and lineup count per team.
- **Distributions** — histograms of total lineup salary and total ownership
  across the population, with min / median / mean / max summaries.

### Slate

Slate context derived from the DraftKings template (`Game Info`, `TeamAbbrev`,
`AvgPointsPerGame`, pitcher rows) and tied to the lineups:

- **Matchups** — each game with start time, both probable starting pitchers, and
  a projection-based game total (sorted high → low as an over/under proxy), plus
  how often each team appears in the sim lineups.
- **Probable starting pitchers** — each team's highest-salaried SP with salary,
  projected points, and lineup exposure.

Projections come from DraftKings' AvgPointsPerGame (an offense proxy, **not** a
Vegas line). Real over/unders and confirmed starters require a live odds/MLB
feed; a hook (`ODDS_API_KEY`) is in place for wiring one up to a real slate.

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
