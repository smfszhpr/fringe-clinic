import json,sqlite3,hashlib
from server import DB,evolve,ROOT
with sqlite3.connect(DB) as db:
    rows=db.execute('SELECT id,data FROM sessions ORDER BY rowid DESC').fetchall()
sid,raw=next((sid,raw) for sid,raw in rows if 'plan' in json.loads(raw) and json.loads(raw).get('patientId')=='leg-001')
session=json.loads(raw);plan=session['plan'];state=session['state']
start=state['minute'];operation=start+plan['preparationMinutes'];end=operation+plan['procedureMinutes']
action={'description':plan['action'],'timeline':{'start':start,'operationStart':operation,'operationEnd':end,'reportReady':start+plan['totalMinutes']},'timingAssumptions':plan.get('assumptions',[])}
print(json.dumps({'beforeMinute':start,'duration':plan['totalMinutes'],'events':len(session['events'])}),flush=True)
try:
    after,usage,clarification=evolve(state,action,plan['totalMinutes'])
    result={'success':True,'beforeMinute':start,'after':after,'usage':usage,'clarification':clarification}
except Exception as exc: result={'success':False,'error':str(exc)}
with sqlite3.connect(DB) as db:
    unchanged=db.execute('SELECT data FROM sessions WHERE id=?',(sid,)).fetchone()[0]==raw
result['originalSessionUnchanged']=unchanged
(ROOT/'diagnostics').mkdir(exist_ok=True)
(ROOT/'diagnostics'/'surgery-reproduction.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k not in ['after','usage']},ensure_ascii=True),flush=True)
