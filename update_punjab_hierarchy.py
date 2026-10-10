import csv,json,re
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parent
MASTER=ROOT/"data/master.csv"
SUMMARY=ROOT/"data/punjab_summary.json"
SCHOOLS=ROOT/"data/punjab_schools.json"

def num(row,*keys):
    for key in keys:
        v=row.get(key)
        if v is None or str(v).strip()=="": continue
        try: return int(float(re.sub(r"[^0-9.-]","",str(v)) or 0))
        except (TypeError,ValueError): pass
    return 0

def txt(row,*keys):
    for key in keys:
        v=str(row.get(key) or "").strip()
        if v: return v
    return ""

def wing_for(row,markaz,school=""):
    # Prefer explicit source classification, then the dedicated Markaz/school type.
    w=txt(row,"Wing","wing","School Wing")
    label=w.strip().lower()
    if "secondary" in label: return "Secondary Wing"
    if "female" in label: return "Female"
    if "male" in label: return "Male"
    m=(markaz or "").strip().upper()
    s=(school or "").strip().upper()
    if "SECONDARY" in m or "HIGHER SECONDARY" in s or "HIGH SCHOOL" in s or "HIGHER SECONDARY SCHOOL" in s:
        return "Secondary Wing"
    if "FEMALE" in m or "(W)" in m or m.endswith("-W"): return "Female"
    if "MALE" in m or "(M)" in m or m.endswith("-M"): return "Male"
    return "Unclassified"

def main():
    if not MASTER.exists() or MASTER.stat().st_size < 100:
        raise SystemExit("data/master.csv is missing or empty; refusing to publish blank data")
    with MASTER.open(newline="",encoding="utf-8-sig") as f:
        source=list(csv.DictReader(f))
    if not source: raise SystemExit("data/master.csv has no rows")
    schools=[]; seen=set()
    for r in source:
        emis=re.sub(r"\D","",txt(r,"EMIS","emis","EMIS Code"))
        district=txt(r,"District","district").upper()
        tehsil=txt(r,"Tehsil","tehsil").upper()
        markaz=txt(r,"Markaz","markaz")
        school=txt(r,"School Name","school","School")
        if not (emis or school): continue
        # Avoid duplicate EMIS entries; keep the first complete source row.
        key=emis or (district,tehsil,markaz,school)
        if key in seen: continue
        seen.add(key)
        baseline=num(r,"Total Baseline","Baseline 2026","baseline") or num(r,"Male Baseline")+num(r,"Female Baseline")
        target=num(r,"Total Target 2026","Target 2026","target")
        if not target: target=num(r,"2026 Male target ","2026 Male target","Male Target 2026")+num(r,"2026 female target","2026 Female target","Female Target 2026")
        current=num(r,"Total Current","Current Enrollment 2026","current") or num(r,"Male Current")+num(r,"Female Current")
        wing=wing_for(r,markaz,school)
        schools.append(dict(district=district,tehsil=tehsil,markaz=markaz,wing=wing,emis=emis,school=school,
            baseline=baseline,target=target,current=current,male=num(r,"Male Current"),female=num(r,"Female Current"),
            live_available=True,fetch_error="",source="master.csv"))
    if not schools: raise SystemExit("No valid school rows found in data/master.csv")
    def agg(level,name,items,**where):
        b=sum(x["baseline"] for x in items); t=sum(x["target"] for x in items); c=sum(x["current"] for x in items); e=b+t; rem=e-c
        return dict(level=level,name=name,**where,school_count=len(items),baseline=b,target=t,expected=e,current=c,remaining=rem,
            progress_pct=round(rem*100/t,2) if t else 0,live_schools=len(items),failed_schools=0)
    report=[agg("Punjab","Punjab Total",schools)]
    for w in sorted({s["wing"] for s in schools}):
        group=[s for s in schools if s["wing"]==w]; report.append(agg("Wing",w,group,wing=w))
    for d in sorted({s["district"] for s in schools if s["district"]}):
        dg=[s for s in schools if s["district"]==d]; report.append(agg("District",d,dg,district=d))
        for t in sorted({s["tehsil"] for s in dg if s["tehsil"]}):
            tg=[s for s in dg if s["tehsil"]==t]; report.append(agg("Tehsil",t,tg,district=d,tehsil=t))
            for m in sorted({s["markaz"] for s in tg if s["markaz"]}):
                mg=[s for s in tg if s["markaz"]==m]; report.append(agg("Markaz",m,mg,district=d,tehsil=t,markaz=m,wing=mg[0]["wing"]))
    stamp=datetime.now(ZoneInfo("Asia/Karachi")).isoformat()
    meta=dict(updated_at=stamp,source="data/master.csv snapshot",data_source="master.csv snapshot (not a live SIS API fetch)",
        district_count=len({s["district"] for s in schools if s["district"]}),school_count=len(schools),
        live_school_count=len(schools),failed_school_count=0,
        options=dict(wings=sorted({s["wing"] for s in schools}),districts=sorted({s["district"] for s in schools if s["district"]}),
            tehsils=sorted({s["tehsil"] for s in schools if s["tehsil"]}),markazs=sorted({s["markaz"] for s in schools if s["markaz"]})),
        summary=report)
    payload=json.dumps(meta,separators=(",",":"),ensure_ascii=False).replace("<","\\u003c").replace(">","\\u003e").replace("&","\\u0026")
    SUMMARY.write_text(payload,encoding="utf-8")
    SCHOOLS.write_text(json.dumps(dict(updated_at=stamp,source="data/master.csv snapshot",schools=schools),separators=(",",":"),ensure_ascii=False),encoding="utf-8")
    print("SUCCESS: built",len(schools),"schools and",len(report),"Punjab/Wing/District/Tehsil/Markaz summary rows from master.csv",flush=True)

if __name__=="__main__": main()
