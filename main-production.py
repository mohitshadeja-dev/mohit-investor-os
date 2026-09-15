from __future__ import annotations
import os, sqlite3
from datetime import date, timedelta
from pathlib import Path
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv
load_dotenv()
from .store import set_secret, get_secret, delete_secret
from .kite_service import login_url, exchange_request_token, profile, fetch_minutes
from .strategy import run_backtest, _norm, _daily_hlc, _first_touch_5m, gann_levels

ROOT=Path(__file__).resolve().parent
DB=Path(os.getenv('APP_DB_PATH','./mohit_os.db'))
app=FastAPI(title='Mohit Investor OS — Gann Production API',version='2.0.0')
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')

class KiteSettings(BaseModel):
    api_key:str=Field(min_length=3)
    api_secret:str=Field(min_length=3)
class BacktestRequest(BaseModel):
    from_date:date
    to_date:date
    instrument_token:int=int(os.getenv('NIFTY_INSTRUMENT_TOKEN','256265'))
    wma_factor:float=.382
    target_points:float=100.0
    gann_step:float=.125
    reentry:bool=True
    max_trades_per_day:int|None=None
    same_bar_policy:str='stop_first'


def jconn():
    DB.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    c.execute('''CREATE TABLE IF NOT EXISTS journal(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      trade_key TEXT UNIQUE, trade_date TEXT, trade_no INTEGER, side TEXT,
      entry_time TEXT, entry REAL, stop REAL, target REAL,
      exit_time TEXT, exit REAL, reason TEXT, points REAL,
      status TEXT, notes TEXT DEFAULT '', updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    return c

def upsert_trade(t:dict):
    key=f"{t.get('date')}|{t.get('trade_no')}|{t.get('entry_time')}"
    with jconn() as c:
        c.execute('''INSERT INTO journal(trade_key,trade_date,trade_no,side,entry_time,entry,stop,target,exit_time,exit,reason,points,status)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(trade_key) DO UPDATE SET exit_time=excluded.exit_time,exit=excluded.exit,reason=excluded.reason,points=excluded.points,status=excluded.status,updated_at=CURRENT_TIMESTAMP''',(
        key,t.get('date'),t.get('trade_no'),t.get('side'),t.get('entry_time'),t.get('entry'),t.get('stop'),t.get('target'),t.get('exit_time'),t.get('exit'),t.get('reason'),t.get('points'),t.get('status','OPEN')))

def journal_rows():
    with jconn() as c:
        rows=[dict(r) for r in c.execute('SELECT * FROM journal ORDER BY trade_date DESC, trade_no DESC, id DESC').fetchall()]
    pts=[float(r['points'] or 0) for r in rows if r['status']=='CLOSED']
    wins=sum(p>0 for p in pts)
    return {'summary':{'trades':len(rows),'closed':len(pts),'win_rate':round(100*wins/len(pts),2) if pts else 0,'total_points':round(sum(pts),2)},'trades':rows}

@app.get('/')
def home(): return FileResponse(ROOT/'static'/'index.html')
@app.get('/health')
def health(): return {'ok':True,'service':'mohit-gann-prod-v2'}

@app.get('/api/kite/status')
def status():
    configured=bool(get_secret('kite_api_key') or os.getenv('KITE_API_KEY'))
    try:
        p=profile(); return {'configured':configured,'connected':True,'user_id':p.get('user_id'),'user_name':p.get('user_name')}
    except Exception as e: return {'configured':configured,'connected':False,'detail':str(e)}

@app.post('/api/kite/settings')
def save_settings(s:KiteSettings):
    try:
        set_secret('kite_api_key',s.api_key.strip()); set_secret('kite_api_secret',s.api_secret.strip()); delete_secret('kite_access_token')
        return {'saved':True}
    except Exception as e: raise HTTPException(400,f'Could not save Kite credentials: {e}')

@app.get('/api/kite/login-url')
def get_login_url():
    try:return {'url':login_url()}
    except Exception as e:raise HTTPException(400,str(e))
@app.get('/api/kite/callback')
def callback(request_token:str=Query(...)):
    try:exchange_request_token(request_token)
    except Exception as e:raise HTTPException(400,str(e))
    return RedirectResponse(url='/?connected=1')

@app.post('/api/backtest')
def backtest(req:BacktestRequest):
    if req.from_date>=req.to_date: raise HTTPException(400,'from_date must be before to_date')
    try:
        df=fetch_minutes(req.instrument_token,req.from_date,req.to_date)
        if df.empty: raise RuntimeError('No minute candles returned by Kite for that range/token')
        summary,trades=run_backtest(df,req.wma_factor,req.target_points,req.gann_step,req.reentry,req.max_trades_per_day,req.same_bar_policy)
        return {'source':'Zerodha Kite historical minute data','instrument_token':req.instrument_token,'from_date':str(req.from_date),'to_date':str(req.to_date),'candles':len(df),'summary':summary,'trades':trades}
    except Exception as e: raise HTTPException(400,str(e))


def scan_live(df:pd.DataFrame,target_points:float=100.0):
    d=_norm(df); d['session']=d.date.dt.date
    sessions=[(k,v.drop(columns='session').reset_index(drop=True)) for k,v in d.groupby('session',sort=True)]
    if len(sessions)<2: return {'state':'WAITING_DATA','message':'Need previous and current session data','trades':[]}
    session_date,day=sessions[-1]; _,prev=sessions[-2]
    ph,pl,pc=_daily_hlc(prev); wma=(ph-pl)*.382; resistance=pc+wma; support=pc-wma
    touch=_first_touch_5m(day,resistance,support)
    base={'date':str(session_date),'prev_high':round(ph,2),'prev_low':round(pl,2),'prev_close':round(pc,2),'wma':round(wma,2),'resistance':round(resistance,2),'support':round(support,2),'latest_close':round(float(day.iloc[-1].close),2),'latest_time':str(day.iloc[-1].date)}
    if touch is None: return {**base,'state':'WAITING_TOUCH','message':'Waiting for first 5-minute support/resistance touch','trades':[]}
    if touch.get('ambiguous'): return {**base,'state':'AMBIGUOUS_TOUCH','message':'Support and resistance touched in same 5-minute candle','trades':[]}
    buy,sell=gann_levels(touch['level'],.125); base.update({'first_touch':touch['type'],'touch_time':str(touch['time']),'reference_price':round(touch['level'],2),'buy_above':buy,'sell_below':sell})
    cursor=int(day.index[day.date>=touch['time']][0]); trades=[]; n=0
    while cursor<len(day):
        entry_i=None; side=None
        for i in range(cursor,len(day)):
            c=float(day.iloc[i].close)
            if c>=buy: entry_i=i; side='LONG'; break
            if c<=sell: entry_i=i; side='SHORT'; break
        if entry_i is None: break
        n+=1; eb=day.iloc[entry_i]; entry=float(eb.close); stop=sell if side=='LONG' else buy; target=entry+target_points if side=='LONG' else entry-target_points
        tr={'date':str(session_date),'trade_no':n,'side':side,'entry_time':str(eb.date),'entry':round(entry,2),'stop':round(stop,2),'target':round(target,2),'exit_time':None,'exit':None,'reason':None,'points':None,'status':'OPEN'}
        exit_i=None
        for j in range(entry_i+1,len(day)):
            b=day.iloc[j]; hit_sl=float(b.low)<=stop if side=='LONG' else float(b.high)>=stop; hit_t=float(b.high)>=target if side=='LONG' else float(b.low)<=target
            if hit_sl or hit_t:
                reason='SL' if hit_sl else 'TARGET'; px=stop if hit_sl else target; pts=px-entry if side=='LONG' else entry-px
                tr.update({'exit_time':str(b.date),'exit':round(px,2),'reason':reason,'points':round(pts,2),'status':'CLOSED'}); exit_i=j; break
        trades.append(tr); upsert_trade(tr)
        if exit_i is None: break
        cursor=exit_i+1
    if not trades: state='GANN_READY'; msg=f"Gann ready: BUY above {buy:.2f} / SELL below {sell:.2f}; waiting for 1-minute close"
    elif trades[-1]['status']=='OPEN': state='TRADE_OPEN'; msg=f"{trades[-1]['side']} open @ {trades[-1]['entry']:.2f}"
    else: state='REENTRY_READY'; msg=f"Last trade {trades[-1]['reason']}; waiting for next 1-minute close trigger"
    return {**base,'state':state,'message':msg,'trades':trades,'current_trade':trades[-1] if trades else None}

@app.get('/api/live/gann')
def live_gann(instrument_token:int=int(os.getenv('NIFTY_INSTRUMENT_TOKEN','256265')),target_points:float=100.0):
    try:
        today=date.today(); df=fetch_minutes(instrument_token,today-timedelta(days=10),today)
        if df.empty: raise RuntimeError('No minute candles returned by Kite')
        return scan_live(df,target_points)
    except Exception as e: raise HTTPException(400,str(e))

@app.get('/api/journal')
def journal(): return journal_rows()
