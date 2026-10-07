from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import csv
import json
import re
import time
import requests

ROOT = Path(__file__).resolve().parent
TARGET_CSV = ROOT / "data" / "district_targets.csv"
OUT = ROOT / "data" / "district_progress.json"

SIS_BASE = "https://sis.pesrp.edu.pk"
PAGE_URL = f"{SIS_BASE}/dashboard/index"
ENROLLMENT_URL = f"{SIS_BASE}/dashboard_revamp/get_gender_summary_pie"

DISTRICTS = [
    "ATTOCK","BAHAWALNAGAR","BAHAWALPUR","BHAKKAR","CHAKWAL","CHINIOT",
    "D.G. KHAN","FAISALABAD","GUJRANWALA","GUJRAT","HAFIZABAD","JHANG",
    "JHELUM","KASUR","KHANEWAL","KHUSHAB","LAHORE","LAYYAH","LODHRAN",
    "MANDI BAHA UD DIN","MIANWALI","MULTAN","MUZAFFARGARH","NANKANA SAHIB",
    "NAROWAL","OKARA","PAKPATTAN","RAHIMYAR KHAN","RAJANPUR","RAWALPINDI",
    "SAHIWAL","SARGODHA","SHEIKHUPURA","SIALKOT","T.T.SINGH","VEHARI",
    "KOT ADU","MURREE","TALAGANG","WAZIRABAD"
]

def get_csrf(session):
    r = session.get(PAGE_URL, timeout=45)
    r.raise_for_status()
    patterns = [
        r'csrf_test_name["\']?\s*[:=]\s*["\']([^"\']+)["\']',
        r'name=["\']csrf_test_name["\']\s+value=["\']([^"\']+)["\']',
        r'csrf_test_name\s*[:=]\s*["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, r.text, flags=re.I)
        if matches:
            return matches[-1]
    raise RuntimeError("SIS CSRF token not found in dashboard response.")

def fetch_district(session, district_id, csrf):
    payload = {
        "district": str(district_id),
        "tehsil": "",
        "markaz": "",
        "school": "",
        "s_id_emis_code": "",
        "csrf_test_name": csrf,
    }
    r = session.post(
        ENROLLMENT_URL,
        data=payload,
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Referer": PAGE_URL,
        },
        timeout=45,
    )
    r.raise_for_status()
    data = r.json()
    total = int(str(data["total"]).replace(",", ""))
    return {
        "male": int(str(data.get("male_count", 0)).replace(",", "")),
        "female": int(str(data.get("female_count", 0)).replace(",", "")),
        "other": int(str(data.get("other_count", 0)).replace(",", "")),
        "total": total,
    }

def main():
    targets = {}
    with TARGET_CSV.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = row["District"].strip().replace("\\", "")
            targets[name] = row

    missing = [d for d in DISTRICTS if d not in targets]
    if missing:
        raise RuntimeError("Districts missing from target CSV: " + ", ".join(missing))

    session = requests.Session()
    session.headers.update({
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "User-Agent": "Mozilla/5.0 District-Enrollment-Progress",
    })

    csrf = get_csrf(session)
    live = {}

    for district_id, district in enumerate(DISTRICTS, start=1):
        try:
            live[district] = fetch_district(session, district_id, getattr(session, "csrf_token", csrf))
        except Exception:
            csrf = get_csrf(session)
            live[district] = fetch_district(session, district_id, csrf)
        time.sleep(0.2)

    rows = []
    for district in DISTRICTS:
        row = targets[district]
        baseline = int(row["Baseline"])
        new_target = int(row["New Target"])
        expected = int(row["Expected"])
        current = live[district]["total"]
        required_increase = expected - baseline
        actual_increase = current - baseline
        progress = (actual_increase / required_increase * 100) if required_increase else 100

        rows.append({
            "district": district,
            "baseline": baseline,
            "new_target": new_target,
            "target": expected,
            "current": current,
            "male": live[district]["male"],
            "female": live[district]["female"],
            "other": live[district]["other"],
            "live_available": True,
            "increase": actual_increase,
            "remaining": max(expected - current, 0),
            "progress_pct": round(max(0, min(progress, 100)), 2),
            "target_progress_pct": round(current / expected * 100, 2) if expected else 0,
        })

    OUT.write_text(
        json.dumps({
            "updated_at": datetime.now(ZoneInfo("Asia/Karachi")).isoformat(),
            "source": ENROLLMENT_URL,
            "district_count": len(rows),
            "current_total": sum(x["current"] for x in rows),
            "districts": rows,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

if __name__ == "__main__":
    main()
