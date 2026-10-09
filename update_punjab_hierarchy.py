import csv,json,re,time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT=Path(__file__).resolve().parent
BASE="https://sis.pesrp.edu.pk"
MASTER=ROOT/"data/master.csv"
SUMMARY=ROOT/"data/punjab_summary.json"
SCHOOLS=ROOT/"data/punjab_schools.json"
local=__import__("threading").local()

def sess():
    if not hasattr(local,"s"):
        local.s=requests.Session()
        local.s.mount("https://",HTTPAdapter(max_retries=Retry(total=2,backoff_factor=.4,status_forcelist=[429,500,502,503,504])))
        local.s.headers.update({"User-Agent":"Mozilla/5.0","X-Requested-With":"XMLHttpRequest","Accept":"application/json, text/javascript, */*; q=0.01","Referer":BASE+"/dashboard/index"})
    return local.s

def get(path,params=None):
    r=sess().get(BASE+path,params=params or {},timeout=25);r.raise_for_status();return r

def opts(path,params=None):
    r=get(path,params);body=r.text.strip()
    if body.startswith("{"):
        try:
            d=r.json();body=d.get("html") or d.get("data") or d.get("options") or ""
        except Exception: pass
    soup=BeautifulSoup(body,"html.parser");out=[]
    for o in soup.find_all("option"):
        v=(o.get("value") or "").strip();n=o.get_text(" ",strip=True)
        if v and n.lower() not in {"0","select","all","--",""}:out.append((v,n))
    return out

def number(v):
    try:return int(float(re.sub(r"[^0-9.-]","",str(v or "0")) or 0))
    except:return 0

def emis(v):return re.sub(r"\D","",str(v or ""))

def wing_of(markaz):
    x=markaz.upper()
    if "FEMALE" in x or "(W)" in x or x.endswith("-W"):return "Female Wing"
    if "MALE" in x or "(M)" in x or x.endswith("-M"):return "Male Wing"
    return "Unclassified"

def live(s):
    p={"district":s["did"],"tehsil":s["tid"],"markaz":s["mid"],"school":s["sid"],"classes":"0","s_id_emis_code":""}
    error=""
    for attempt in range(3):
        try:
            d=get("/dashboard_revamp/get_gender_summary_pie",p).json()
            if not isinstance(d,dict) or "total" not in d:raise ValueError("Unexpected SIS response")
            s.update(current=number(d["total"]),male=number(d.get("male_count")),female=number(d.get("female_count")),live_available=True)
            return s
        except Exception as e:
            error=str(e);time.sleep(.4*(attempt+1))
    s.update(current=0,male=0,female=0,live_available=False,fetch_error=error[:120]);return s

def aggregate(level,name,items,**where):
    base=sum(x["baseline"] for x in items);target=sum(x["target"] for x in items)
    liveitems=[x for x in items if x["live_available"]]
    current=sum(x["current"] for x in liveitems);den=target-base
    progress=(current-base)*100/den if den>0 else (100 if current>=target else 0)
    return dict(level=level,name=name,**where,school_count=len(items),baseline=base,target=target,current=current,
        male=sum(x["male"] for x in liveitems),female=sum(x["female"] for x in liveitems),remaining=target-current,
        progress_pct=round(max(0,min(100,progress)),2),live_schools=len(liveitems),failed_schools=len(items)-len(liveitems))

def main():
    with MASTER.open(newline="",encoding="utf-8-sig") as f:master=list(csv.DictReader(f))
    target={}
    for r in master:
        e=emis(r.get("EMIS"))
        if e:
            b=number(r.get("Total Baseline")) or number(r.get("Male Baseline"))+number(r.get("Female Baseline"))
            t=number(r.get("Total Target 2026")) or b+number(r.get("2026 Male target "))+number(r.get("2026 female target"))
            target[e]=(b,t)
    ds=opts("/user/get_districts")
    if not ds:raise RuntimeError("SIS district list empty; no data published")
    tasks=[]
    for did,dn in ds:
        for tid,tn in opts("/user/get_tehsils",{"district":did,"selectedTehsil":"false","all":"All"}):
            for mid,mn in opts("/user/get_markazes",{"tehsil":tid,"selectedMarkaz":"false","all":"All"}):
                tasks.append((did,dn,tid,tn,mid,mn))
    def inventory(task):
        did,dn,tid,tn,mid,mn=task;rows=[]
        for sid,label in opts("/user/get_schools",{"markaz":mid,"selectedSchool":"false","all":"All"}):
            e="";name=label
            if " - " in label:
                part,rest=label.split(" - ",1)
                if emis(part):e=emis(part);name=rest.strip()
            b,t=target.get(e,(0,0))
            rows.append(dict(did=did,tid=tid,mid=mid,sid=sid,district=dn.strip().upper(),tehsil=tn.strip().upper(),
                markaz=mn.strip(),wing=wing_of(mn),emis=e,school=name,baseline=b,target=t))
        return rows
    inventory=[];errors=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        fs={pool.submit(inventory,t):t for t in tasks}
        for f in as_completed(fs):
            try:inventory.extend(f.result())
            except Exception as e:errors.append(str(fs[f][5])+": "+str(e))
    if errors:raise RuntimeError("Incomplete SIS school inventory; no publish: "+"; ".join(errors[:5]))
    uniq={ (s["did"],s["tid"],s["mid"],s["sid"]):s for s in inventory };inventory=list(uniq.values())
    print("Schools discovered:",len(inventory),flush=True)
    done=[]
    with ThreadPoolExecutor(max_workers=18) as pool:
        fs=[pool.submit(live,s) for s in inventory]
        for i,f in enumerate(as_completed(fs),1):
            done.append(f.result())
            if i%1000==0:print("Enrollment fetched",i,"/",len(fs),flush=True)
    failures=sum(not s["live_available"] for s in done)
    if failures>max(25,int(len(done)*.02)):raise RuntimeError(f"{failures} schools failed; refusing to publish incomplete report")
    outschools=[{k:s.get(k) for k in ("district","tehsil","markaz","wing","emis","school","baseline","target","current","male","female","live_available","fetch_error")} for s in done]
    report=[aggregate("Punjab","Punjab Total",done)]
    for w in sorted({s["wing"] for s in done}):report.append(aggregate("Wing",w,[s for s in done if s["wing"]==w],wing=w))
    for d in sorted({s["district"] for s in done}):
        dg=[s for s in done if s["district"]==d];report.append(aggregate("District",d,dg,district=d))
        for t in sorted({s["tehsil"] for s in dg}):
            tg=[s for s in dg if s["tehsil"]==t];report.append(aggregate("Tehsil",t,tg,district=d,tehsil=t))
            for m in sorted({s["markaz"] for s in tg}):
                mg=[s for s in tg if s["markaz"]==m];report.append(aggregate("Markaz",m,mg,district=d,tehsil=t,markaz=m,wing=mg[0]["wing"]))
    stamp=datetime.now(ZoneInfo("Asia/Karachi")).isoformat()
    meta=dict(updated_at=stamp,district_count=len({s["district"] for s in done}),school_count=len(done),live_school_count=len(done)-failures,failed_school_count=failures,summary=report,
        options=dict(wings=sorted({s["wing"] for s in done}),districts=sorted({s["district"] for s in done}),tehsils=sorted({s["tehsil"] for s in done}),markazs=sorted({s["markaz"] for s in done})))
    SUMMARY.write_text(json.dumps(meta,separators=(",",":"),ensure_ascii=False),encoding="utf-8")
    SCHOOLS.write_text(json.dumps(dict(updated_at=stamp,schools=outschools),separators=(",",":"),ensure_ascii=False),encoding="utf-8")
    print("SUCCESS",len(done),"schools; failures",failures,"; report rows",len(report),flush=True)

if __name__=="__main__":main()
