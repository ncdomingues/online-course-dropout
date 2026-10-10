"""Download OULAD and prepare compact tables in data/processed/.

OULAD (Kuzilek, Hlosta & Zdrahal, 2017) covers 32,593 student enrolments in 7 Open University
modules (22 module presentations, 2013–2014), with demographics, assessment results and
10.6 million daily click records from the virtual learning environment (VLE).

Outputs
- students.parquet    one row per enrolment: demographics, registration dates, final result
- vle_daily.parquet   clicks per enrolment, day and activity type (aggregated from 10.6M rows)
- assessments.parquet every assessment with its deadline, and each student's submission and score

Run once:  python src/build_dataset.py   (about a minute)
"""
import io
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"
URL = "https://archive.ics.uci.edu/static/public/349/open+university+learning+analytics+dataset.zip"
KEY = ["code_module", "code_presentation", "id_student"]


def download():
    RAW.mkdir(parents=True, exist_ok=True)
    if (RAW / "studentVle.csv").exists():
        return
    print("Downloading OULAD (47 MB) ...")
    resp = requests.get(URL, timeout=300)
    resp.raise_for_status()
    zipfile.ZipFile(io.BytesIO(resp.content)).extractall(RAW)


def main():
    download()
    OUT.mkdir(parents=True, exist_ok=True)

    info = pd.read_csv(RAW / "studentInfo.csv")
    reg = pd.read_csv(RAW / "studentRegistration.csv", na_values="?")
    courses = pd.read_csv(RAW / "courses.csv")
    students = (info.merge(reg, on=KEY, how="left")
                .merge(courses, on=["code_module", "code_presentation"], how="left"))
    students["imd_band"] = students["imd_band"].replace({"10-20": "10-20%"})  # one band is mislabelled in the source
    students["withdrawn"] = (students["final_result"] == "Withdrawn").astype(int)
    students["year"] = students["code_presentation"].str[:4].astype(int)
    students.to_parquet(OUT / "students.parquet", index=False)
    print(f"students.parquet: {len(students):,} enrolments")

    print("Aggregating VLE clicks (10.6M rows) ...")
    vle_sites = pd.read_csv(RAW / "vle.csv", usecols=["id_site", "activity_type"])
    clicks = pd.read_csv(RAW / "studentVle.csv",
                         dtype={"id_student": "int32", "id_site": "int32", "date": "int16", "sum_click": "int32"})
    clicks = clicks.merge(vle_sites, on="id_site", how="left")
    daily = (clicks.groupby(KEY + ["date", "activity_type"], observed=True)["sum_click"].sum()
             .reset_index().rename(columns={"sum_click": "clicks"}))
    daily.to_parquet(OUT / "vle_daily.parquet", index=False)
    print(f"vle_daily.parquet: {len(daily):,} rows")

    assess = pd.read_csv(RAW / "assessments.csv", na_values="?")
    submitted = pd.read_csv(RAW / "studentAssessment.csv", na_values="?")
    sa = submitted.merge(assess, on="id_assessment", how="left")
    sa.to_parquet(OUT / "assessments.parquet", index=False)
    assess.to_parquet(OUT / "assessment_schedule.parquet", index=False)
    print(f"assessments.parquet: {len(sa):,} submissions")


if __name__ == "__main__":
    main()
