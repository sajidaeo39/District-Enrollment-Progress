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

def wing_of(markaz,school=""):
    # Classify school level first: high/higher-secondary schools belong to Secondary Wing,
    # even when their Markaz is a male/female elementary Markaz.
    x=(str(markaz or "")+" "+str(school or "")).upper()
    if any(k in str(school or "").upper() for k in ("HIGH SCHOOL","HIGHER SECONDARY","SECONDARY SCHOOL","HSS")):
        return "Secondary Wing"
    if "FEMALE" in x or "(W)" in x or x.endswith("-W") or "GIRLS" in str(school or "").upper():
        return "Female Elementary Wing"
    if "MALE" in x or "(M)" in x or x.endswith("-M") or "BOYS" in str(school or "").upper():
        return "Male Elementary Wing"
    return "Unclassified"

def live_aggregate(did,tid="0",mid="0"):
    # Use SIS's own hierarchy total because the sum of school endpoints can differ.
    p={"district":did,"tehsil":tid or "0","markaz":mid or "0","school":"0","classes":"0","s_id_emis_code":""}
    error=""
    for attempt in range(3):
        try:
            d=get("/dashboard_revamp/get_gender_summary_pie",p).json()
            if not isinstance(d,dict) or "total" not in d:
                raise ValueError("Unexpected SIS aggregate response")
            return {"current":number(d["total"]),"male":number(d.get("male_count")),"female":number(d.get("female_count"))}
        except Exception as e:
            error=str(e);time.sleep(.4*(attempt+1))
    raise RuntimeError("SIS aggregate unavailable for "+str((did,tid,mid))+": "+error[:120])

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

def aggregate(level,name,items,official=None,**where):
    wing=str(where.get("wing") or "")
    if "Male" in wing:
        base=sum(x.get("male_baseline",0) for x in items);target=sum(x.get("male_target",0) for x in items)
    elif "Female" in wing:
        base=sum(x.get("female_baseline",0) for x in items);target=sum(x.get("female_target",0) for x in items)
    else:
        base=sum(x["baseline"] for x in items);target=sum(x["target"] for x in items)
    liveitems=[x for x in items if x["live_available"]]
    current=(official or {}).get("current") if official is not None else sum(x.get("current",0) or 0 for x in items)
    male=(official or {}).get("male") if official is not None else sum(x.get("male",0) or 0 for x in items)
    female=(official or {}).get("female") if official is not None else sum(x.get("female",0) or 0 for x in items)
    progress=((current-base)*100/target) if target>0 else 0
    return dict(level=level,name=name,**where,school_count=len(items),baseline=base,target=target,expected=base+target,current=current,
        male=male,female=female,remaining=base+target-current,
        progress_pct=round(progress,2),live_schools=len(liveitems),failed_schools=len(items)-len(liveitems))

def main():
    with MASTER.open(newline="",encoding="utf-8-sig") as f:master=list(csv.DictReader(f))
    target={}
    for r in master:
        e=emis(r.get("EMIS"))
        if e:
            mb=number(r.get("Male Baseline")); fb=number(r.get("Female Baseline"))
            mt=number(r.get("2026 Male target ")); ft=number(r.get("2026 female target"))
            b=number(r.get("Total Baseline")) or mb+fb
            t=number(r.get("Total Target 2026")) or mt+ft
            target[e]=(b,t,mb,mt,fb,ft)
    ds=opts("/user/get_districts")
    if not ds:raise RuntimeError("SIS district list empty; no data published")
    tasks=[]
    for did,dn in ds:
        for tid,tn in opts("/user/get_tehsils",{"district":did,"selectedTehsil":"false","all":"All"}):
            for mid,mn in opts("/user/get_markazes",{"tehsil":tid,"selectedMarkaz":"false","all":"All"}):
                tasks.append((did,dn,tid,tn,mid,mn))
    def get_inventory_for_markaz(task):
        did,dn,tid,tn,mid,mn=task;rows=[]
        for sid,label in opts("/user/get_schools",{"markaz":mid,"selectedSchool":"false","all":"All"}):
            e="";name=label
            if " - " in label:
                part,rest=label.split(" - ",1)
                if emis(part):e=emis(part);name=rest.strip()
            b,t,mb,mt,fb,ft=target.get(e,(0,0,0,0,0,0))
            rows.append(dict(did=did,tid=tid,mid=mid,sid=sid,district=dn.strip().upper(),tehsil=tn.strip().upper(),
                markaz=mn.strip(),wing=wing_of(mn,name),emis=e,school=name,baseline=b,target=t,
                male_baseline=mb,male_target=mt,female_baseline=fb,female_target=ft))
        return rows
    inventory_rows=[];errors=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        fs={pool.submit(get_inventory_for_markaz,t):t for t in tasks}
        for f in as_completed(fs):
            try:inventory_rows.extend(f.result())
            except Exception as e:errors.append(str(fs[f][5])+": "+str(e))
    if errors:raise RuntimeError("Incomplete SIS school inventory; no publish: "+"; ".join(errors[:5]))
    uniq={ (s["did"],s["tid"],s["mid"],s["sid"]):s for s in inventory_rows };inventory_rows=list(uniq.values())
    print("Schools discovered:",len(inventory_rows),flush=True)
    done=[]
    with ThreadPoolExecutor(max_workers=18) as pool:
        fs=[pool.submit(live,s) for s in inventory_rows]
        for i,f in enumerate(as_completed(fs),1):
            done.append(f.result())
            if i%1000==0:print("Enrollment fetched",i,"/",len(fs),flush=True)
    # Preserve the last known live counts when SIS temporarily fails for a school.
    previous={}
    if SCHOOLS.exists():
        try:
            old=json.loads(SCHOOLS.read_text(encoding="utf-8"))
            for p in old.get("schools",[]):
                key=emis(p.get("emis")) or "|".join(str(p.get(k) or "").strip().upper() for k in ("district","tehsil","markaz","school"))
                if key.strip("|"): previous[key]=p
        except Exception as e:
            print("WARNING: previous dataset could not be read for fallback:",e,flush=True)
    for s in done:
        if s["live_available"]: continue
        key=emis(s.get("emis")) or "|".join(str(s.get(k) or "").strip().upper() for k in ("district","tehsil","markaz","school"))
        old=previous.get(key)
        if old:
            s["current"]=number(old.get("current"))
            s["male"]=number(old.get("male"))
            s["female"]=number(old.get("female"))
            s["used_last_known"]=True
        else:
            s["used_last_known"]=False
    failures=sum(not s["live_available"] for s in done)
    outschools=[{k:s.get(k) for k in ("district","tehsil","markaz","wing","emis","school","baseline","target","current","male","female","live_available","fetch_error","used_last_known")} for s in done]
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
    # Query SIS aggregate totals for every district, tehsil and markaz.
    # Do not publish a partially reconciled hierarchy if any aggregate fails.
    agg_keys=set()
    for did,dn in ds:
        agg_keys.add((did,"0","0","District",dn.strip().upper()))
    for did,dn,tid,tn,mid,mn in tasks:
        agg_keys.add((did,tid,"0","Tehsil",tn.strip().upper()))
        agg_keys.add((did,tid,mid,"Markaz",mn.strip()))
    official={}
    with ThreadPoolExecutor(max_workers=18) as pool:
        fs={pool.submit(live_aggregate,did,tid,mid):(did,tid,mid,level,name) for did,tid,mid,level,name in agg_keys}
        for f in as_completed(fs):
            did,tid,mid,level,name=fs[f]
            official[(level,did,tid,mid)]=f.result()
    # Use SIS roll-ups for every hierarchy level; keep a separate accurate row for each gender wing.
    districts={}
    for did,dn in ds:
        val=official[("District",did,"0","0")]
        for k in ("current","male","female"): districts.setdefault(k,0); districts[k]+=val[k]
    report=[aggregate("Punjab","Punjab Total",done,official=districts)]
    for wing,gender_key in (("Male Elementary Wing","male"),("Female Elementary Wing","female")):
        items=[s for s in done if s["wing"]==wing]
        report.append(aggregate("Wing",wing,items,official={"current":districts[gender_key],"male":districts["male"] if gender_key=="male" else 0,"female":districts["female"] if gender_key=="female" else 0},wing=wing))
    secondary=[s for s in done if s["wing"]=="Secondary Wing"]
    if secondary: report.append(aggregate("Wing","Secondary Wing",secondary,wing="Secondary Wing"))
    for d in sorted({s["district"] for s in done}):
        dg=[s for s in done if s["district"]==d]
        did=next((x[0] for x in ds if x[1].strip().upper()==d),None)
        dval=official.get(("District",did,"0","0"))
        report.append(aggregate("District",d,dg,official=dval,district=d))
        for wing,gkey in (("Male Elementary Wing","male"),("Female Elementary Wing","female")):
            witems=[s for s in dg if s["wing"]==wing]
            if witems:
                report.append(aggregate("District",d,witems,official={"current":(dval or {}).get(gkey,0),"male":(dval or {}).get("male",0) if gkey=="male" else 0,"female":(dval or {}).get("female",0) if gkey=="female" else 0},district=d,wing=wing))
        for t in sorted({s["tehsil"] for s in dg}):
            tg=[s for s in dg if s["tehsil"]==t]
            tid=next((x[2] for x in tasks if x[1].strip().upper()==d and x[3].strip().upper()==t),None)
            tval=official.get(("Tehsil",did,tid,"0"))
            report.append(aggregate("Tehsil",t,tg,official=tval,district=d,tehsil=t))
            for wing,gkey in (("Male Elementary Wing","male"),("Female Elementary Wing","female")):
                witems=[s for s in tg if s["wing"]==wing]
                if witems:
                    report.append(aggregate("Tehsil",t,witems,official={"current":(tval or {}).get(gkey,0),"male":(tval or {}).get("male",0) if gkey=="male" else 0,"female":(tval or {}).get("female",0) if gkey=="female" else 0},district=d,tehsil=t,wing=wing))
            for m in sorted({s["markaz"] for s in tg}):
                mg=[s for s in tg if s["markaz"]==m]
                mid=next((x[4] for x in tasks if x[1].strip().upper()==d and x[3].strip().upper()==t and x[5].strip()==m),None)
                mval=official.get(("Markaz",did,tid,mid))
                report.append(aggregate("Markaz",m,mg,official=mval,district=d,tehsil=t,markaz=m,wing=mg[0]["wing"]))
                for wing,gkey in (("Male Elementary Wing","male"),("Female Elementary Wing","female")):
                    witems=[s for s in mg if s["wing"]==wing]
                    if witems:
                        report.append(aggregate("Markaz",m,witems,official={"current":(mval or {}).get(gkey,0),"male":(mval or {}).get("male",0) if gkey=="male" else 0,"female":(mval or {}).get("female",0) if gkey=="female" else 0},district=d,tehsil=t,markaz=m,wing=wing))
    stamp=datetime.now(ZoneInfo("Asia/Karachi")).isoformat()
    meta=dict(updated_at=stamp,district_count=len({s["district"] for s in done}),school_count=len(done),live_school_count=len(done)-failures,failed_school_count=failures,summary=report,
        options=dict(wings=sorted({s["wing"] for s in done}),districts=sorted({s["district"] for s in done}),tehsils=sorted({s["tehsil"] for s in done}),markazs=sorted({s["markaz"] for s in done})))
    SUMMARY.write_text(json.dumps(meta,separators=(",",":"),ensure_ascii=False),encoding="utf-8")
    SCHOOLS.write_text(json.dumps(dict(updated_at=stamp,schools=outschools),separators=(",",":"),ensure_ascii=False),encoding="utf-8")
    print("SUCCESS",len(done),"schools; failures",failures,"; verified SIS hierarchy aggregates",len(official),"; report rows",len(report),flush=True)

if __name__=="__main__":main()
