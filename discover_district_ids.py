import re, requests, json, time
SIS="https://sis.pesrp.edu.pk"
s=requests.Session()
s.headers.update({"User-Agent":"Mozilla/5.0 District-Enrollment-ID-Discovery","Accept":"application/json, text/javascript, */*; q=0.01"})
r=s.get(SIS+"/dashboard/enrollment",timeout=45); r.raise_for_status()
patterns=[r'csrf_test_name["\']?\s*[:=]\s*["\']([^"\']+)["\']',r'name=["\']csrf_test_name["\']\s+value=["\']([^"\']+)["\']']
csrf=None
for p in patterns:
 m=re.findall(p,r.text,re.I)
 if m: csrf=m[-1]; break
if not csrf: raise RuntimeError("csrf not found")
out=[]
for i in range(1,61):
 try:
  q=s.post(SIS+"/dashboard_revamp/get_gender_summary_pie",data={"district":i,"tehsil":"","markaz":"","school":"","s_id_emis_code":"","csrf_test_name":csrf},headers={"X-Requested-With":"XMLHttpRequest","Referer":SIS+"/dashboard/enrollment"},timeout=30)
  d=q.json()
  out.append({"id":i,"total":int(str(d.get("total","0")).replace(",","")),"male":d.get("male_count"),"female":d.get("female_count")})
 except Exception as e: out.append({"id":i,"error":str(e)})
 time.sleep(.15)
print(json.dumps(out))
