from datetime import datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

spec=spec_from_file_location('live_execution',Path(__file__).with_name('live-execution.py'))
m=module_from_spec(spec);spec.loader.exec_module(m)

class FakeKite:
    VARIETY_REGULAR='regular';EXCHANGE_NFO='NFO';PRODUCT_MIS='MIS';ORDER_TYPE_MARKET='MARKET';VALIDITY_DAY='DAY'
    TRANSACTION_TYPE_BUY='BUY';TRANSACTION_TYPE_SELL='SELL'
    def __init__(self):self.placed=[]
    def ltp(self,keys):return {'NSE:NIFTY 50':{'last_price':24000}}
    def instruments(self,exchange):
        exp=(datetime.now(m.IST)+timedelta(days=3)).date()
        rows=[]
        for kind in ('CE','PE'):
            for strike in range(22500,25501,100):rows.append({'name':'NIFTY','instrument_type':kind,'expiry':exp,'strike':strike,'tradingsymbol':f'NIFTY{exp:%y%m%d}{strike}{kind}','lot_size':50})
        return rows
    def quote(self,keys):
        out={}
        for key in keys:
            strike=int(key[-7:-2]) if key.endswith(('CE','PE')) else 24000
            # Broad positive time value is sufficient for selector tests.
            out[key]={'last_price':max(8,250-abs(strike-24000)*.08)}
        return out
    def place_order(self,**kw):self.placed.append(kw);return f'O{len(self.placed)}'
    def order_history(self,order_id):return [{'order_id':order_id,'status':'COMPLETE','average_price':100}]
    def orders(self):return self.placed

def test_preview_never_places_and_hedge_goes_first():
    k=FakeKite();ticket=m.build_ticket(k,'SHORT',650,.70,.30)
    assert ticket['option_type']=='CE' and not k.placed
    placed=m.place_spread(k,ticket['ticket_id'],'SHORT',650,.70,.30,'PLACE')
    assert placed['status']=='ORDERS_SENT'
    assert k.placed[0]['transaction_type']=='BUY'
    assert k.placed[1]['transaction_type']=='SELL'

def test_long_uses_puts_and_quantity_must_match_lot():
    k=FakeKite();ticket=m.build_ticket(k,'LONG',650,.70,.30)
    assert ticket['option_type']=='PE'
    try:m.build_ticket(k,'LONG',1301,.70,.30)
    except m.LiveOrderError as e:assert 'multiple' in str(e)
    else:raise AssertionError('invalid quantity accepted')

def test_exit_buys_short_first_then_sells_hedge():
    k=FakeKite();ticket=m.build_ticket(k,'SHORT',650,.70,.30);spread=m.place_spread(k,ticket['ticket_id'],'SHORT',650,.70,.30,'PLACE')
    m.close_spread_record(k,spread)
    assert k.placed[2]['transaction_type']=='BUY'
    assert k.placed[2]['tradingsymbol']==spread['short_leg']['tradingsymbol']
    assert k.placed[3]['transaction_type']=='SELL'
    assert k.placed[3]['tradingsymbol']==spread['hedge_leg']['tradingsymbol']
    assert spread['status']=='CLOSED'
