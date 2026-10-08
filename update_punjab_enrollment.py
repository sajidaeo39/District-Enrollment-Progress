from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import csv,json,re,time,requests
from concurrent.futures import ThreadPoolExecutor,as_completed
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parent
MASTER=ROOT/"data"/"master.csv"
OUT=ROOT/"data"/"live-data.json"
BASE="https://sis.pesrp.edu.pk"
TIMEOUT=45
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36"
HEAD={"Accept":"application/json, text/javascript, */*; q=0.01","Accept-Language":"en-US,en;q=0.9","X-Requested-With":"XMLHttpRequest","User-Agent":UA,"Referer":BASE+"/"}

def session():
 s=requests.Session();s.headers.update({"User-Agent":UA});r=s.get(BASE+"/",headers=HEAD,timeout=TIMEOUT);r.raise_for_status();return s
def csrf(s): return s.cookies.get("csrf_cookie_name","")
def req(s,ep,params):
 for attempt in range(1,6):
  try:
   p=dict(params);p["csrf_test_name"]=csrf(s);r=s.get(BASE+ep,params=p,headers=HEAD,timeout=TIMEOUT)
   if r.status_code==403:s=session();time.sleep(attempt);continue
   r.raise_for_status()
   if not r.text.strip().startswith("{"):raise RuntimeError("SIS returned non-JSON")
   return r.json()
  except Exception:
   if attempt==5:raise
   time.sleep(min(attempt*2,10))
def opts(data):
 soup=BeautifulSoup(data.get("html",""),"html.parser");out=[]
 for o in soup.find_all("option"):
  v=str(o.get("value","")).strip();t=" ".join(o.get_text(" ",strip=True).split())
  if v:out.append((v,t))
 return list(dict.fromkeys(out))
def wing(markaz,school):
 x=(markaz+" "+school).upper()
 if "SECONDARY" in x or "HIGH SCHOOL" in x or "HIGHER SECONDARY" in x or re.search(r"\b(GHSS|GHS|GGHS|HSS)\b",x):return "Secondary Wing"
 if "FEMALE" in x or "GIRLS" in x or re.search(r"\bGG[A-Z]",school.upper()) or re.search(r"\b(GGES|GGPS)\b",school.upper()):return "Female Wing"
 if "MALE" in x or "BOYS" in x or re.search(r"\b(GM[A-Z]|GB[A-Z])",school.upper()) or re.search(r"\b(GMES|GMPS)\b",school.upper()):return "Male Wing"
 return "Male Wing"
def enrol(s,district,tehsil,markaz,school):
 d=req(s,"/dashboard_revamp/get_gender_summary_pie",{"district":district,"tehsil":tehsil,"markaz":markaz,"school":school,"s_id_emis_code":""})
 def num(k):return int(re.sub(r"[^\d-]","",str(d.get(k,0))) or 0)
 return num("male_count"),num("female_count"),num("other_count"),num("total")
def main():
 if not MASTER.exists():raise RuntimeError("data/master.csv is missing. Upload the supplied Punjab master CSV to this path.")
 with MASTER.open(newline="",encoding="utf-8-sig") as f: master=list(csv.DictReader(f))
 required=["District","Tehsil","Markaz","EMIS","School Name","Male Baseline","Female Baseline","2026 Male target ","2026 female target","Total Baseline"]
 miss=[x for x in required if x not in master[0]]
 if miss:raise RuntimeError("Master CSV missing columns: "+", ".join(miss))
 by_emis={str(r["EMIS"]).strip():r for r in master}
 s=session();live={};fail=[]
 districts=list(dict.fromkeys(r["District"].strip() for r in master))
 # SIS district IDs follow the 40-district category order used by the existing Okara/Punjab collector.
 district_ids={name:i for i,name in enumerate(["ATTOCK","BAHAWALNAGAR","BAHAWALPUR","BHAKKAR","CHAKWAL","CHINIOT","D.G. KHAN","FAISALABAD","GUJRANWALA","GUJRAT","HAFIZABAD","JHANG","JHELUM","KASUR","KHANEWAL","KHUSHAB","LAHORE","LAYYAH","LODHRAN","MANDI BAHA UD DIN","MIANWALI","MULTAN","MUZAFFARGARH","NANKANA SAHIB","NAROWAL","OKARA","PAKPATTAN","RAHIMYAR KHAN","RAJANPUR","RAWALPINDI","SAHIWAL","SARGODHA","SHEIKHUPURA","SIALKOT","T.T.SINGH","VEHARI","KOT ADU","MURREE","TALAGANG","WAZIRABAD"],1)}
 def one(item):
  emis,r=item
  did=district_ids.get(r["District"].strip())
  if not did:return emis,None,"No district ID"
  # The same SIS hierarchy endpoints used by the Okara collector.
  ss=session()
  try:
   # Tehsil list endpoint is returned by SIS as HTML options.
   td=req(ss,"/user/get_tehsils",{"district":did,"selectedTehsil":"false","all":"All"})
   tehs=opts(td)
   # Match the master hierarchy row to its SIS tehsil.
   tv=next((v for v,t in tehs if t.strip().upper()==r["Tehsil"].strip().upper()),None)
   if not tv:
    return emis,None,"Tehsil not found in SIS"
   md=req(ss,"/user/get_markazes",{"tehsil":tv,"selectedMarkaz":"false","all":"All"})
   marks=opts(md);mv=next((v,t) for v,t in marks if t.strip().upper()==r["Markaz"].strip().upper())
   sd=req(ss,"/user/get_schools",{"markaz":mv[0],"selectedSchool":"false","all":"All"})
   schools=opts(sd)
   target=next(((v,t) for v,t in schools if str(r["EMIS"]).strip() in t),None)
   if not target:return emis,None,"School EMIS not found in SIS"
   return emis,enrol(ss,did,tv,mv[0],target[0]),None
  except Exception as e:return emis,None,str(e)
 # Use unique EMIS rows; each worker owns a session.
 with ThreadPoolExecutor(max_workers=12) as ex:
  futures=[ex.submit(one,(e,r)) for e,r in by_emis.items()]
  for f in as_completed(futures):
   e,got,err=f.result()
   if err:fail.append((e,err))
   else:live[e]=got
 if fail:raise RuntimeError(f"{len(fail)} school SIS requests failed. Nothing will be published.")
 rows=[]
 for r in master:
  e=str(r["EMIS"]).strip();got=live.get(e)
  if not got:raise RuntimeError("Missing live enrollment for EMIS "+e)
  mb=int(float(r["Male Baseline"] or 0));fb=int(float(r["Female Baseline"] or 0));mt=int(float(r["2026 Male target "] or 0));ft=int(float(r["2026 female target"] or 0))
  mc,fc,other,tc=got;target=mt+ft;remain=max(target-tc,0);progress=(tc/target*100) if target else 0
  rows.append({**r,"Wing":wing(r["Markaz"],r["School Name"]),"Male Baseline":mb,"Female Baseline":fb,"Male Target 2026":mt,"Female Target 2026":ft,"Male Current":mc,"Female Current":fc,"Other Current":other,"Total Current":tc,"Remaining":remain,"Progress %":round(progress,2)})
 payload={"updated_at":datetime.now(ZoneInfo("Asia/Karachi")).isoformat(),"source":BASE+"/dashboard_revamp/get_gender_summary_pie","school_count":len(rows),"rows":rows}
 tmp=OUT.with_suffix(".tmp");tmp.write_text(json.dumps(payload,ensure_ascii=False,separators=(",",":")),encoding="utf-8");tmp.replace(OUT)
 print("Published",len(rows),"schools.")
if __name__=="__main__":main()
