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
    # Use school gender/level codes as well as full names; Markaz labels alone
    # are not sufficient because elementary Markazes may contain secondary schools.
    m=str(markaz or "").strip().upper()
    s=str(school or "").strip().upper()
    # Normalize separators so SECONDARY-WING, SECONDARY WING and similar labels match.
    x=re.sub(r"[^A-Z0-9]+"," ",m+" "+s).strip()
    sn=re.sub(r"[^A-Z0-9]+"," ",s).strip()
    # Secondary level takes precedence over elementary classification.
    secondary_codes=("GGHS","GGHSS","GBHS","GBHSS","GHS","GHSS","HSS","HIGH SCHOOL","HIGHER SECONDARY","SECONDARY SCHOOL","SECONDARY WING")
    if any(re.search(r"\b"+re.escape(k)+r"\b",x) for k in secondary_codes):
        return "Secondary Wing"
    female_codes=("GGPS","GGES","GGHS","GGHSS","GIRLS","GIRL S","FEMALE","QAED F","W")
    male_codes=("GBPS","GBES","GBHS","GBHSS","BOYS","BOY S","MALE","QAED M","M")
    # Explicit gender codes / school names are used before generic Markaz labels.
    if any(re.search(r"\b"+re.escape(k)+r"\b",sn) for k in female_codes) or "FEMALE" in m or re.search(r"\bW\b",m):
        return "Female Elementary Wing" if not any(k in s for k in ("GGHS","GGHSS")) else "Secondary Wing"
    if any(re.search(r"\b"+re.escape(k)+r"\b",sn) for k in male_codes) or "MALE" in m or re.search(r"\bM\b",m):
        return "Male Elementary Wing" if not any(k in s for k in ("GBHS","GBHSS")) else "Secondary Wing"
    # In Punjab naming, unprefixed GPS is the boys/general primary-school code;
    # girls' primary schools are explicitly prefixed GGPS.
    if re.match(r"^GPS\b",sn):
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
    # Baseline and target are school-record sums for the selected administrative wing.
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
    # Infer missing wing only when all classifiable schools in the same Markaz agree.
    by_markaz={}
    for row in inventory_rows:
        by_markaz.setdefault((row["did"],row["tid"],row["mid"]),[]).append(row)
    inferred=0
    for group in by_markaz.values():
        known=[row["wing"] for row in group if row["wing"]!="Unclassified"]
        if not known: continue
        counts={w:known.count(w) for w in set(known)}
        dominant,n=max(counts.items(),key=lambda pair:pair[1])
        if len(counts)==1:
            for row in group:
                if row["wing"]=="Unclassified":
                    row["wing"]=dominant
                    row["wing_inferred_from_markaz"]=True
                    inferred+=1
    print("Schools discovered:",len(inventory_rows),"; wing inferred from consistent Markaz:",inferred,flush=True)
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
    unclassified=[s for s in done if s.get("wing")=="Unclassified"]
    by_district={}
    for s in unclassified: by_district[s["district"]]=by_district.get(s["district"],0)+1
    print("UNCLASSIFIED COUNT:",len(unclassified),flush=True)
    print("UNCLASSIFIED BY DISTRICT:",json.dumps(dict(sorted(by_district.items(),key=lambda kv:(-kv[1],kv[0]))),ensure_ascii=False),flush=True)
    print("UNCLASSIFIED SAMPLE:",json.dumps([{k:s.get(k) for k in ("district","tehsil","markaz","emis","school")} for s in unclassified[:100]],ensure_ascii=False),flush=True)
    with (ROOT/"data/unclassified_schools.csv").open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=["district","tehsil","markaz","emis","school","reason"])
        w.writeheader()
        for s in unclassified:
            w.writerow({"district":s["district"],"tehsil":s["tehsil"],"markaz":s["markaz"],"emis":s["emis"],"school":s["school"],"reason":"School and Markaz labels do not contain a reliable gender/level indicator"})
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
    # Assign each markaz to one administrative wing, then use SIS markaz roll-ups
    # so wing, tehsil, district and Punjab totals reconcile without double counting genders.
    markaz_groups={}
    for did,dn,tid,tn,mid,mn in tasks:
        items=[s for s in done if s["did"]==did and s["tid"]==tid and s["mid"]==mid]
        if not items: continue
        wing=wing_of(mn)
        if wing=="Unclassified":
            counts={}
            for s in items: counts[s["wing"]]=counts.get(s["wing"],0)+1
            wing=max(counts,key=counts.get) if counts else "Unclassified"
        markaz_groups[(did,tid,mid)]={"wing":wing,"items":items,"district":dn.strip().upper(),"tehsil":tn.strip().upper(),"markaz":mn.strip()}
    def official_sum(groups):
        total={"current":0,"male":0,"female":0}
        for key in groups:
            val=official.get(("Markaz",key[0],key[1],key[2]))
            if val is None: raise RuntimeError("Missing verified SIS markaz total: "+str(key))
            for k in total: total[k]+=val[k]
        return total
    district_total={"current":0,"male":0,"female":0}
    for did,dn in ds:
        dval=official.get(("District",did,"0","0"))
        if dval is None: raise RuntimeError("Missing verified SIS district total: "+str(dn))
        for k in district_total: district_total[k]+=dval[k]
    report=[aggregate("Punjab","Punjab Total",done,official=district_total)]
    all_wings=sorted({s["wing"] for s in done})
    for wing in all_wings:
        keys=[k for k,g in markaz_groups.items() if g["wing"]==wing]
        items=[s for k in keys for s in markaz_groups[k]["items"]]
        report.append(aggregate("Wing",wing,items,wing=wing))
    for d in sorted({s["district"] for s in done}):
        dg=[s for s in done if s["district"]==d]
        did=next((x[0] for x in ds if x[1].strip().upper()==d),None)
        dval=official.get(("District",did,"0","0"))
        report.append(aggregate("District",d,dg,official=dval,district=d))
        for wing in all_wings:
            keys=[k for k,g in markaz_groups.items() if k[0]==did and g["wing"]==wing]
            if keys:
                items=[s for k in keys for s in markaz_groups[k]["items"]]
                report.append(aggregate("District",d,items,district=d,wing=wing))
        for t in sorted({s["tehsil"] for s in dg}):
            tg=[s for s in dg if s["tehsil"]==t]
            tid=next((x[2] for x in tasks if x[1].strip().upper()==d and x[3].strip().upper()==t),None)
            tval=official.get(("Tehsil",did,tid,"0"))
            report.append(aggregate("Tehsil",t,tg,official=tval,district=d,tehsil=t))
            for wing in all_wings:
                keys=[k for k,g in markaz_groups.items() if k[0]==did and k[1]==tid and g["wing"]==wing]
                if keys:
                    items=[s for k in keys for s in markaz_groups[k]["items"]]
                    report.append(aggregate("Tehsil",t,items,district=d,tehsil=t,wing=wing))
            for m in sorted({s["markaz"] for s in tg}):
                mg=[s for s in tg if s["markaz"]==m]
                mid=next((x[4] for x in tasks if x[1].strip().upper()==d and x[3].strip().upper()==t and x[5].strip()==m),None)
                mval=official.get(("Markaz",did,tid,mid))
                mw=markaz_groups.get((did,tid,mid),{}).get("wing","Unclassified")
                report.append(aggregate("Markaz",m,mg,official=mval,district=d,tehsil=t,markaz=m,wing=mw))
    stamp=datetime.now(ZoneInfo("Asia/Karachi")).isoformat()
    meta=dict(updated_at=stamp,district_count=len({s["district"] for s in done}),school_count=len(done),live_school_count=len(done)-failures,failed_school_count=failures,summary=report,
        options=dict(wings=sorted({s["wing"] for s in done}),districts=sorted({s["district"] for s in done}),tehsils=sorted({s["tehsil"] for s in done}),markazs=sorted({s["markaz"] for s in done})))
    SUMMARY.write_text(json.dumps(meta,separators=(",",":"),ensure_ascii=False),encoding="utf-8")
    SCHOOLS.write_text(json.dumps(dict(updated_at=stamp,schools=outschools),separators=(",",":"),ensure_ascii=False),encoding="utf-8")
    print("SUCCESS",len(done),"schools; failures",failures,"; verified SIS hierarchy aggregates",len(official),"; report rows",len(report),flush=True)

if __name__=="__main__":main()
