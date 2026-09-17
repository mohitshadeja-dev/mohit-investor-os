from pathlib import Path

p=Path('/app/app/main.py')
s=p.read_text(encoding='utf-8')
if 'from .weekly_wma_gann import run_weekly' not in s:
    s=s.replace('from .strategy_lab import run_lab','from .strategy_lab import run_lab\nfrom .weekly_wma_gann import run_weekly')

block='''

class WeeklyWmaGannRequest(BaseModel):
    symbol:str='NIFTY 50'
    instrument_token:int|None=None
    from_date:date
    to_date:date
    wma_factor:float=.382
    gann_step:float=.125
    target_points:float=100.0
    gap_near_target_points:float=30.0
    first_candle_distance_points:float=150.0
    primary_stop_mode:str='gann'
    primary_stop_points:float=100.0
    same_bar_policy:str='stop_first'

@app.post('/api/weekly/backtest')
def weekly_wma_gann_backtest(req:WeeklyWmaGannRequest):
    if req.from_date>=req.to_date:
        raise HTTPException(400,'From date must be before To date')
    try:
        inst={'symbol':req.symbol,'instrument_token':req.instrument_token} if req.instrument_token else resolve_symbol(req.symbol)
        df=fetch_minutes(int(inst['instrument_token']),req.from_date,req.to_date)
        if df.empty:
            raise RuntimeError('No 1-minute candles returned by Zerodha for this range')
        summary,trades,attempts=run_weekly(
            df,wma_factor=req.wma_factor,gann_step=req.gann_step,
            target_points=req.target_points,gap_near_target_points=req.gap_near_target_points,
            same_bar_policy=req.same_bar_policy,
            first_candle_distance_points=req.first_candle_distance_points,
            primary_stop_mode=req.primary_stop_mode,primary_stop_points=req.primary_stop_points)
        return {
            'source':'Zerodha Kite 1-minute historical data aggregated to completed 5-minute closes',
            'strategy':'Weekly WMA-Gann positional — primary trade only',
            'symbol':inst.get('symbol',req.symbol),
            'instrument_token':int(inst['instrument_token']),
            'candles':len(df),
            'summary':summary,
            'trades':trades,
            'attempts':attempts,
            'config':req.model_dump(),
            'rules':{
                'entry':'5-minute close only; Gann + Tuesday High/Low + previous-day Fib filter',
                'distance_filter':'Skip the day when first 5m High is 150+ above previous-day Low or first 5m Low is 150+ below previous-day High; resume next day',
                'target':'Editable primary target (default 100 points)',
                'stop':'Editable primary SL: opposite Gann or fixed points, evaluated on 5-minute close',
                'carry':'Positional across trading days/weeks until target or SL',
                'reverse':'Disabled — a primary SL closes the setup with no opposite trade',
                'overlap':'No new weekly setup while the primary position is open'
            }
        }
    except Exception as e:
        raise HTTPException(400,str(e))
'''

if "@app.post('/api/weekly/backtest')" not in s:
    s += block
p.write_text(s,encoding='utf-8')
print('Weekly positional primary-only API wired')
