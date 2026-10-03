import re,requests,json,time
S="https://sis.pesrp.edu.pk"; page=S+"/dashboard/enrollment"; ep=S+"/dashboard_revamp/get_gender_summary_pie"
known=[221439,352948,304604,220367,98451,165230,280523,728586,295735,294255,128441,321361,134195,331032,362224,141844,122742,599912,265847,149594,182166,219139,335168,38953,214936,163151,189369,318756,197642,528508,182406,325111,323111,399272,291371,349050,300903,57407,295290,81758]
s=requests.Session(); s.headers.update({"User-Agent":"Mozilla/5.0","Accept":"application/json, text/javascript, */*; q=0.01"})
r=s.get(page,timeout=45); r.raise_for_status()
ms=re.findall(r'csrf_test_name["\']?\s*[:=]\s*["\']([^"\']+)["\']',r.text,re.I) or re.findall(r'name=["\']csrf_test_name["\']\s+value=["\']([^"\']+)["\']',r.text,re.I)
csrf=ms[-1]
out=[]
for i in range(1,41):
 q=s.post(ep,data={"district":i,"tehsil":"","markaz":"","school":"","s_id_emis_code":"","csrf_test_name":csrf},headers={"X-Requested-With":"XMLHttpRequest","Referer":page},timeout=45)
 try:
  d=q.json()
  if d.get("csrf_test_name"): csrf=d["csrf_test_name"]
  out.append({"id":i,"total":int(str(d["total"]).replace(",",""))})
 except Exception as e:
  out.append({"id":i,"status":q.status_code,"error":q.text[:120]})
 time.sleep(.2)
print(json.dumps(out))
