"""Stakeholder outputs: one Excel workbook and three PNG charts."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # render without a screen (CI, servers)
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import seaborn as sns  # noqa: E402

from .radar import MONTH_LABELS  # noqa: E402

sns.set_style("whitegrid")

# Japanese terms need a CJK font; use whichever one this machine has
# (Hiragino on macOS, Noto CJK on the Linux CI runner) after the default font.
_CJK = ["Hiragino Sans", "Noto Sans CJK JP", "Yu Gothic", "MS Gothic"]
_installed = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
plt.rcParams["font.family"] = ["DejaVu Sans"] + [f for f in _CJK if f in _installed]

README_ROWS = [
    ("What this is", "Google Trends search interest turned into timing signals per market and product concept."),
    ("Market_Peaks", "Each concept's typical peak and low month per market (from complete calendar years)."),
    ("Seasonality", "Typical month vs the year's average; 100 = average month, 130 = 30% above."),
    ("Localization", "Local search term vs the English term inside each market; local_share near 1 = locals search in their own word."),
    ("Activation_Radar", "Momentum score per month: 0.6 z-score + 0.3 YoY growth + 0.1 share of hot weeks, scaled 0-1."),
    ("Top_Months", "Three highest-scoring months per series."),
    ("Caveat", "Search interest is not demand. Validate with site, search, and conversion data before spending."),
]


def _autosize(ws, df: pd.DataFrame) -> None:
    for j, col in enumerate(df.columns):
        values = df[col].astype(str)
        p90 = int(values.str.len().quantile(0.9)) if not values.empty else 10
        ws.set_column(j, j, max(10, min(60, max(len(str(col)), p90) + 2)))


def export_excel(path: Path, signals, scored, top_months, season, peaks, localization) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    season_wide = season.copy()
    season_wide["month"] = season_wide["cal_month"].map(lambda m: MONTH_LABELS[m - 1])
    season_wide = (season_wide.pivot(index=["market", "concept"], columns="month", values="season_idx")
                   .reindex(columns=MONTH_LABELS).round(1).reset_index())
    sheets = {
        "Readme": pd.DataFrame(README_ROWS, columns=["sheet", "what it shows"]),
        "Market_Peaks": peaks,
        "Seasonality": season_wide,
        "Localization": localization,
        "Activation_Radar": scored.sort_values(["market", "concept", "month"]).round(3),
        "Top_Months": top_months.round(3),
        "Signals_preview": signals[["market", "concept", "term", "date", "trend", "trend_ma", "yoy_idx", "z_score"]]
        .head(2000).round(3),
    }
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        for name, df in sheets.items():
            if df is None or df.empty:
                df = pd.DataFrame({"note": ["not generated in this run"]})
            df.to_excel(writer, index=False, sheet_name=name)
            _autosize(writer.sheets[name], df)


def plot_seasonality(season: pd.DataFrame, path: Path) -> None:
    pv = season.assign(series=season["market"] + " · " + season["concept"])
    pv = pv.pivot(index="series", columns="cal_month", values="season_idx").reindex(columns=range(1, 13))
    pv.columns = MONTH_LABELS
    fig, ax = plt.subplots(figsize=(11, 1.2 + 0.38 * len(pv)))
    sns.heatmap(pv, cmap="RdBu_r", center=100, vmin=60, vmax=140, annot=True, fmt=".0f",
                annot_kws={"size": 7}, linewidths=.3, cbar_kws={"label": "100 = average month"}, ax=ax)
    ax.set_title("When each market searches: typical month vs yearly average")
    ax.set_xlabel(""); ax.set_ylabel("")
    fig.tight_layout(); fig.savefig(path, dpi=160); plt.close(fig)


def plot_activation(pivot: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(14, 1.5 + 0.35 * pivot.shape[0]))
    sns.heatmap(pivot, cmap="viridis", vmin=0, vmax=1, linewidths=.2,
                cbar_kws={"label": "Activation score (0-1)"}, ax=ax)
    ax.set_title("Activation Radar: momentum by month")
    ax.set_xlabel(""); ax.set_ylabel("")
    fig.tight_layout(); fig.savefig(path, dpi=160); plt.close(fig)


def plot_localization(loc: pd.DataFrame, path: Path) -> None:
    d = loc.assign(label=loc["market"] + ": " + loc["local_term"] + " vs " + loc["english_term"])
    d = d.sort_values("local_share")
    fig, ax = plt.subplots(figsize=(8, 1 + 0.4 * len(d)))
    ax.barh(d["label"], d["local_share"] * 100, color="#2b6cb0")
    ax.axvline(50, color="#888", lw=1, ls="--")
    ax.set_xlim(0, 100)
    ax.set_xlabel("Share of search interest going to the local term (%)")
    ax.set_title("Same market, same request: local word vs English word")
    for y, v in enumerate(d["local_share"] * 100):
        ax.text(min(v + 1, 93), y, f"{v:.0f}%", va="center", fontsize=8)
    fig.tight_layout(); fig.savefig(path, dpi=160); plt.close(fig)
