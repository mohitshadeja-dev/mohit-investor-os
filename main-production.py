from __future__ import annotations
import os, sqlite3, json, csv, io, urllib.request, threading, uuid, time as pytime
from datetime import date, datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo
from pathlib import Path
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv
load_dotenv()
from .store import set_secret, get_secret, delete_secret
from .kite_service import login_url, exchange_request_token, profile, fetch_minutes, client
from .strategy import run_backtest, _norm, _daily_hlc, _first_touch_5m, gann_levels
from .strategy_lab import run_lab
from .options_backtest import run_options_backtest
from .live_execution import LiveOrderError, build_ticket, place_spread, close_spread, close_spread_record, order_book
from .live_signal_7575 import scan_live_7575

ROOT=Path(__file__).resolve().parent
DB=Path(os.getenv('APP_DB_PATH','./mohit_os.db'))
IST=ZoneInfo('Asia/Kolkata')
app=FastAPI(title='Mohit Investor OS — Strategy Lab',version='3.0.0')
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')

class KiteSettings(BaseModel): api_key:str=Field(min_length=3); api_secret:str=Field(min_length=3)
class BacktestRequest(BaseModel):
    from_date:date; to_date:date; instrument_token:int=int(os.getenv('NIFTY_INSTRUMENT_TOKEN','256265')); wma_factor:float=.382; target_points:float=100.; gann_step:float=.125; reentry:bool=True; max_trades_per_day:int|None=None; same_bar_policy:str='stop_first'
class LabRequest(BaseModel):
    symbol:str='NIFTY 50'; instrument_token:int|None=None; from_date:date; to_date:date
    touch_interval:int=5; confirm_interval:int=1; wma_factor:float=.382; gann_step:float=.125
    target_mode:str='points'; target_value:float=100.; stop_mode:str='gann'; stop_value:float=0
    reentry:bool=True; max_trades_per_day:int|None=None; same_bar_policy:str='stop_first'; direction:str='both'; entry_mode:str='close'; touch_side:str='both'
    start_time:str='09:15'; end_time:str='15:30'; cost_points:float=0; slippage_points:float=0
class SaveTestRequest(BaseModel): name:str=Field(min_length=1,max_length=100); symbol:str; config:dict; summary:dict; trades:list[dict]=[]
class BatchRequest(BaseModel): symbols:list[str]; config:dict
class DhanSettings(BaseModel): client_id:str=Field(min_length=3); access_token:str=Field(min_length=20)
class OptionsBacktestRequest(BaseModel):
    from_date:date=date(2025,9,5); to_date:date=date(2026,9,15)
    quantity:int=Field(default=1300,gt=0)
class LiveTicketRequest(BaseModel):
    signal:str
    quantity:int=Field(default=650,gt=0)
    sell_delta:float=Field(default=.70,gt=.5,lt=1)
    buy_delta:float=Field(default=.30,gt=0,lt=.5)
class LivePlaceRequest(LiveTicketRequest):
    ticket_id:str=Field(min_length=8,max_length=80)
    confirmation:str
class LiveCloseRequest(BaseModel):
    spread_id:str=Field(min_length=8,max_length=80)
    confirmation:str

INSTRUMENT_CACHE={'ts':0,'rows':[]}; JOBS={}

def jconn():
    DB.parent.mkdir(parents=True,exist_ok=True); c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
    c.execute('''CREATE TABLE IF NOT EXISTS journal(id INTEGER PRIMARY KEY AUTOINCREMENT,trade_key TEXT UNIQUE,trade_date TEXT,trade_no INTEGER,side TEXT,entry_time TEXT,entry REAL,stop REAL,target REAL,exit_time TEXT,exit REAL,reason TEXT,points REAL,status TEXT,notes TEXT DEFAULT '',updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS saved_backtests(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,symbol TEXT,config_json TEXT,summary_json TEXT,trades_json TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS option_data_cache(cache_key TEXT PRIMARY KEY,payload_json TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS live_spreads(spread_id TEXT PRIMARY KEY,signal_key TEXT UNIQUE,payload_json TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    return c

def save_live_spread(spread):
    with jconn() as c:c.execute('''INSERT OR REPLACE INTO live_spreads(spread_id,signal_key,payload_json,status,updated_at) VALUES(?,?,?,?,CURRENT_TIMESTAMP)''',(spread['spread_id'],spread.get('signal_key'),json.dumps(spread),spread.get('status','UNKNOWN')))

def open_live_spreads():
    with jconn() as c:rows=c.execute("SELECT payload_json FROM live_spreads WHERE status IN ('ORDERS_SENT','SHORT_CLOSED')").fetchall()
    return [json.loads(r['payload_json']) for r in rows]

def _instruments():
    if INSTRUMENT_CACHE['rows'] and pytime.time()-INSTRUMENT_CACHE['ts']<21600:return INSTRUMENT_CACHE['rows']
    rows=client().instruments('NSE'); INSTRUMENT_CACHE.update({'ts':pytime.time(),'rows':rows}); return rows

def resolve_symbol(symbol:str):
    s=symbol.strip().upper()
    if s in ('NIFTY','NIFTY50','NIFTY 50'): return {'symbol':'NIFTY 50','instrument_token':int(os.getenv('NIFTY_INSTRUMENT_TOKEN','256265')),'name':'NIFTY 50','exchange':'NSE'}
    rows=_instruments(); exact=[r for r in rows if str(r.get('tradingsymbol','')).upper()==s]
    if not exact: exact=[r for r in rows if s in str(r.get('tradingsymbol','')).upper() or s in str(r.get('name','')).upper()]
    if not exact: raise RuntimeError(f'Instrument not found on NSE: {symbol}')
    r=exact[0]; return {'symbol':r.get('tradingsymbol'),'instrument_token':int(r['instrument_token']),'name':r.get('name') or r.get('tradingsymbol'),'exchange':r.get('exchange','NSE')}

def upsert_trade(t):
    key=f"{t.get('date')}|{t.get('trade_no')}|{t.get('entry_time')}"
    with jconn() as c:c.execute('''INSERT INTO journal(trade_key,trade_date,trade_no,side,entry_time,entry,stop,target,exit_time,exit,reason,points,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(trade_key) DO UPDATE SET exit_time=excluded.exit_time,exit=excluded.exit,reason=excluded.reason,points=excluded.points,status=excluded.status,updated_at=CURRENT_TIMESTAMP''',(key,t.get('date'),t.get('trade_no'),t.get('side'),t.get('entry_time'),t.get('entry'),t.get('stop'),t.get('target'),t.get('exit_time'),t.get('exit'),t.get('reason'),t.get('points'),t.get('status','OPEN')))

def journal_rows():
    with jconn() as c: rows=[dict(r) for r in c.execute('SELECT * FROM journal ORDER BY trade_date DESC,trade_no DESC,id DESC').fetchall()]
    pts=[float(r['points'] or 0) for r in rows if r['status']=='CLOSED']; wins=sum(p>0 for p in pts)
    return {'summary':{'trades':len(rows),'closed':len(pts),'win_rate':round(100*wins/len(pts),2) if pts else 0,'total_points':round(sum(pts),2)},'trades':rows}

@app.get('/')
def home(): return FileResponse(ROOT/'static'/'index.html')
@app.get('/health')
def health(): return {'ok':True,'service':'mohit-strategy-lab-v3'}
@app.get('/api/kite/status')
def status():
    configured=bool(get_secret('kite_api_key') or os.getenv('KITE_API_KEY'))
    try:p=profile(); return {'configured':configured,'connected':True,'user_id':p.get('user_id'),'user_name':p.get('user_name')}
    except Exception as e:return {'configured':configured,'connected':False,'detail':str(e)}

@app.get('/api/dhan/status')
def dhan_status():
    return {'configured':bool(get_secret('dhan_client_id') and get_secret('dhan_access_token')),
            'client_id':get_secret('dhan_client_id') or ''}

@app.post('/api/dhan/settings')
def dhan_settings(s:DhanSettings):
    try:
        set_secret('dhan_client_id',s.client_id.strip());set_secret('dhan_access_token',s.access_token.strip())
        return {'saved':True}
    except Exception as e:raise HTTPException(400,f'Could not save Dhan credentials: {e}')
@app.post('/api/kite/settings')
def save_settings(s:KiteSettings):
    try:set_secret('kite_api_key',s.api_key.strip());set_secret('kite_api_secret',s.api_secret.strip());delete_secret('kite_access_token');return {'saved':True}
    except Exception as e:raise HTTPException(400,f'Could not save Kite credentials: {e}')
@app.get('/api/kite/login-url')
def get_login_url():
    try:return {'url':login_url()}
    except Exception as e:raise HTTPException(400,str(e))
@app.get('/api/kite/callback')
def callback(request_token:str=Query(...)):
    try:exchange_request_token(request_token)
    except Exception as e:raise HTTPException(400,str(e))
    return RedirectResponse(url='/?connected=1#gann')

@app.get('/api/instruments/search')
def instrument_search(q:str=Query('',max_length=40)):
    try:
        if not q:return {'items':[resolve_symbol('NIFTY 50')]}
        s=q.upper(); items=[]
        if 'NIFTY' in s:items.append(resolve_symbol('NIFTY 50'))
        for r in _instruments():
            sym=str(r.get('tradingsymbol','')); name=str(r.get('name',''))
            if s in sym.upper() or s in name.upper():items.append({'symbol':sym,'instrument_token':int(r['instrument_token']),'name':name or sym,'exchange':r.get('exchange','NSE')})
            if len(items)>=20:break
        return {'items':items}
    except Exception as e:raise HTTPException(400,str(e))

@app.get('/api/universe/nifty100')
def nifty100():
    url='https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv'
    try:
        req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}); text=urllib.request.urlopen(req,timeout=12).read().decode('utf-8-sig')
        rows=list(csv.DictReader(io.StringIO(text))); syms=[r.get('Symbol','').strip() for r in rows if r.get('Symbol')]
        return {'source':'NSE Indices official constituent CSV','count':len(syms),'symbols':syms}
    except Exception as e:raise HTTPException(502,f'Could not load NIFTY 100 constituents: {e}')

@app.post('/api/backtest')
def backtest(req:BacktestRequest):
    if req.from_date>=req.to_date:raise HTTPException(400,'from_date must be before to_date')
    try:
        df=fetch_minutes(req.instrument_token,req.from_date,req.to_date); summary,trades=run_backtest(df,req.wma_factor,req.target_points,req.gann_step,req.reentry,req.max_trades_per_day,req.same_bar_policy)
        return {'source':'Zerodha Kite historical minute data','instrument_token':req.instrument_token,'candles':len(df),'summary':summary,'trades':trades}
    except Exception as e:raise HTTPException(400,str(e))

@app.post('/api/lab/backtest')
def lab_backtest(req:LabRequest):
    if req.from_date>=req.to_date:raise HTTPException(400,'From date must be before To date')
    try:
        inst={'symbol':req.symbol,'instrument_token':req.instrument_token} if req.instrument_token else resolve_symbol(req.symbol)
        df=fetch_minutes(int(inst['instrument_token']),req.from_date,req.to_date)
        if df.empty:raise RuntimeError('No 1-minute candles returned by Zerodha for this range')
        cfg=req.model_dump(); summary,trades,daily,monthly=run_lab(df,cfg)
        return {'source':'Zerodha Kite 1-minute historical data','symbol':inst.get('symbol',req.symbol),'instrument_token':int(inst['instrument_token']),'candles':len(df),'summary':summary,'trades':trades,'daily':daily,'monthly':monthly,'config':cfg}
    except Exception as e:raise HTTPException(400,str(e))

@app.post('/api/lab/save')
def save_test(req:SaveTestRequest):
    with jconn() as c:
        cur=c.execute('INSERT INTO saved_backtests(name,symbol,config_json,summary_json,trades_json) VALUES(?,?,?,?,?)',(req.name,req.symbol,json.dumps(req.config),json.dumps(req.summary),json.dumps(req.trades)))
        return {'saved':True,'id':cur.lastrowid}
@app.get('/api/lab/saved')
def saved_tests():
    with jconn() as c:rows=[dict(r) for r in c.execute('SELECT id,name,symbol,config_json,summary_json,created_at FROM saved_backtests ORDER BY id DESC').fetchall()]
    for r in rows:r['config']=json.loads(r.pop('config_json'));r['summary']=json.loads(r.pop('summary_json'))
    return {'items':rows}
@app.get('/api/lab/saved/{test_id}')
def saved_test(test_id:int):
    with jconn() as c:r=c.execute('SELECT * FROM saved_backtests WHERE id=?',(test_id,)).fetchone()
    if not r:raise HTTPException(404,'Saved test not found')
    x=dict(r);x['config']=json.loads(x.pop('config_json'));x['summary']=json.loads(x.pop('summary_json'));x['trades']=json.loads(x.pop('trades_json'));return x
@app.delete('/api/lab/saved/{test_id}')
def delete_test(test_id:int):
    with jconn() as c:c.execute('DELETE FROM saved_backtests WHERE id=?',(test_id,))
    return {'deleted':True}

def _batch_worker(jid,symbols,cfg):
    job=JOBS[jid]; results=[]
    for i,s in enumerate(symbols):
        try:
            inst=resolve_symbol(s); f=date.fromisoformat(cfg['from_date']); t=date.fromisoformat(cfg['to_date']); df=fetch_minutes(inst['instrument_token'],f,t); summ,_,_,_=run_lab(df,cfg)
            results.append({'symbol':inst['symbol'],'ok':True,**summ})
        except Exception as e:results.append({'symbol':s,'ok':False,'error':str(e)})
        job.update({'done':i+1,'results':results})
    results.sort(key=lambda x:x.get('total_points',-1e18) if x.get('ok') else -1e18,reverse=True);job.update({'status':'complete','results':results,'finished_at':pytime.time()})
@app.post('/api/lab/batch')
def start_batch(req:BatchRequest):
    if not req.symbols:raise HTTPException(400,'No symbols selected')
    jid=uuid.uuid4().hex[:10];JOBS[jid]={'id':jid,'status':'running','total':len(req.symbols),'done':0,'results':[],'started_at':pytime.time()}
    threading.Thread(target=_batch_worker,args=(jid,req.symbols,req.config),daemon=True).start();return JOBS[jid]
@app.get('/api/lab/batch/{job_id}')
def batch_status(job_id:str):
    if job_id not in JOBS:raise HTTPException(404,'Batch job not found')
    return JOBS[job_id]

def _option_cache_get(key):
    with jconn() as c:r=c.execute('SELECT payload_json FROM option_data_cache WHERE cache_key=?',(key,)).fetchone()
    return json.loads(r['payload_json']) if r else None

def _option_cache_set(key,payload):
    with jconn() as c:c.execute('INSERT OR REPLACE INTO option_data_cache(cache_key,payload_json) VALUES(?,?)',(key,json.dumps(payload)))

@app.get('/api/options/stockmock-worklist.csv')
def stockmock_worklist(from_date:date=date(2025,9,5),to_date:date=date(2026,9,15)):
    """Export the locked 7,575.6 signal ledger as a manual StockMock simulator worklist."""
    if from_date>=to_date:raise HTTPException(400,'From date must be before To date')
    try:
        df=fetch_minutes(int(os.getenv('NIFTY_INSTRUMENT_TOKEN','256265')),from_date,to_date)
        cfg={'symbol':'NIFTY 50','from_date':str(from_date),'to_date':str(to_date),'touch_interval':5,'confirm_interval':1,
             'wma_factor':.382,'gann_step':.125,'touch_side':'both_recalc','entry_mode':'trigger','direction':'both',
             'same_bar_policy':'stop_first','target_mode':'points','target_value':150,'stop_mode':'points','stop_value':150,
             'reentry':False,'max_trades_per_day':3,'start_time':'09:15','end_time':'15:30','cost_points':0,'slippage_points':0}
        summary,trades,_,_=run_lab(df,cfg)
        if abs(float(summary.get('total_points',0))-7575.6)>.05 or int(summary.get('trades',0))!=277:
            raise RuntimeError(f"Locked baseline audit failed: {summary.get('trades')} trades / {summary.get('total_points')} points")
        output=io.StringIO();columns=['date','trade_no','signal','entry_time','exit_time','option_type','test_a_sell_delta','test_a_buy_delta','test_b_sell_delta','test_b_buy_delta','expiry','quantity_units','underlying_entry','underlying_exit','exit_reason','underlying_points','stockmock_action']
        writer=csv.DictWriter(output,fieldnames=columns);writer.writeheader()
        for t in trades:
            signal=t.get('side','');option_type='CALL' if signal=='SHORT' else 'PUT'
            entry_time=str(t.get('entry_time') or '').split(' ')[-1][:5];exit_time=str(t.get('exit_time') or '').split(' ')[-1][:5]
            writer.writerow({'date':t.get('date'),'trade_no':t.get('trade_no'),'signal':signal,'entry_time':entry_time,'exit_time':exit_time,
                'option_type':option_type,'test_a_sell_delta':'0.80','test_a_buy_delta':'0.40','test_b_sell_delta':'0.50','test_b_buy_delta':'0.20',
                'expiry':'NEAREST WEEKLY','quantity_units':1300,'underlying_entry':t.get('entry'),'underlying_exit':t.get('exit'),
                'exit_reason':t.get('reason'),'underlying_points':t.get('points'),
                'stockmock_action':f"{signal}: SELL {option_type} at chosen sell delta + BUY {option_type} at chosen hedge delta; square off both at {exit_time}"})
        data=('\ufeff'+output.getvalue()).encode('utf-8');headers={'Content-Disposition':'attachment; filename="stockmock_gann_worklist_7575.csv"','X-Baseline-Audit':'PASS'}
        return StreamingResponse(iter([data]),media_type='text/csv; charset=utf-8',headers=headers)
    except HTTPException:raise
    except Exception as e:raise HTTPException(400,str(e))

def _options_worker(jid,req):
    job=JOBS[jid]
    try:
        token=get_secret('dhan_access_token')
        if not token:raise RuntimeError('Connect Dhan first: Client ID and 24-hour access token are required')
        job.update({'stage':'signals','message':'Running the unchanged 7575 underlying engine'})
        df=fetch_minutes(int(os.getenv('NIFTY_INSTRUMENT_TOKEN','256265')),req.from_date,req.to_date)
        cfg={'symbol':'NIFTY 50','from_date':str(req.from_date),'to_date':str(req.to_date),'touch_interval':5,'confirm_interval':1,
             'wma_factor':.382,'gann_step':.125,'touch_side':'both_recalc','entry_mode':'trigger','direction':'both',
             'same_bar_policy':'stop_first','target_mode':'points','target_value':150,'stop_mode':'points','stop_value':150,
             'reentry':False,'max_trades_per_day':3,'start_time':'09:15','end_time':'15:30','cost_points':0,'slippage_points':0}
        signal_summary,signals,_,_=run_lab(df,cfg)
        job.update({'signal_summary':signal_summary,'signal_trades':len(signals),'stage':'options'})
        def progress(done,total,message):job.update({'done':done,'total':total,'message':message})
        pairs=[('80/40',.80,.40),('50/20',.50,.20)];comparisons=[];ledger=[]
        for pair_name,short_delta,hedge_delta in pairs:
            summary,rows=run_options_backtest(signals,token,req.quantity,short_delta,hedge_delta,_option_cache_get,_option_cache_set,progress)
            summary.update({'pair':pair_name,'short_delta':short_delta,'hedge_delta':hedge_delta})
            comparisons.append(summary)
            ledger.extend([{**row,'pair':pair_name} for row in rows])
        job.update({'status':'complete','stage':'complete','comparisons':comparisons,'trades':ledger,'message':'Both delta-pair backtests complete'})
    except Exception as e:job.update({'status':'error','stage':'error','error':str(e),'message':str(e)})

@app.post('/api/options/backtest')
def options_backtest(req:OptionsBacktestRequest):
    if req.from_date>=req.to_date:raise HTTPException(400,'From date must be before To date')
    if not get_secret('dhan_access_token'):raise HTTPException(400,'Dhan setup required for real expired weekly option prices')
    jid=uuid.uuid4().hex[:10];JOBS[jid]={'id':jid,'kind':'options','status':'running','stage':'queued','done':0,'total':42,'message':'Queued'}
    threading.Thread(target=_options_worker,args=(jid,req),daemon=True).start();return JOBS[jid]

@app.get('/api/options/backtest/{job_id}')
def options_backtest_status(job_id:str):
    job=JOBS.get(job_id)
    if not job or job.get('kind')!='options':raise HTTPException(404,'Options backtest job not found')
    return job

# Live alert + journal engine retained for the locked default strategy.
def scan_live(df:pd.DataFrame,target_points:float=100.0):
    d=_norm(df);d['session']=d.date.dt.date;sessions=[(k,v.drop(columns='session').reset_index(drop=True)) for k,v in d.groupby('session',sort=True)]
    if len(sessions)<2:return {'state':'WAITING_DATA','message':'Need previous and current session data','trades':[]}
    session_date,day=sessions[-1];_,prev=sessions[-2];ph,pl,pc=_daily_hlc(prev);wma=(ph-pl)*.382;resistance=pc+wma;support=pc-wma;touch=_first_touch_5m(day,resistance,support)
    base={'date':str(session_date),'prev_high':round(ph,2),'prev_low':round(pl,2),'prev_close':round(pc,2),'wma':round(wma,2),'resistance':round(resistance,2),'support':round(support,2),'latest_close':round(float(day.iloc[-1].close),2),'latest_time':str(day.iloc[-1].date)}
    if touch is None:return {**base,'state':'WAITING_TOUCH','message':'Waiting for first 5-minute support/resistance touch','trades':[]}
    if touch.get('ambiguous'):return {**base,'state':'AMBIGUOUS_TOUCH','message':'Support and resistance touched in same 5-minute candle','trades':[]}
    buy,sell=gann_levels(touch['level'],.125);base.update({'first_touch':touch['type'],'touch_time':str(touch['time']),'reference_price':round(touch['level'],2),'buy_above':buy,'sell_below':sell});cursor=int(day.index[day.date>=touch['time']][0]);trades=[];n=0
    while cursor<len(day):
        entry_i=None;side=None
        for i in range(cursor,len(day)):
            c=float(day.iloc[i].close)
            if c>=buy:entry_i=i;side='LONG';break
            if c<=sell:entry_i=i;side='SHORT';break
        if entry_i is None:break
        n+=1;eb=day.iloc[entry_i];entry=float(eb.close);stop=sell if side=='LONG' else buy;target=entry+target_points if side=='LONG' else entry-target_points;tr={'date':str(session_date),'trade_no':n,'side':side,'entry_time':str(eb.date),'entry':round(entry,2),'stop':round(stop,2),'target':round(target,2),'exit_time':None,'exit':None,'reason':None,'points':None,'status':'OPEN'};exit_i=None
        for j in range(entry_i+1,len(day)):
            b=day.iloc[j];hsl=float(b.low)<=stop if side=='LONG' else float(b.high)>=stop;ht=float(b.high)>=target if side=='LONG' else float(b.low)<=target
            if hsl or ht:
                reason='SL' if hsl else 'TARGET';px=stop if hsl else target;pts=px-entry if side=='LONG' else entry-px;tr.update({'exit_time':str(b.date),'exit':round(px,2),'reason':reason,'points':round(pts,2),'status':'CLOSED'});exit_i=j;break
        trades.append(tr);upsert_trade(tr)
        if exit_i is None:break
        cursor=exit_i+1
    state='GANN_READY' if not trades else ('TRADE_OPEN' if trades[-1]['status']=='OPEN' else 'REENTRY_READY');return {**base,'state':state,'message':'Live Gann engine updated','trades':trades,'current_trade':trades[-1] if trades else None}
@app.get('/api/live/gann')
def live_gann(instrument_token:int=int(os.getenv('NIFTY_INSTRUMENT_TOKEN','256265')),target_points:float=100.0):
    try:
        now=datetime.now(IST);today=now.date();df=fetch_minutes(instrument_token,today-timedelta(days=10),today);state=scan_live_7575(df)
        if state.get('date')!=str(today):return {**state,'state':'MARKET_CLOSED','message':'No current-session candle is available','current_trade':None}
        if now.weekday()>4 or not (dtime(9,15)<=now.time()<=dtime(15,30)):
            return {**state,'state':'MARKET_CLOSED','message':'Live execution is available only during NSE market hours','current_trade':None}
        return state
    except Exception as e:raise HTTPException(400,str(e))
@app.get('/api/journal')
def journal():return journal_rows()

@app.post('/api/live/options/ticket')
def live_options_ticket(req:LiveTicketRequest):
    """Create a read-only manual order ticket. This endpoint never places an order."""
    try:
        now=datetime.now(IST);today=now.date()
        if now.weekday()>4 or not (dtime(9,15)<=now.time()<=dtime(15,25)):
            raise LiveOrderError('Order tickets are available only from 09:15 to 15:25 IST')
        df=fetch_minutes(int(os.getenv('NIFTY_INSTRUMENT_TOKEN','256265')),today-timedelta(days=10),today)
        state=scan_live_7575(df);trade=state.get('current_trade')
        if state.get('date')!=str(today) or not trade or trade.get('status')!='OPEN':
            raise LiveOrderError('There is no confirmed open Gann signal for the current session')
        if trade.get('side')!=req.signal.strip().upper():
            raise LiveOrderError('The requested side does not match the current confirmed Gann signal')
        ticket=build_ticket(client(),req.signal,650,req.sell_delta,req.buy_delta)
        ticket.update({'signal_key':f"{trade.get('date')}|{trade.get('trade_no')}|{trade.get('entry_time')}",'underlying_entry':trade.get('entry'),'underlying_stop':trade.get('stop'),'underlying_target':trade.get('target'),'underlying_trade_no':trade.get('trade_no'),'underlying_date':trade.get('date')})
        return ticket
    except LiveOrderError as e:raise HTTPException(400,str(e))
    except Exception as e:raise HTTPException(400,f'Could not prepare option spread: {e}')

@app.post('/api/live/options/place')
def live_options_place(req:LivePlaceRequest):
    """Place the exact previewed ticket only after an explicit UI confirmation."""
    try:
        spread=place_spread(client(),req.ticket_id,req.signal,req.quantity,req.sell_delta,req.buy_delta,req.confirmation);save_live_spread(spread);return spread
    except LiveOrderError as e:raise HTTPException(400,str(e))
    except Exception as e:raise HTTPException(400,f'Order placement failed: {e}')

@app.post('/api/live/options/close')
def live_options_close(req:LiveCloseRequest):
    try:return close_spread(client(),req.spread_id,req.confirmation)
    except LiveOrderError as e:raise HTTPException(400,str(e))
    except Exception as e:raise HTTPException(400,f'Exit failed: {e}')

@app.get('/api/live/options/orders')
def live_options_orders():
    try:return order_book(client())
    except Exception as e:raise HTTPException(400,str(e))

def _auto_exit_worker():
    """Watch NIFTY LTP each second and close persisted spreads at their 150-point target or SL."""
    while True:
        try:
            now=datetime.now(IST)
            if now.weekday()<5 and dtime(9,15)<=now.time()<=dtime(15,30):
                spreads=open_live_spreads()
                if spreads:
                    kite=client();spot=float(kite.ltp(['NSE:NIFTY 50'])['NSE:NIFTY 50']['last_price'])
                    for spread in spreads:
                        if spread.get('underlying_date')!=str(now.date()):continue
                        side=spread.get('signal');target=float(spread['underlying_target']);stop=float(spread['underlying_stop']);reason=spread.get('auto_exit_reason') if spread.get('status')=='SHORT_CLOSED' else None
                        if reason is None and side=='LONG':reason='TARGET' if spot>=target else ('SL' if spot<=stop else None)
                        elif reason is None and side=='SHORT':reason='TARGET' if spot<=target else ('SL' if spot>=stop else None)
                        if reason:
                            spread.update({'auto_exit_reason':reason,'underlying_exit':spot,'underlying_exit_time':now.isoformat()})
                            try:save_live_spread(close_spread_record(kite,spread))
                            except Exception as e:
                                spread['last_exit_error']=str(e);save_live_spread(spread)
        except Exception:pass
        pytime.sleep(1)

@app.on_event('startup')
def start_live_exit_monitor():
    threading.Thread(target=_auto_exit_worker,daemon=True,name='mio-live-auto-exit').start()
