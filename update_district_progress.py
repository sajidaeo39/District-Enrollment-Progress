from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
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
TIMEOUT = 35
WORKERS = 8
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
    response = session.get(PAGE_URL, timeout=TIMEOUT)
    response.raise_for_status()
    patterns = [
        r'csrf_test_name["\']?\s*[:=]\s*["\']([^"\']+)["\']',
        r'name=["\']csrf_test_name["\']\s+value=["\']([^"\']+)["\']',
        r'csrf_test_name\s*[:=]\s*["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, response.text, flags=re.I)
        if matches:
            return matches[-1]
    # Some SIS responses rely on the cookie and do not expose a token in page HTML.
    return session.cookies.get("csrf_cookie_name", "")

def new_session():
    s = requests.Session()
    s.headers.update({
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": PAGE_URL,
        "Origin": SIS_BASE,
    })
    csrf = get_csrf(s)
    return s, csrf

def fetch_district(district_id):
    last_error = None
    for attempt in range(1, 5):
        session = None
        try:
            session, csrf = new_session()
            payload = {
                "district": str(district_id),
                "tehsil": "",
                "markaz": "",
                "school": "",
                "s_id_emis_code": "",
                "csrf_test_name": csrf,
            }
            response = session.post(ENROLLMENT_URL, data=payload, timeout=TIMEOUT)
            if response.status_code in (403, 419):
                raise RuntimeError(f"SIS rejected session/CSRF (HTTP {response.status_code})")
            response.raise_for_status()
            data = response.json()

            def number(key):
                value = re.sub(r"[^0-9-]", "", str(data.get(key, 0)))
                return int(value or 0)

            if "total" not in data:
                raise RuntimeError("SIS response did not include total enrollment")
            result = {
                "male": number("male_count"),
                "female": number("female_count"),
                "other": number("other_count"),
                "total": number("total"),
            }
            if result["total"] <= 0:
                raise RuntimeError("SIS returned zero/invalid total; refusing to publish")
            return result
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(min(attempt, 3))
        finally:
            if session is not None:
                session.close()
    raise RuntimeError(f"District ID {district_id}: failed after 4 attempts: {last_error}")

def main():
    with TARGET_CSV.open(newline="", encoding="utf-8-sig") as f:
        target_rows = {
            row["District"].strip().replace("\\", ""): row
            for row in csv.DictReader(f)
        }

    missing = [name for name in DISTRICTS if name not in target_rows]
    if missing:
        raise RuntimeError("Districts missing from target CSV: " + ", ".join(missing))

    # Sum 2026 gender targets from the supplied school-level master file.
    master_path = ROOT / "data" / "master.csv"
    gender_targets = {name: {"target_boys_2026": 0, "target_girls_2026": 0} for name in DISTRICTS}
    with master_path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            district = row.get("District", "").strip()
            if district not in gender_targets:
                continue
            boys = re.sub(r"[^0-9-]", "", str(row.get("2026 Male target ", "0"))) or "0"
            girls = re.sub(r"[^0-9-]", "", str(row.get("2026 female target", "0"))) or "0"
            gender_targets[district]["target_boys_2026"] += int(boys)
            gender_targets[district]["target_girls_2026"] += int(girls)

    # Fetch 8 district aggregates concurrently instead of querying thousands of schools.
    live = {}
    errors = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch_district, idx): name
                   for idx, name in enumerate(DISTRICTS, start=1)}
        for future in as_completed(futures):
            name = futures[future]
            try:
                live[name] = future.result()
                print(f"Fetched {name}: {live[name]['total']:,} students")
            except Exception as exc:
                errors.append(f"{name}: {exc}")

    if errors:
        raise RuntimeError("No data published because district fetches failed:\n" + "\n".join(errors))
    if len(live) != 40:
        raise RuntimeError(f"Expected 40 districts, received {len(live)}")

    rows = []
    for district in DISTRICTS:
        target_row = target_rows[district]
        baseline = int(target_row["Baseline"])
        new_target = int(target_row["New Target"])
        expected = int(target_row["Expected"])
        current = live[district]["total"]
        required_increase = expected - baseline
        actual_increase = current - baseline
        progress = actual_increase / required_increase * 100 if required_increase else 100
        rows.append({
            "district": district,
            "baseline": baseline,
            "new_target": new_target,
            "target": expected,
            "current": current,
            "male": live[district]["male"],
            "female": live[district]["female"],
            "other": live[district]["other"],
            "target_boys_2026": gender_targets[district]["target_boys_2026"],
            "target_girls_2026": gender_targets[district]["target_girls_2026"],
            "live_available": True,
            "increase": actual_increase,
            "remaining": max(expected - current, 0),
            "progress_pct": round(max(0, min(progress, 100)), 2),
            "target_progress_pct": round(current / expected * 100, 2) if expected else 0,
        })

    payload = {
        "updated_at": datetime.now(ZoneInfo("Asia/Karachi")).isoformat(),
        "source": ENROLLMENT_URL,
        "district_count": len(rows),
        "current_total": sum(row["current"] for row in rows),
        "districts": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUT.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(OUT)
    print(f"SUCCESS: published fresh SIS totals for {len(rows)} districts.")

if __name__ == "__main__":
    main()
