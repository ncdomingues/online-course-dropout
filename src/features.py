"""Build a prediction snapshot for a given day of the course.

`snapshot(day)` answers: "on this day of the course, which students who are still enrolled
will go on to withdraw?" It only uses information available up to that day:
- background known at enrolment (demographics, previous attempts, credits, registration date)
- online activity (VLE clicks) up to that day
- assessments whose deadline has passed by that day

Students who had already withdrawn by that day are excluded: there is nothing left to predict.
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
KEY = ["code_module", "code_presentation", "id_student"]

CATEGORICAL = ["code_module", "gender", "region", "highest_education", "imd_band", "age_band", "disability"]
BACKGROUND = ["num_of_prev_attempts", "studied_credits", "registered_days_early", "module_presentation_length"]
ACTIVITY_TYPES = ["oucontent", "forumng", "homepage", "subpage", "resource", "url", "quiz", "ouwiki"]

_cache = {}


def _load():
    if not _cache:
        _cache["students"] = pd.read_parquet(PROC / "students.parquet")
        _cache["vle"] = pd.read_parquet(PROC / "vle_daily.parquet")
        _cache["assess"] = pd.read_parquet(PROC / "assessments.parquet")
        _cache["schedule"] = pd.read_parquet(PROC / "assessment_schedule.parquet")
    return _cache["students"], _cache["vle"], _cache["assess"], _cache["schedule"]


def snapshot(day):
    """Features and target for every student still enrolled on `day` (day 0 = course start)."""
    students, vle, assess, schedule = _load()

    # Still enrolled on `day`: never unregistered, or unregistered later
    s = students[students["date_unregistration"].isna() | (students["date_unregistration"] > day)].copy()
    s["registered_days_early"] = -s["date_registration"]
    s["imd_band"] = s["imd_band"].fillna("Unknown").replace("?", "Unknown")

    # --- online activity up to `day` ---
    v = vle[vle["date"] <= day]
    by_student = v.groupby(KEY)
    act = pd.DataFrame({
        "clicks_total": by_student["clicks"].sum(),
        "clicks_before_start": v[v["date"] < 0].groupby(KEY)["clicks"].sum(),
        "active_days": by_student["date"].nunique(),
        "last_active_day": by_student["date"].max(),
        "clicks_last_7_days": v[v["date"] > day - 7].groupby(KEY)["clicks"].sum(),
    })
    by_type = v[v["activity_type"].isin(ACTIVITY_TYPES)].pivot_table(
        index=KEY, columns="activity_type", values="clicks", aggfunc="sum")
    by_type.columns = [f"clicks_{c}" for c in by_type.columns]
    s = s.merge(act, left_on=KEY, right_index=True, how="left").merge(by_type, left_on=KEY, right_index=True, how="left")
    click_cols = [c for c in s if c.startswith("clicks_")] + ["active_days"]
    s[click_cols] = s[click_cols].fillna(0)
    for c in [f"clicks_{t}" for t in ACTIVITY_TYPES]:
        if c not in s:
            s[c] = 0.0
    s["days_since_last_active"] = (day - s["last_active_day"]).fillna(day + 30)  # never active: treat as long ago
    # Activity relative to classmates in the same presentation (courses differ a lot in how clickable they are)
    s["clicks_vs_classmates"] = s["clicks_total"] / s.groupby(["code_module", "code_presentation"])["clicks_total"].transform("median").clip(lower=1)

    # --- assessments due by `day` (exams are at the end, so they never count here) ---
    due = schedule[(schedule["date"] <= day) & (schedule["assessment_type"] != "Exam")]
    n_due = due.groupby(["code_module", "code_presentation"]).size().rename("assessments_due")
    s = s.merge(n_due, left_on=["code_module", "code_presentation"], right_index=True, how="left")
    s["assessments_due"] = s["assessments_due"].fillna(0)
    sub = assess[assess["id_assessment"].isin(due["id_assessment"]) & (assess["date_submitted"] <= day)].copy()
    sub["late"] = (sub["date_submitted"] > sub["date"]).astype(int)
    agg = sub.groupby(KEY).agg(assessments_submitted=("id_assessment", "nunique"), late_submissions=("late", "sum"),
                               mean_score=("score", "mean"))
    s = s.merge(agg, left_on=KEY, right_index=True, how="left")
    s[["assessments_submitted", "late_submissions"]] = s[["assessments_submitted", "late_submissions"]].fillna(0)
    s["assessments_missed"] = (s["assessments_due"] - s["assessments_submitted"]).clip(lower=0)
    s["mean_score"] = s["mean_score"].astype(float)

    s["day"] = day
    return s


ACTIVITY = (["clicks_total", "clicks_before_start", "active_days", "clicks_last_7_days", "days_since_last_active",
             "clicks_vs_classmates"] + [f"clicks_{t}" for t in ACTIVITY_TYPES])
ASSESSMENT = ["assessments_due", "assessments_submitted", "assessments_missed", "late_submissions", "mean_score"]
