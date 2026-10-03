from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import io,json,urllib.request
import pandas as pd
ROOT=Path(__file__).resolve().parent
TARGET_CSV=ROOT/"data/district_targets.csv"
SOURCE_XLSX="https://raw.githubusercontent.com/sajidaeo39/okara-enrollment-live/main/data/Okara_Latest_Enrollment_Gender_Latest.xlsx"
OUT=ROOT/"data/district_progress.json"
def read_live():
    with urllib.request.urlopen(SOURCE_XLSX,timeout=60) as r: raw=r.read()
    df=pd.read_excel(io.BytesIO(raw),sheet_name="Gender Summary")
    df.columns=[str(c).strip() for c in df.columns]
    if "Total Enrollment" in df.columns: total=pd.to_numeric(df["Total Enrollment"],errors="coerce").fillna(0).sum()
    else:
        cols=[c for c in ["Male Enrollment","Female Enrollment","Other Enrollment"] if c in df.columns]
        total=sum(pd.to_numeric(df[c],errors="coerce").fillna(0).sum() for c in cols)
    return {"OKARA":int(total)}
def main():
    df=pd.read_csv(TARGET_CSV); live=read_live(); rows=[]
    for _,r in df.iterrows():
        d=str(r["District"]).strip(); b=int(r["Baseline"]); nt=int(r["New Target"]); target=int(r["Expected"]); ok=d.upper() in live; cur=int(live[d.upper()]) if ok else None
        gain=cur-b if ok else None; rem=max(target-cur,0) if ok else None; p=(gain/max(target-b,1)*100) if ok else None; tp=(cur/target*100) if ok else None
        rows.append({"district":d,"baseline":b,"new_target":nt,"target":target,"current":cur,"live_available":ok,"increase":gain,"remaining":rem,"progress_pct":round(max(0,min(p,100)),2) if p is not None else None,"target_progress_pct":round(max(0,min(tp,100)),2) if tp is not None else None})
    OUT.write_text(json.dumps({"updated_at":datetime.now(ZoneInfo("Asia/Karachi")).isoformat(),"source":SOURCE_XLSX,"districts":rows},ensure_ascii=False,indent=2),encoding="utf-8")
if __name__=="__main__": main()
