"""DFS Lineup Explorer — filter, query and export simulated DraftKings lineups.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import os

import altair as alt
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

# Persistent export basket: lineup numbers the user has selected. This survives
# filter changes and reruns until the user unchecks or clears them.
if "selected" not in st.session_state:
    st.session_state.selected = set()

# --------------------------------------------------------------------------- #
# Display helpers — clean lineup views + good/bad coloration
# --------------------------------------------------------------------------- #
# The only summary stats shown (all "higher is better" -> green = good).
STAT_COLS = ["ROI", "Win %", "ITM %", "Top10 %"]
STAT_FMT = {"ROI": "{:.1f}", "Win %": "{:.2f}", "ITM %": "{:.1f}",
            "Top10 %": "{:.2f}", "Salary": "${:,.0f}"}

# Excel-style 3-colour scale: red (low) -> yellow (mid) -> green (high).
_SCALE = [(248, 105, 107), (255, 235, 132), (99, 190, 123)]


def _lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _heat_color(t: float) -> str:
    t = max(0.0, min(1.0, t))
    c = _lerp(_SCALE[0], _SCALE[1], t / 0.5) if t < 0.5 \
        else _lerp(_SCALE[1], _SCALE[2], (t - 0.5) / 0.5)
    return f"background-color: rgb({c[0]},{c[1]},{c[2]}); color: #1a1a1a"


def _gradient(col: pd.Series) -> list[str]:
    """Per-column red->green background, scaled across the visible rows."""
    vals = pd.to_numeric(col, errors="coerce")
    vmin, vmax = vals.min(), vals.max()
    rng = vmax - vmin
    return [
        _heat_color(0.5 if (rng == 0 or pd.isna(v)) else (v - vmin) / rng)
        for v in vals
    ]


# Consistent pastel colour per team (used to highlight stacks in the detail view).
_TEAM_PALETTE = [
    "#cfe8ff", "#ffe0cc", "#d9f2d0", "#f3d9ff", "#fff2cc", "#d0f0f0",
    "#ffd6e0", "#e8e8e8", "#e3eaa7", "#c9e4de", "#f7c9c9", "#cdd6ff",
    "#ffe8b3", "#d7f0c2",
]
TEAM_COLORS = {t: _TEAM_PALETTE[i % len(_TEAM_PALETTE)] for i, t in enumerate(all_teams)}


def _stack_label(row) -> str:
    """Readable stack summary, e.g. 'Angels 5 / Dodgers 2'."""
    parts = []
    if row["PrimaryStackSize"] >= 2:
        parts.append(f"{row['PrimaryStackTeam']} {row['PrimaryStackSize']}")
    if row["SecondaryStackSize"] >= 2:
        parts.append(f"{row['SecondaryStackTeam']} {row['SecondaryStackSize']}")
    return " / ".join(parts) if parts else "—"


def make_view(df: pd.DataFrame) -> pd.DataFrame:
    """Build the clean, display-ready lineup table (id, 4 stats, stack, players)."""
    return pd.DataFrame(
        {
            "Lineup #": df["LineupNum"].values,
            "ROI": df["ROI"].values if "ROI" in df else None,
            "Win %": df["WinRate"].values if "WinRate" in df else None,
            "ITM %": df["ITMRate"].values if "ITMRate" in df else None,
            "Top10 %": df["Top10Rate"].values if "Top10Rate" in df else None,
            "Stacks": df.apply(_stack_label, axis=1).values,
            "Players": df["Players"].apply(", ".join).values,
            "Salary": df["TotalSalary"].values,
        }
    )


def style_view(view: pd.DataFrame):
    """Apply good/bad colouring to the stat columns and number formatting."""
    cols = [c for c in STAT_COLS if c in view.columns and view[c].notna().any()]
    return (
        view.style.apply(_gradient, subset=cols)
        .format({k: v for k, v in STAT_FMT.items() if k in view.columns})
    )


RESULTS_COLCONFIG = {
    "Lineup #": st.column_config.NumberColumn(width="small"),
    "Players": st.column_config.TextColumn(width="large"),
    "Stacks": st.column_config.TextColumn(width="medium"),
}

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
view = make_view(filtered)

st.subheader("Matching lineups")
st.caption(
    "Stats are colour-scaled green (good) → red (bad) across the matches. "
    "Tick the checkboxes to pick lineups, then **Add selected to basket**. "
    "The basket persists as you change filters."
)

# Selection toolbar.
tb1, tb2, tb3 = st.columns([1.6, 1.4, 3])
add_clicked = tb1.button("➕ Add checked to basket", use_container_width=True)
if tb2.button("🗑️ Clear basket", use_container_width=True):
    st.session_state.selected = set()
    st.rerun()
tb3.metric("In export basket", f"{len(st.session_state.selected):,}")

event = st.dataframe(
    style_view(view),
    use_container_width=True,
    hide_index=True,
    height=460,
    column_config=RESULTS_COLCONFIG,
    on_select="rerun",
    selection_mode="multi-row",
    key="results_grid",
)

picked_rows = event.selection["rows"] if event and event.selection else []
if add_clicked and picked_rows:
    st.session_state.selected |= set(filtered.iloc[picked_rows]["LineupNum"].tolist())
    st.rerun()
elif add_clicked:
    st.toast("No rows checked — tick lineups in the table first.")

# Quick add-all of the current filter (handy after narrowing a search).
if st.button(f"➕ Add all {len(filtered):,} filtered lineups to basket"):
    st.session_state.selected |= set(filtered["LineupNum"].tolist())
    st.rerun()

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
detail["_o"] = detail["Position"].map(
    lambda p: dd.SLOT_DISPLAY_ORDER.get(str(p).split("/")[0], 9)
)
detail = detail.sort_values("_o")
detail_view = detail[["Position", "FullName", "Team", "Salary", "Ownership"]].rename(
    columns={"FullName": "Player", "Ownership": "Own %"}
)


def _team_row(row):
    # Tint each row by its team so stacks are obvious at a glance.
    bg = TEAM_COLORS.get(row["Team"], "#ffffff")
    return [f"background-color: {bg}; color: #1a1a1a"] * len(row)


st.dataframe(
    detail_view.style.apply(_team_row, axis=1).format(
        {"Salary": "${:,.0f}", "Own %": "{:.2f}"}
    ),
    use_container_width=True,
    hide_index=True,
)

# --------------------------------------------------------------------------- #
# Export basket
# --------------------------------------------------------------------------- #
st.divider()
st.subheader("🧺 Export basket")

selected_nums = st.session_state.selected
if not selected_nums:
    st.info(
        "No lineups selected yet. Tick **✓ Select** on lineups above (selections "
        "stay in the basket as you change filters), then export them here."
    )
    st.stop()

# Show the basket (all selected lineups, regardless of the current filter),
# sorted by ROI, with a control to remove individual lineups.
basket = table[table["LineupNum"].isin(selected_nums)].copy()
if "ROI" in basket.columns:
    basket = basket.sort_values("ROI", ascending=False, na_position="last")

basket_view = make_view(basket)
st.dataframe(
    style_view(basket_view), use_container_width=True, hide_index=True, height=240,
    column_config=RESULTS_COLCONFIG,
)

rc1, rc2 = st.columns([3, 1])
to_remove = rc1.multiselect(
    "Remove specific lineups from the basket",
    sorted(selected_nums),
    help="Removes only the chosen lineups; the rest stay selected.",
)
if rc2.button("Remove", use_container_width=True) and to_remove:
    st.session_state.selected -= set(to_remove)
    st.rerun()

# --------------------------------------------------------------------------- #
# Exposure — guard against being overweight on any one player / team
# --------------------------------------------------------------------------- #
st.markdown("#### 📊 Exposure across selected lineups")
n_sel = len(selected_nums)
ec1, ec2 = st.columns([2, 1])
target = ec1.slider(
    "Max exposure target %", 5, 100, 50, 5,
    help="Players/teams above this line are highlighted in red — a signal you "
    "may be overweight.",
)
top_n = ec2.number_input("Show top N", min_value=5, max_value=200, value=30, step=5)

sel_players = players[players["LineupNum"].isin(selected_nums)]


def _exposure_chart(df, label_col, count_label):
    """Sorted horizontal bar chart of exposure %, with a target rule line."""
    shown = df.head(int(top_n))
    bars = (
        alt.Chart(shown)
        .mark_bar()
        .encode(
            x=alt.X("Exposure:Q", title="Exposure %", scale=alt.Scale(domain=[0, 100])),
            y=alt.Y(f"{label_col}:N", sort="-x", title=None),
            color=alt.condition(
                alt.datum.Exposure > target,
                alt.value("#e45756"),  # red = over target
                alt.value("#4c78a8"),
            ),
            tooltip=[
                alt.Tooltip(f"{label_col}:N", title=count_label),
                alt.Tooltip("Lineups:Q"),
                alt.Tooltip("Exposure:Q", title="Exposure %", format=".1f"),
            ],
        )
        .properties(height=max(150, 18 * len(shown)))
    )
    rule = (
        alt.Chart(pd.DataFrame({"t": [target]}))
        .mark_rule(color="#999", strokeDash=[4, 4])
        .encode(x="t:Q")
    )
    st.altair_chart(bars + rule, use_container_width=True)


tab_players, tab_teams = st.tabs(["Players", "Teams"])

with tab_players:
    pe = (
        sel_players.groupby(["FullName", "Team"])["LineupNum"]
        .nunique()
        .reset_index(name="Lineups")
    )
    pe["Exposure"] = (100 * pe["Lineups"] / n_sel).round(1)
    pe = pe.sort_values("Exposure", ascending=False).reset_index(drop=True)
    over = pe[pe["Exposure"] > target]
    if not over.empty:
        st.caption(
            f"⚠️ {len(over)} player(s) above your {target}% target: "
            + ", ".join(f"{r.FullName} ({r.Exposure:.0f}%)" for r in over.head(8).itertuples())
            + ("…" if len(over) > 8 else "")
        )
    _exposure_chart(pe, "FullName", "Player")

with tab_teams:
    te = (
        sel_players.groupby("Team")["LineupNum"]
        .nunique()
        .reset_index(name="Lineups")
    )
    te["Exposure"] = (100 * te["Lineups"] / n_sel).round(1)
    te = te.sort_values("Exposure", ascending=False).reset_index(drop=True)
    st.caption("Share of selected lineups containing at least one player from each team.")
    _exposure_chart(te, "Team", "Team")

# --------------------------------------------------------------------------- #
# Export the selected lineups
# --------------------------------------------------------------------------- #
st.markdown("#### ⬇️ Export selected lineups")
export_nums = basket["LineupNum"].tolist()  # ROI-sorted
if len(export_nums) > 500:
    st.warning(
        f"{len(export_nums)} lineups selected — DraftKings allows 500 per file. "
        "Only the top 500 by ROI will be exported."
    )
    export_nums = export_nums[:500]

upload_df, skipped = dd.build_dk_upload(players, export_nums, slots, valid_ids)
if skipped:
    st.warning(
        f"{len(skipped)} selected lineup(s) had players whose IDs aren't in the "
        f"current template (these export with blank slots). Upload a matching DK "
        f"template above to fix. Lineups: {skipped[:10]}{'…' if len(skipped) > 10 else ''}"
    )

e1, e2 = st.columns(2)
e1.download_button(
    f"📤 Export Lineups — DK upload ({len(export_nums)})",
    data=upload_df.to_csv(index=False).encode("utf-8"),
    file_name="dk_upload_lineups.csv",
    mime="text/csv",
    use_container_width=True,
)
e2.download_button(
    "Export selected summary (with stats)",
    data=basket_view.to_csv(index=False).encode("utf-8"),
    file_name="selected_lineup_summary.csv",
    mime="text/csv",
    use_container_width=True,
)
