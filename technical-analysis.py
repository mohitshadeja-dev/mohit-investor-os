from __future__ import annotations

import math
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf


def _f(value):
    try:
        x=float(value);return x if math.isfinite(x) else None
    except Exception:return None


def _candidate_patterns(df):
    patterns=[]
    if len(df)>=45:
        w=df.tail(40);n=len(w);x=pd.Series(range(n),dtype='float64')
        hs=float(pd.Series(w['High'].to_numpy()).corr(x));ls=float(pd.Series(w['Low'].to_numpy()).corr(x))
        start=float((w.High.iloc[:5].max()-w.Low.iloc[:5].min()) or 1);end=float(w.High.iloc[-5:].max()-w.Low.iloc[-5:].min())
        if hs<-.25 and ls>.25 and end<start*.75:
            patterns.append({'name':'Symmetrical triangle','direction':'NEUTRAL','confidence':'MEDIUM','status':'CANDIDATE',
              'evidence':'Forty-session highs slope down while lows slope up and the trading range contracts.',
              'confirmation':'A daily close outside the triangle with volume at least 1.5× its 20-day average.',
              'invalidation':'A close through the opposite trend boundary.'})
    if len(df)>=100:
        w=df.tail(120);third=max(10,len(w)//3);left=w.High.iloc[:third].idxmax();bottom=w.Low.loc[left:].iloc[:third+15].idxmin();right=w.High.loc[bottom:].iloc[-third:].idxmax()
        lp,rp,b=float(w.loc[left,'High']),float(w.loc[right,'High']),float(w.loc[bottom,'Low'])
        rim=(lp+rp)/2;depth=(rim-b)/rim if rim else 0;rim_gap=abs(lp-rp)/rim if rim else 1
        handle=w.loc[right:];handle_dd=(rp-float(handle.Low.min()))/rp if len(handle) else 1
        if .10<=depth<=.45 and rim_gap<=.10 and handle_dd<=.15:
            close=float(w.Close.iloc[-1]);patterns.append({'name':'Cup and handle','direction':'BULLISH','confidence':'MEDIUM','status':'CONFIRMED' if close>max(lp,rp) else 'CANDIDATE',
              'evidence':f'Rounded-base depth approximately {depth*100:.1f}% with rim separation {rim_gap*100:.1f}% and handle pullback {handle_dd*100:.1f}%.',
              'confirmation':f'Daily close above ₹{max(lp,rp):.2f} with strong volume.','invalidation':f'Close below the handle low near ₹{float(handle.Low.min()):.2f}.'})
    if len(df)>=70:
        w=df.tail(100);h=w.High.to_numpy();peaks=[]
        for i in range(3,len(h)-3):
            if h[i]==max(h[i-3:i+4]):peaks.append(i)
        if len(peaks)>=3:
            a,b,c=peaks[-3:];pa,pb,pc=h[a],h[b],h[c]
            shoulders=(abs(pa-pc)/max(pa,pc))<=.10
            if shoulders and pb>max(pa,pc)*1.06:
                neckline=float(min(w.Low.iloc[a:b+1].min(),w.Low.iloc[b:c+1].min()))
                close=float(w.Close.iloc[-1]);patterns.append({'name':'Head and shoulders','direction':'BEARISH','confidence':'MEDIUM','status':'CONFIRMED' if close<neckline else 'CANDIDATE',
                  'evidence':'Three swing peaks detected; the middle peak is higher and shoulders are within 10%.',
                  'confirmation':f'Daily close below neckline near ₹{neckline:.2f} with expanding volume.','invalidation':f'Close above the right shoulder near ₹{pc:.2f}.'})
    return patterns


def analyze_technical(symbol):
    ticker=str(symbol or '').upper()
    if not ticker.endswith(('.NS','.BO')):ticker+='.NS'
    try:
        df=yf.download(ticker,period='18mo',interval='1d',auto_adjust=False,progress=False,threads=False)
        if isinstance(df.columns,pd.MultiIndex):df.columns=df.columns.get_level_values(0)
        df=df.dropna(subset=['Open','High','Low','Close']).copy()
        if len(df)<30:raise ValueError('fewer than 30 daily candles are available')
        for p in (20,50,200):df[f'MA{p}']=df.Close.rolling(p).mean()
        vol20=df.Volume.rolling(20).mean();last=df.iloc[-1];prior20=float(df.High.iloc[-21:-1].max()) if len(df)>21 else None
        volume_ratio=_f(last.Volume/vol20.iloc[-1]) if _f(vol20.iloc[-1]) else None
        breakout=bool(prior20 and float(last.Close)>prior20)
        volume_confirmed=bool(breakout and volume_ratio is not None and volume_ratio>=1.5)
        high52=float(df.High.tail(252).max());low52=float(df.Low.tail(252).min());close=float(last.Close)
        rows=[]
        for idx,row in df.tail(180).iterrows():
            rows.append({'date':idx.strftime('%Y-%m-%d'),'open':_f(row.Open),'high':_f(row.High),'low':_f(row.Low),'close':_f(row.Close),'volume':_f(row.Volume),'ma20':_f(row.MA20),'ma50':_f(row.MA50),'ma200':_f(row.MA200)})
        return {'status':'READY','symbol':ticker,'candles':rows,'updated_at':datetime.now(timezone.utc).isoformat(),
          'summary':{'close':close,'high_52w':high52,'low_52w':low52,'distance_from_52w_high_pct':round((close/high52-1)*100,2),
            'ma20':_f(last.MA20),'ma50':_f(last.MA50),'ma200':_f(last.MA200),'above_ma20':bool(_f(last.MA20) and close>last.MA20),'above_ma50':bool(_f(last.MA50) and close>last.MA50),'above_ma200':bool(_f(last.MA200) and close>last.MA200),
            'breakout_20d':breakout,'breakout_level':prior20,'volume_ratio_20d':round(volume_ratio,2) if volume_ratio is not None else None,'volume_confirmed':volume_confirmed,
            'breakout_message':('Confirmed 20-day breakout with higher volume.' if volume_confirmed else ('Price breakout detected, but volume is below the 1.5× confirmation rule.' if breakout else 'No confirmed 20-day breakout.'))},
          'patterns':_candidate_patterns(df),'disclaimer':'Pattern recognition is rules-based and can produce false positives. Confirm on the chart and define risk before trading.'}
    except Exception as exc:
        return {'status':'UNAVAILABLE','symbol':ticker,'candles':[],'summary':{},'patterns':[],'message':f'Technical data unavailable: {exc}','disclaimer':'No technical conclusion was generated.'}
