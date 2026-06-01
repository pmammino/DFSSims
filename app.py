"""DFS Lineup Explorer — filter, query and export simulated DraftKings lineups.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

import dfs_data as dd

HERE = os.path.dirname(os.path.abspath(__file__))
LINEUPS_CSV = os.path.join(HERE, "sim_lineups.csv")
RESULTS_CSV = os.path.join(HERE, "sim_results.csv")
TEMPLATE_CSV = os.path.join(HERE, "DKSalaries.csv")

st.set_page_config(page_title="DFS Lineup Explorer", page_icon="⚾", layout="wide")


# --------------------------------------------------------------------------- #
# Cached data loaders
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner="Loading lineups…")
def get_lineups() -> pd.DataFrame:
    return dd.load_lineups(LINEUPS_CSV)


@st.cache_data(show_spinner="Loading results…")
def get_results() -> pd.DataFrame:
    return dd.load_results(RESULTS_CSV)


@st.cache_data(show_spinner="Building lineup table…")
def get_lineup_table() -> pd.DataFrame:
    return dd.build_lineup_table(get_lineups(), get_results())


@st.cache_data(show_spinner="Parsing DK template…")
def get_default_template():
    return dd.parse_dk_template(TEMPLATE_CSV)


def parse_uploaded_template(file_bytes: bytes):
    import io

    return dd.parse_dk_template(io.BytesIO(file_bytes))


# --------------------------------------------------------------------------- #
# Load data
# --------------------------------------------------------------------------- #
players = get_lineups()
table = get_lineup_table()

all_player_names = sorted(players["FullName"].unique())
all_teams = sorted(players["Team"].unique())
all_patterns = sorted(table["StackPattern"].unique())

# --------------------------------------------------------------------------- #
# Sidebar — DK upload template
# --------------------------------------------------------------------------- #
st.sidebar.header("⚙️ Lineup Template")
uploaded = st.sidebar.file_uploader(
    "Upload DK Salaries template (.csv)",
    type="csv",
    help="DraftKings export changes often. Upload a fresh DKSalaries.csv to use "
    "the current player IDs and roster slots for the export below.",
)
try:
    if uploaded is not None:
        slots, dk_players = parse_uploaded_template(uploaded.getvalue())
        st.sidebar.success(f"Using uploaded template ({len(dk_players)} players).")
    else:
        slots, dk_players = get_default_template()
        st.sidebar.caption("Using bundled DKSalaries.csv.")
    valid_ids = set(dk_players["ID"].astype(str))
except Exception as exc:  # noqa: BLE001
    st.sidebar.error(f"Template error: {exc}")
    slots, valid_ids = (
        ["P", "P", "C", "1B", "2B", "3B", "SS", "OF", "OF", "OF"],
        None,
    )

# --------------------------------------------------------------------------- #
# Sidebar — filters
# --------------------------------------------------------------------------- #
st.sidebar.header("🔍 Filters")

with st.sidebar.expander("Players", expanded=True):
    include_players = st.multiselect("Include players", all_player_names)
    include_mode = st.radio(
        "Match", ["All of these", "Any of these"], horizontal=True,
        help="Whether a lineup must contain all selected players or just one.",
    )
    exclude_players = st.multiselect("Exclude players", all_player_names)

with st.sidebar.expander("Stacks", expanded=True):
    stack_teams = st.multiselect("Stack team(s)", all_teams,
                                 help="Lineups that stack one of these teams.")
    min_stack_size = st.slider(
        "Min stack size", 1, 6, 1,
        help="For the team filter, and the minimum size of a lineup's primary stack.",
    )
    stack_patterns = st.multiselect(
        "Stack pattern(s)", all_patterns,
        help="e.g. 5-3 = a 5-stack plus a 3-stack of hitters.",
    )

with st.sidebar.expander("Teams in pool", expanded=False):
    pool_teams = st.multiselect(
        "Restrict to teams", all_teams,
        help="Only lineups whose players all come from these teams.",
    )

with st.sidebar.expander("Salary & results", expanded=True):
    sal_min, sal_max = int(table["TotalSalary"].min()), int(table["TotalSalary"].max())
    salary_range = st.slider("Total salary", sal_min, sal_max, (sal_min, sal_max), step=100)

    def _stat_slider(col, label, fmt="%.2f"):
        if col not in table.columns or table[col].dropna().empty:
            return None
        lo, hi = float(table[col].min()), float(table[col].max())
        return st.slider(label, lo, hi, lo, format=fmt)

    min_roi = _stat_slider("ROI", "Min ROI")
    min_win = _stat_slider("WinRate", "Min Win Rate %")
    min_itm = _stat_slider("ITMRate", "Min ITM Rate %")
    min_top10 = _stat_slider("Top10Rate", "Min Top 10 %")


# --------------------------------------------------------------------------- #
# Apply filters
# --------------------------------------------------------------------------- #
def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)

    if include_players:
        want = set(include_players)
        if include_mode == "All of these":
            mask &= df["PlayerSet"].apply(lambda s: want.issubset(s))
        else:
            mask &= df["PlayerSet"].apply(lambda s: bool(want & s))

    if exclude_players:
        avoid = set(exclude_players)
        mask &= df["PlayerSet"].apply(lambda s: not (avoid & s))

    if stack_teams:
        # Lineup matches if ANY selected team has >= min_stack_size hitters.
        want_teams = set(stack_teams)
        mask &= df["TeamCounts"].apply(
            lambda counts: any(
                counts.get(t, 0) >= min_stack_size for t in want_teams
            )
        )
    elif min_stack_size > 1:
        # No team specified: require the largest stack to be at least this big.
        mask &= df["PrimaryStackSize"] >= min_stack_size

    if stack_patterns:
        mask &= df["StackPattern"].isin(stack_patterns)

    if pool_teams:
        allowed = set(pool_teams)
        mask &= df["TeamSet"].apply(lambda s: s.issubset(allowed))

    mask &= df["TotalSalary"].between(*salary_range)

    for col, val in [("ROI", min_roi), ("WinRate", min_win),
                     ("ITMRate", min_itm), ("Top10Rate", min_top10)]:
        if val is not None and col in df.columns:
            mask &= df[col].fillna(float("-inf")) >= val

    return df[mask]


filtered = apply_filters(table)
if "ROI" in filtered.columns:
    filtered = filtered.sort_values("ROI", ascending=False, na_position="last")

# --------------------------------------------------------------------------- #
# Header + summary metrics
# --------------------------------------------------------------------------- #
st.title("⚾ DFS Lineup Explorer")
st.caption("Filter and query simulated DraftKings lineups, sorted by ROI.")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Lineups", f"{len(filtered):,}", f"of {len(table):,}")
if not filtered.empty:
    if "ROI" in filtered.columns:
        c2.metric("Best ROI", f"{filtered['ROI'].max():.1f}")
        c3.metric("Avg ROI", f"{filtered['ROI'].mean():.1f}")
    if "WinRate" in filtered.columns:
        c4.metric("Avg Win %", f"{filtered['WinRate'].mean():.2f}")
    if "ITMRate" in filtered.columns:
        c5.metric("Avg ITM %", f"{filtered['ITMRate'].mean():.1f}")

st.divider()

if filtered.empty:
    st.warning("No lineups match the current filters. Loosen them in the sidebar.")
    st.stop()

# --------------------------------------------------------------------------- #
# Results table
# --------------------------------------------------------------------------- #
display_cols = {
    "LineupNum": "Lineup #",
    "ROI": "ROI",
    "WinRate": "Win %",
    "ITMRate": "ITM %",
    "Top10Rate": "Top10 %",
    "AvgProfit": "Avg Profit",
    "TotalSalary": "Salary",
    "TotalOwnership": "Own % Σ",
    "StackPattern": "Stack",
    "PrimaryStackTeam": "Primary Stack",
}
display_cols = {k: v for k, v in display_cols.items() if k in filtered.columns}

view = filtered[list(display_cols)].rename(columns=display_cols).copy()
view["Players"] = filtered["Players"].apply(lambda ps: ", ".join(ps))

st.subheader("Matching lineups")
st.dataframe(
    view,
    use_container_width=True,
    hide_index=True,
    height=480,
    column_config={
        "ROI": st.column_config.NumberColumn(format="%.1f"),
        "Win %": st.column_config.NumberColumn(format="%.2f"),
        "ITM %": st.column_config.NumberColumn(format="%.1f"),
        "Top10 %": st.column_config.NumberColumn(format="%.2f"),
        "Salary": st.column_config.NumberColumn(format="$%d"),
    },
)

# --------------------------------------------------------------------------- #
# Lineup detail
# --------------------------------------------------------------------------- #
st.subheader("Lineup detail")
sel = st.selectbox(
    "Inspect a lineup",
    filtered["LineupNum"].tolist(),
    format_func=lambda n: f"Lineup {n}"
    + (f"  —  ROI {table.loc[table.LineupNum == n, 'ROI'].iloc[0]:.1f}"
       if "ROI" in table.columns else ""),
)
detail = players[players["LineupNum"] == sel].copy()
slot_order = {"SP": 0, "P": 0, "C": 1, "1B": 2, "2B": 3, "3B": 4, "SS": 5, "OF": 6}
detail["_o"] = detail["Position"].map(slot_order).fillna(9)
detail = detail.sort_values("_o")
st.dataframe(
    detail[["Position", "FullName", "Team", "Salary", "Ownership"]].rename(
        columns={"FullName": "Player", "Ownership": "Own %"}
    ),
    use_container_width=True,
    hide_index=True,
    column_config={
        "Salary": st.column_config.NumberColumn(format="$%d"),
        "Own %": st.column_config.NumberColumn(format="%.2f"),
    },
)

# --------------------------------------------------------------------------- #
# Export to DraftKings upload format
# --------------------------------------------------------------------------- #
st.divider()
st.subheader("⬇️ Export lineups (DraftKings upload format)")

max_export = min(500, len(filtered))  # DK allows up to 500 lineups per file.
n_export = st.number_input(
    "How many of the top-ROI matching lineups to export?",
    min_value=1, max_value=max_export, value=max_export, step=1,
)
to_export = filtered["LineupNum"].head(int(n_export)).tolist()

upload_df, skipped = dd.build_dk_upload(players, to_export, slots, valid_ids)
if skipped:
    st.warning(
        f"{len(skipped)} lineup(s) had players whose IDs aren't in the current "
        f"template (these export with blank slots). Upload a matching DK template "
        f"to fix. Lineups: {skipped[:10]}{'…' if len(skipped) > 10 else ''}"
    )

csv_bytes = upload_df.to_csv(index=False).encode("utf-8")
st.download_button(
    "Download DraftKings upload CSV",
    data=csv_bytes,
    file_name="dk_upload_lineups.csv",
    mime="text/csv",
)

# Also offer the full filtered summary (with stats) as a plain CSV.
summary_csv = view.to_csv(index=False).encode("utf-8")
st.download_button(
    "Download filtered summary CSV (with stats)",
    data=summary_csv,
    file_name="lineup_summary.csv",
    mime="text/csv",
)
