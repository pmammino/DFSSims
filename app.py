"""DFS Lineup Explorer — filter, query and export simulated DraftKings lineups.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import os
from collections import Counter

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


# One column per roster slot (Players shown first), then the four stats.
PLAYER_SLOT_COLS = ["P1", "P2", "C", "1B", "2B", "3B", "SS", "OF1", "OF2", "OF3"]


def make_view(df: pd.DataFrame) -> pd.DataFrame:
    """Build the display table: a column per position, then the 4 stats."""
    data = {c: df[c].values for c in PLAYER_SLOT_COLS}
    data["ROI"] = df["ROI"].values if "ROI" in df else None
    data["Win %"] = df["WinRate"].values if "WinRate" in df else None
    data["ITM %"] = df["ITMRate"].values if "ITMRate" in df else None
    data["Top10 %"] = df["Top10Rate"].values if "Top10Rate" in df else None
    data["Stacks"] = df.apply(_stack_label, axis=1).values
    data["Salary"] = df["TotalSalary"].values
    return pd.DataFrame(data)


def team_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Team-per-slot frame aligned to make_view's player columns (for colouring)."""
    return pd.DataFrame({c: df[f"{c}__team"].values for c in PLAYER_SLOT_COLS})


def _team_cell(team) -> str:
    return f"background-color: {TEAM_COLORS.get(team, '#ffffff')}; color: #1a1a1a"


def style_view(view: pd.DataFrame, teams: pd.DataFrame | None = None):
    """Colour the stat columns (good/bad) and tint each player cell by team.

    Per-cell colouring is costly, so it is skipped above STYLE_ROW_LIMIT rows
    (number formatting is always applied) to keep the table responsive.
    """
    fmt = {k: v for k, v in STAT_FMT.items() if k in view.columns}
    styler = view.style.format(fmt)
    if len(view) <= STYLE_ROW_LIMIT:
        cols = [c for c in STAT_COLS if c in view.columns and view[c].notna().any()]
        styler = styler.apply(_gradient, subset=cols)
        if teams is not None:
            team_css = teams.map(_team_cell)
            styler = styler.apply(lambda _: team_css, axis=None, subset=PLAYER_SLOT_COLS)
    return styler


# Above this many rows, skip per-cell colouring (it dominates render time).
STYLE_ROW_LIMIT = 500


RESULTS_COLCONFIG = {
    **{c: st.column_config.TextColumn(width="small") for c in PLAYER_SLOT_COLS},
    "Stacks": st.column_config.TextColumn(width="small"),
}

# --------------------------------------------------------------------------- #
# Header
# --------------------------------------------------------------------------- #
st.title("⚾ DFS Lineup Explorer")
st.caption("Filter and query simulated DraftKings lineups, sorted by ROI.")

# --------------------------------------------------------------------------- #
# Lineup template (DraftKings upload) — collapsible, top of page
# --------------------------------------------------------------------------- #
with st.expander("⚙️ Lineup template (DraftKings upload)", expanded=False):
    uploaded = st.file_uploader(
        "Upload DK Salaries template (.csv)",
        type="csv",
        help="DraftKings export changes often. Upload a fresh DKSalaries.csv to "
        "use the current player IDs for the export.",
    )
    try:
        if uploaded is not None:
            slots, dk_players = parse_uploaded_template(uploaded.getvalue())
            st.success(f"Using uploaded template ({len(dk_players)} players).")
        else:
            slots, dk_players = get_default_template()
            st.caption("Using bundled DKSalaries.csv.")
        valid_ids = set(dk_players["ID"].astype(str))
    except Exception as exc:  # noqa: BLE001
        st.error(f"Template error: {exc}")
        slots, valid_ids = (
            ["P", "P", "C", "1B", "2B", "3B", "SS", "OF", "OF", "OF"],
            None,
        )

# --------------------------------------------------------------------------- #
# Filters — across the top so the table can use the full width
# --------------------------------------------------------------------------- #
with st.expander("🔍 Filters", expanded=False):
    fc1, fc2, fc3, fc4 = st.columns(4)

    with fc1:
        st.markdown("**Players**")
        include_players = st.multiselect("Include players", all_player_names)
        include_mode = st.radio(
            "Match", ["All of these", "Any of these"], horizontal=True,
            help="Whether a lineup must contain all selected players or just one.",
        )
        exclude_players = st.multiselect("Exclude players", all_player_names)

    with fc2:
        st.markdown("**Stacks**")
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

    with fc3:
        st.markdown("**Teams in pool**")
        pool_teams = st.multiselect(
            "Restrict to teams", all_teams,
            help="Only lineups whose players all come from these teams.",
        )
        sal_min = int(table["TotalSalary"].min())
        sal_max = int(table["TotalSalary"].max())
        salary_range = st.slider("Total salary", sal_min, sal_max,
                                 (sal_min, sal_max), step=100)

    with fc4:
        st.markdown("**Minimum results**")

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



def render_explore():
    # --------------------------------------------------------------------------- #
    # Summary metrics
    # --------------------------------------------------------------------------- #
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
        st.warning("No lineups match the current filters. Loosen them above.")
        return

    # --------------------------------------------------------------------------- #
    # Results table
    # --------------------------------------------------------------------------- #
    st.subheader("Matching lineups")

    # Colouring every cell is expensive, so only the top-N (by ROI) are rendered in
    # the interactive table; the full filtered set still feeds the basket / export.
    n_matches = len(filtered)
    hc1, hc2 = st.columns([3, 1])
    hc1.caption(
        "Stats are colour-scaled green (good) → red (bad). Tick the checkboxes to "
        "pick lineups, then **Add checked to basket** (which persists across filters)."
    )
    show_n = int(hc2.number_input(
        "Rows shown", min_value=25, max_value=1000,
        value=min(250, n_matches), step=25,
        help="Top lineups by ROI rendered in the table. Lower this if sorting feels "
        "slow; the full filtered set is still used by 'Add all filtered' and export.",
    ))

    shown = filtered.head(show_n)
    view = make_view(shown)
    if n_matches > show_n:
        st.caption(f"Showing the top **{show_n:,}** of **{n_matches:,}** matching lineups by ROI.")

    # Selection toolbar.
    tb1, tb2, tb3 = st.columns([1.6, 1.4, 3])
    add_clicked = tb1.button("➕ Add checked to basket", use_container_width=True)
    if tb2.button("🗑️ Clear basket", use_container_width=True):
        st.session_state.selected = set()
        st.rerun()
    tb3.metric("In export basket", f"{len(st.session_state.selected):,}")

    event = st.dataframe(
        style_view(view, team_frame(shown)),
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
        st.session_state.selected |= set(shown.iloc[picked_rows]["LineupNum"].tolist())
        st.rerun()
    elif add_clicked:
        st.toast("No rows checked — tick lineups in the table first.")

    # Quick add-all of the current filter (handy after narrowing a search).
    if st.button(f"➕ Add all {n_matches:,} filtered lineups to basket"):
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
    _SLOT_ORDER = {"SP": 0, "RP": 0, "P": 0, "C": 1, "1B": 2,
                   "2B": 3, "3B": 4, "SS": 5, "OF": 6}
    detail = players[players["LineupNum"] == sel].copy()
    detail["_o"] = detail["Position"].map(
        lambda p: _SLOT_ORDER.get(str(p).split("/")[0], 9)
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
        return

    # Show the basket (all selected lineups, regardless of the current filter),
    # sorted by ROI, with a control to remove individual lineups.
    basket = table[table["LineupNum"].isin(selected_nums)].copy()
    if "ROI" in basket.columns:
        basket = basket.sort_values("ROI", ascending=False, na_position="last")

    basket_view = make_view(basket)
    st.dataframe(
        style_view(basket_view, team_frame(basket)),
        use_container_width=True, hide_index=True, height=240,
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

# --------------------------------------------------------------------------- #
# Summary analytics — aggregate breakdowns across a population of lineups
# --------------------------------------------------------------------------- #
def _hbar(df, label, value, value_title, top=40, color="#4c78a8"):
    """Horizontal bar chart sorted by value (frequency-style breakdowns)."""
    shown = df.head(top)
    chart = (
        alt.Chart(shown)
        .mark_bar(color=color)
        .encode(
            x=alt.X(f"{value}:Q", title=value_title),
            y=alt.Y(f"{label}:N", sort="-x", title=None),
            tooltip=list(df.columns),
        )
        .properties(height=max(140, 20 * len(shown)))
    )
    st.altair_chart(chart, use_container_width=True)


def _vbar(df, label, value, x_title, color="#54a24b"):
    """Vertical bar chart over an ordinal category (distributions)."""
    chart = (
        alt.Chart(df)
        .mark_bar(color=color)
        .encode(
            x=alt.X(f"{label}:O", title=x_title),
            y=alt.Y(f"{value}:Q", title="Lineups"),
            tooltip=list(df.columns),
        )
    )
    st.altair_chart(chart, use_container_width=True)


def _summary_population():
    """Pick which lineups to summarise; returns (frame, count, label)."""
    c1, c2 = st.columns([2, 1])
    basis = c1.radio(
        "Summarise", ["Top N by ROI", "Current filter", "All lineups"],
        horizontal=True, key="sum_basis",
    )
    if basis == "All lineups":
        return table, len(table), "all lineups"
    if basis == "Current filter":
        return filtered, len(filtered), "current filter"
    n = int(c2.number_input(
        "Top N", min_value=10, max_value=len(table),
        value=min(200, len(table)), step=10, key="sum_topn",
    ))
    pop = table
    if "ROI" in table.columns:
        pop = table.sort_values("ROI", ascending=False, na_position="last")
    return pop.head(n), min(n, len(table)), f"top {n} by ROI"


def render_summary():
    st.subheader("📊 Summary stats across lineups")
    pop, n_pop, label = _summary_population()
    if n_pop == 0:
        st.info("No lineups in this population — adjust the filters or basis.")
        return
    st.caption(f"Breaking down **{n_pop:,}** lineups ({label}).")
    pop_nums = set(pop["LineupNum"])
    pop_players = players[players["LineupNum"].isin(pop_nums)]

    t_players, t_stacks, t_teams = st.tabs(["Players", "Stacks", "Teams"])

    # ---- Player frequency -------------------------------------------------- #
    with t_players:
        freq = (
            pop_players.groupby(["FullName", "Team"])
            .agg(Lineups=("LineupNum", "nunique"), AvgOwn=("Ownership", "mean"))
            .reset_index()
        )
        freq["Freq %"] = (100 * freq["Lineups"] / n_pop).round(1)
        freq["AvgOwn"] = freq["AvgOwn"].round(2)
        freq = freq.sort_values("Lineups", ascending=False).reset_index(drop=True)
        freq = freq.rename(columns={"FullName": "Player", "AvgOwn": "Avg Own %"})

        topn = st.slider("Players shown", 10, 100, 30, 5, key="sum_players_n")
        _hbar(freq[["Player", "Freq %", "Lineups"]], "Player", "Freq %",
              "Appears in % of lineups", top=topn)
        st.dataframe(
            freq[["Player", "Team", "Lineups", "Freq %", "Avg Own %"]],
            use_container_width=True, hide_index=True, height=320,
        )
        st.download_button(
            "Download player frequency CSV",
            data=freq.to_csv(index=False).encode("utf-8"),
            file_name="player_frequency.csv", mime="text/csv",
        )

    # ---- Stack breakdowns -------------------------------------------------- #
    with t_stacks:
        sc1, sc2 = st.columns(2)

        with sc1:
            st.markdown("**Stack pattern frequency**")
            pat = pop["StackPattern"].value_counts().reset_index()
            pat.columns = ["Pattern", "Lineups"]
            pat["%"] = (100 * pat["Lineups"] / n_pop).round(1)
            _hbar(pat, "Pattern", "Lineups", "Lineups", top=20, color="#b279a2")

            st.markdown("**Primary stack size**")
            psize = (
                pop["PrimaryStackSize"].value_counts().sort_index().reset_index()
            )
            psize.columns = ["Size", "Lineups"]
            _vbar(psize, "Size", "Lineups", "Primary stack size")

        with sc2:
            st.markdown("**Primary stack team**")
            pteam = pop["PrimaryStackTeam"].replace("", "—").value_counts().reset_index()
            pteam.columns = ["Team", "Lineups"]
            pteam["%"] = (100 * pteam["Lineups"] / n_pop).round(1)
            _hbar(pteam, "Team", "Lineups", "Lineups", top=20, color="#e45756")

            st.markdown("**Team stacked (2+ hitters)**")
            stacked = Counter()
            for tc in pop["TeamCounts"]:
                for team, cnt in tc.items():
                    if cnt >= 2:
                        stacked[team] += 1
            sdf = pd.DataFrame(
                sorted(stacked.items(), key=lambda kv: -kv[1]),
                columns=["Team", "Lineups"],
            )
            if not sdf.empty:
                sdf["%"] = (100 * sdf["Lineups"] / n_pop).round(1)
                _hbar(sdf, "Team", "Lineups", "Lineups stacking team", top=20,
                      color="#4c78a8")

    # ---- Team breakdown ---------------------------------------------------- #
    with t_teams:
        team_tbl = (
            pop_players.groupby("Team")
            .agg(RosterSpots=("LineupNum", "count"),
                 Lineups=("LineupNum", "nunique"))
            .reset_index()
        )
        team_tbl["Spots/Lineup"] = (team_tbl["RosterSpots"] / n_pop).round(2)
        team_tbl["Lineup %"] = (100 * team_tbl["Lineups"] / n_pop).round(1)
        team_tbl = team_tbl.sort_values("RosterSpots", ascending=False).reset_index(drop=True)

        st.caption("Total roster spots (player appearances) and how many lineups "
                   "include each team.")
        _hbar(team_tbl, "Team", "RosterSpots", "Total roster spots", top=30,
              color="#72b7b2")
        st.dataframe(
            team_tbl[["Team", "RosterSpots", "Spots/Lineup", "Lineups", "Lineup %"]],
            use_container_width=True, hide_index=True,
        )


# --------------------------------------------------------------------------- #
# Tabs: explore/build vs. summary analytics
# --------------------------------------------------------------------------- #
tab_explore, tab_summary = st.tabs(["🔍 Explore & Build", "📊 Summary Stats"])

with tab_summary:
    render_summary()

with tab_explore:
    render_explore()
