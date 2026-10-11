"""Signal engineering and scoring for the Activation Radar.

Everything here is pure pandas, so it runs offline and is covered by tests.
Input is a tidy frame with one row per (market, concept, week):

    market   concept    term        date        trend
    JP       sneakers   スニーカー   2026-01-04  73

`trend` is Google Trends interest (0-100). Google scales each request to its
own peak, so every term is requested on its own: each series then uses the
full 0-100 range, and every feature below is relative to that series' own
history. Absolute search volume is never compared across terms or markets.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SERIES = ["market", "concept"]
MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def tidy_interest(df: pd.DataFrame) -> pd.DataFrame:
    """Turn a pytrends `interest_over_time()` frame into long format.

    Drops the current, still-filling week (`isPartial`), which would otherwise
    read as a sudden drop at the end of every series.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "term", "trend"])
    df = df.copy()
    if "isPartial" in df.columns:
        partial = df["isPartial"].astype(str).str.lower() == "true"
        df = df.loc[~partial].drop(columns=["isPartial"])
    df = df.rename_axis("date").reset_index()
    return df.melt(id_vars="date", var_name="term", value_name="trend")


def add_signal_features(df: pd.DataFrame, w_ma: int = 4, w_z: int = 12, yoy_lag: int = 52) -> pd.DataFrame:
    """Add a smoothed trend, a year-over-year index, and a rolling z-score per series.

    - trend_ma: `w_ma`-week moving average (smooths weekly noise).
    - yoy_idx:  this week vs the same week a year earlier, x100 (100 = flat).
    - z_score:  how far the smoothed trend sits above its own last `w_z` weeks.
    """
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["trend"] = pd.to_numeric(df["trend"], errors="coerce").fillna(0)
    out = []
    for _, g in df.groupby(SERIES, sort=False):
        g = g.sort_values("date").copy()
        g["trend_ma"] = g["trend"].rolling(w_ma, min_periods=max(1, w_ma // 2)).mean()
        g["yoy_idx"] = (g["trend"] / g["trend"].shift(yoy_lag) * 100.0).replace([np.inf, -np.inf], np.nan)
        roll = g["trend_ma"].rolling(w_z, min_periods=max(3, w_z // 3))
        g["z_score"] = (g["trend_ma"] - roll.mean()) / roll.std(ddof=0).replace(0, np.nan)
        out.append(g)
    out = pd.concat(out, ignore_index=True)
    out["month"] = out["date"].dt.to_period("M").astype(str)
    return out


def monthly_agg(df: pd.DataFrame, crit_z: float = 1.2) -> pd.DataFrame:
    """Roll weekly signals up to series x month; a 'hot week' has z >= crit_z."""
    agg = (df.groupby(SERIES + ["month"], as_index=False)
             .agg(avg_trend=("trend_ma", "mean"),
                  avg_yoy=("yoy_idx", "mean"),
                  avg_z=("z_score", "mean"),
                  weeks=("date", "count"),
                  hot_weeks=("z_score", lambda s: int((s >= crit_z).sum()))))
    agg["hot_share"] = agg["hot_weeks"] / agg["weeks"]
    return agg


def minmax(x: pd.Series) -> pd.Series:
    x = x.astype(float)
    lo, hi = np.nanmin(x), np.nanmax(x)
    if not hi > lo:
        return pd.Series(0.0, index=x.index)
    return (x - lo) / (hi - lo)


def activation_score(agg: pd.DataFrame, top_n: int = 3):
    """Score each series-month and return (scored, top_months, heatmap pivot).

    act_score = 0.6 * momentum (z) + 0.3 * growth (YoY) + 0.1 * share of hot weeks.
    z and YoY are clipped to robust ranges, then min-max scaled to 0-1, so the
    score is a 0-1 ranking aid, not a forecast. The weights are a stated
    judgment call; README > Limitations explains how to stress-test them.
    """
    # Score only months with both signals: the first year has no YoY baseline
    # and the first weeks have no z-score window, so they would read as zero.
    tmp = agg.dropna(subset=["avg_z", "avg_yoy"]).copy()
    tmp["z_scaled"] = minmax(tmp["avg_z"].clip(-3, 5))
    tmp["yoy_scaled"] = minmax(tmp["avg_yoy"].clip(80, 200))
    tmp["act_score"] = (0.6 * tmp["z_scaled"].fillna(0) + 0.3 * tmp["yoy_scaled"].fillna(0)
                        + 0.1 * tmp["hot_share"].fillna(0))
    top = (tmp.sort_values(SERIES + ["act_score"], ascending=[True, True, False])
              .groupby(SERIES, as_index=False).head(top_n)
              [SERIES + ["month", "act_score", "avg_yoy", "avg_z", "hot_share"]]
              .reset_index(drop=True))
    tmp["series"] = tmp["market"] + " · " + tmp["concept"]
    pivot = tmp.pivot(index="series", columns="month", values="act_score").fillna(0)
    return tmp.drop(columns="series"), top, pivot


def seasonality(df: pd.DataFrame) -> pd.DataFrame:
    """Typical yearly shape of each series, by calendar month.

    For every complete calendar year, each month's mean interest is divided by
    that year's mean, which removes growth or decline across years. Those
    ratios are averaged across years and reported x100, so 100 = an average
    month and 130 = a month that typically runs 30% above the year's average.
    """
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"])
    d["year"] = d["date"].dt.year
    d["cal_month"] = d["date"].dt.month
    monthly = d.groupby(SERIES + ["year", "cal_month"], as_index=False)["trend"].mean()
    full = monthly.groupby(SERIES + ["year"])["cal_month"].transform("nunique") == 12
    monthly = monthly.loc[full].copy()
    if monthly.empty:
        return pd.DataFrame(columns=SERIES + ["cal_month", "season_idx", "years"])
    year_mean = monthly.groupby(SERIES + ["year"])["trend"].transform("mean")
    monthly["ratio"] = monthly["trend"] / year_mean.replace(0, np.nan)
    out = (monthly.groupby(SERIES + ["cal_month"], as_index=False)
                  .agg(season_idx=("ratio", "mean"), years=("year", "nunique")))
    out["season_idx"] = out["season_idx"] * 100
    return out


def peak_table(season: pd.DataFrame) -> pd.DataFrame:
    """One row per series: the month it peaks, the month it bottoms out, and how strong the swing is."""
    rows = []
    for (market, concept), g in season.groupby(SERIES):
        g = g.dropna(subset=["season_idx"])
        if g.empty:
            continue
        hi = g.loc[g["season_idx"].idxmax()]
        lo = g.loc[g["season_idx"].idxmin()]
        rows.append({
            "market": market, "concept": concept,
            "peak_month": MONTH_LABELS[int(hi["cal_month"]) - 1], "peak_idx": round(hi["season_idx"], 1),
            "low_month": MONTH_LABELS[int(lo["cal_month"]) - 1], "low_idx": round(lo["season_idx"], 1),
            "swing_pts": round(hi["season_idx"] - lo["season_idx"], 1),
            "years": int(g["years"].max()),
        })
    return pd.DataFrame(rows)


def localization_share(pairs: pd.DataFrame) -> pd.DataFrame:
    """Compare the local search term with the English one inside the same market.

    `pairs` holds one row per (market, concept, date) with columns `local` and
    `english`, both from the same Google request, so they share one scale.
    local_share = local / (local + english) over the whole period.
    """
    g = pairs.groupby(["market", "concept", "local_term", "english_term"], as_index=False)[["local", "english"]].mean()
    total = (g["local"] + g["english"]).replace(0, np.nan)
    g["local_share"] = (g["local"] / total).round(3)
    return g.rename(columns={"local": "local_avg", "english": "english_avg"})
