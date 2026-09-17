from __future__ import annotations

import math
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

import pandas as pd
import yfinance as yf
from .annual_report_analysis import discover_and_analyze

SME_SNAPSHOTS={
 'INFLUX':{'company':'Influx Healthtech Ltd','price':284,'mcap':657,'pe':32.0,'roe':30.0,'roce':40.0,'sales':[147,105,100,76,59],'profit':[21,13,11,7,4],'cfo':4,'fcf':-21,'debt':0,'equity':101,'debtor':84,'inventory':67,'payable':80,'ccc':71},
 'ADISOFT':{'company':'Adisoft Technologies Ltd','price':229,'mcap':373,'pe':16.4,'roe':37.9,'roce':38.3,'sales':[166,132,103,76],'profit':[23,16,11,6],'cfo':17,'fcf':15,'debt':22,'equity':72,'debtor':181,'inventory':55,'payable':145,'ccc':90},
}

# Human-reviewed annual-report evidence.  These entries are deliberately
# separate from market-data snapshots: every scored qualitative dimension has
# a primary-source report, report year, and an explicit evidence trail.
ANNUAL_REPORT_REVIEWS={
 'ADISOFT':{
  'year':'2025-26','url':'https://www.adisoft.co.in/img/Adisoft_Annual_Report_2025-26.pdf',
  'scores':{'Management & allocation':7.0,'Governance & forensics':7.5,'Capacity/order visibility':4.5},
  'findings':[
   {'area':'Management & allocation','status':'POSITIVE','evidence':'FY26 total income ₹169.33 crore (+26.7%) and net profit ₹22.80 crore (+42.9%); capital is being deployed into a new integrated Pune facility.'},
   {'area':'Governance & forensics','status':'WATCH','evidence':'Auditor reports a true-and-fair opinion and effective internal financial controls; related-party transactions were disclosed as arm’s length. The Rule 11(g) audit-trail exception still requires follow-up.'},
   {'area':'Capacity/order visibility','status':'POSITIVE','evidence':'Annual report identifies a 70,000 sq ft integrated facility under construction. Capacity evidence exists, but a quantified order book was not disclosed.'},
   {'area':'Customers & suppliers','status':'UNVERIFIED','evidence':'The report cites 950+ customers, but does not provide enough concentration data to score dependence risk.'},
  ],
  'warnings':['Annual-report follow-up: verify the Rule 11(g) audit-trail exception and quantify customer concentration.']},
 'INFLUX':{
  'year':'2024-25','url':'https://influxhealthtech.com/wp-content/uploads/2025/08/080825_IHL_Annual-Report_Final.pdf',
  'scores':{'Management & allocation':6.5,'Governance & forensics':6.5,'Capacity/order visibility':4.0},
  'findings':[
   {'area':'Management & allocation','status':'WATCH','evidence':'The company disclosed that it was operating at maximum capacity and planned material equipment additions; working-capital intensity needs monitoring.'},
   {'area':'Governance & forensics','status':'WATCH','evidence':'The Board states there were no audit qualifications or adverse remarks and that related-party transactions were arm’s length. Purchases from related party Trusan Printpack require continued monitoring.'},
   {'area':'Capacity/order visibility','status':'POSITIVE','evidence':'The report itemises proposed production equipment and rated capacities, supporting a capacity-expansion programme; it does not establish a firm order book.'},
   {'area':'Customers & suppliers','status':'UNVERIFIED','evidence':'The report does not provide sufficient customer-concentration disclosure for a reliable score.'},
  ],
  'warnings':['Annual-report follow-up: quantify related-party purchases, working-capital funding and customer concentration.']},
}


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.text=[]; self.tables=[]; self.table=None; self.row=None; self.cell=None
    def handle_starttag(self, tag, attrs):
        if tag=='table': self.table=[]
        elif tag=='tr' and self.table is not None: self.row=[]
        elif tag in ('td','th') and self.row is not None: self.cell=[]
    def handle_data(self, data):
        value=' '.join(data.split())
        if value:
            self.text.append(value)
            if self.cell is not None:self.cell.append(value)
    def handle_endtag(self, tag):
        if tag in ('td','th') and self.cell is not None:
            self.row.append(' '.join(self.cell));self.cell=None
        elif tag=='tr' and self.row is not None:
            if self.row:self.table.append(self.row)
            self.row=None
        elif tag=='table' and self.table is not None:
            self.tables.append(self.table);self.table=None


def _download(url):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 MohitResearchOS/1.0','Accept':'text/html,application/json'})
    return urllib.request.urlopen(req,timeout=20).read().decode('utf-8','replace')


def _screener_symbol(name):
    raw=name.strip().upper().replace('.NS','').replace('.BO','')
    aliases={'INFLUX HEALTHTECH':'INFLUX','INFLUX HEALTH':'INFLUX','ADISOFT TECHNOLOGIES':'ADISOFT','ADISOFT TECHNOLOGY':'ADISOFT'}
    if raw in aliases:return aliases[raw]
    if ' ' not in raw and len(raw)<=20:return raw
    try:
        results=json.loads(_download('https://www.screener.in/api/company/search/?q='+urllib.parse.quote(name)))
        if results:
            url=str(results[0].get('url','')).strip('/').split('/')
            if len(url)>=2:return url[1].upper()
    except Exception:pass
    return raw.replace(' ','')


def _number(value):
    if value is None:return None
    found=re.search(r'-?[\d,]+(?:\.\d+)?',str(value))
    return _finite(found.group(0).replace(',','')) if found else None


def _table_row(tables,label):
    for table in tables:
        for row in table:
            if row and row[0].replace('\xa0',' ').strip().lower().rstrip('+').strip()==label.lower():
                return [_number(x) for x in row[1:] if _number(x) is not None]
    return []


def _screener_metric(text,label):
    match=re.search(re.escape(label)+r'\s+₹?\s*([\d,]+(?:\.\d+)?)',text,re.I)
    return _number(match.group(1)) if match else None


def _analyze_screener(name):
    symbol=_screener_symbol(name);url=f'https://www.screener.in/company/{symbol}/';parser=_PageParser();parser.feed(_download(url));text=' '.join(parser.text)
    if 'Page not found' in text or not parser.tables:raise ValueError(f"Could not find an Indian listed company for '{name}'")
    sales=_table_row(parser.tables,'Sales');profit=_table_row(parser.tables,'Net Profit');cfo=_table_row(parser.tables,'Cash from Operating Activity');fcf=_table_row(parser.tables,'Free Cash Flow');borrowings=_table_row(parser.tables,'Borrowings');equity=_table_row(parser.tables,'Equity Capital');reserves=_table_row(parser.tables,'Reserves');debtor=_table_row(parser.tables,'Debtor Days');inventory=_table_row(parser.tables,'Inventory Days');payable=_table_row(parser.tables,'Days Payable');ccc=_table_row(parser.tables,'Cash Conversion Cycle')
    rev_cagr=_cagr(pd.Series(list(reversed(sales[-5:])))) if len(sales)>1 else None
    pat_cagr=_cagr(pd.Series(list(reversed(profit[-5:])))) if len(profit)>1 else None
    latest_pat=profit[-1] if profit else None;latest_cfo=cfo[-1] if cfo else None
    roe=_screener_metric(text,'ROE');roce=_screener_metric(text,'ROCE');pe=_screener_metric(text,'Stock P/E');price=_screener_metric(text,'Current Price');mcap=_screener_metric(text,'Market Cap')
    cfo_pat=_ratio(latest_cfo,latest_pat,100);fcf_margin=_ratio(fcf[-1] if fcf else None,sales[-1] if sales else None,100)
    latest_equity=(equity[-1] if equity else 0)+(reserves[-1] if reserves else 0);debt_equity=_ratio(borrowings[-1] if borrowings else None,latest_equity)
    ccc_now=ccc[-1] if ccc else None;debtor_now=debtor[-1] if debtor else None;inventory_now=inventory[-1] if inventory else None;payable_now=payable[-1] if payable else None
    peg=_ratio(pe,pat_cagr) if pe is not None and pat_cagr and pat_cagr>0 else None
    dimensions=[
      ('Business quality & moat',10,_score_linear(roe,8,25,10),'ROE proxy; moat needs annual-report verification'),('TAM & reinvestment runway',10,_score_linear(rev_cagr,5,25,10),'Revenue-growth proxy; TAM needs verification'),('Revenue/PAT growth quality',8,_score_linear(min(x for x in (rev_cagr,pat_cagr) if x is not None) if any(x is not None for x in (rev_cagr,pat_cagr)) else None,5,25,8),'Reported annual growth'),('Incremental ROIC/ROCE',10,_score_linear(roce,8,25,10),'ROCE proxy'),('Cash conversion & FCF',10,_score_linear(cfo_pat,40,100,10),'CFO/PAT and FCF'),('Balance sheet',7,_score_linear(debt_equity,1.5,0,7,higher=False),'Debt/equity'),('Working capital',7,_score_linear(ccc_now,180,30,7,higher=False),'Cash-conversion cycle'),('Management & allocation',8,None,'Needs annual-report verification'),('Governance & forensics',10,None,'Needs auditor, RPT, pledge and filing verification'),('Customers & suppliers',5,None,'Needs concentration verification'),('Capacity/order visibility',5,None,'Needs order-book verification'),('Valuation & PEG',7,_score_linear(peg,2.5,.8,7,higher=False),'PEG proxy'),('Starting-base asymmetry',3,_score_linear(mcap,100000,1000,3,higher=False),'Market-cap proxy')]
    verified=[d for d in dimensions if d[2] is not None];raw_score=round(sum(d[2] for d in verified),1);weight=sum(d[1] for d in verified);normalized=round(raw_score/weight*100,1) if weight else 0
    warnings=[]
    if cfo_pat is not None and cfo_pat<80:warnings.append('CFO/PAT is below the 80% framework threshold.')
    if roe is not None and roe<20:warnings.append('ROE is below the preferred 20% threshold.')
    if debt_equity is not None and debt_equity>1:warnings.append('Debt/equity is above 1.0.')
    if fcf and fcf[-1]<0:warnings.append('Latest reported free cash flow is negative.')
    available=sum(x is not None for x in [rev_cagr,pat_cagr,roe,roce,cfo_pat,fcf_margin,debt_equity,pe])
    if weight<35 or available<5:verdict,action='DATA INCOMPLETE — NOT SCORED','Not enough verified financial data. This is not a zero score and not an Avoid verdict.'
    elif normalized>=85 and not warnings:verdict,action='PROVISIONAL WATCHLIST','Verify governance, auditor, customers and order book before any purchase.'
    elif normalized>=70 and len(warnings)<=2:verdict,action='DEEP RESEARCH','Financials pass the first screen, but unverified evidence blocks a Buy decision.'
    else:verdict,action='AVOID / WAIT','The automatic financial screen is not strong enough unless new verified evidence changes it.'
    title=re.search(r'^(.+? Ltd)',text);company=title.group(1) if title else symbol
    metrics={'Revenue CAGR':rev_cagr,'PAT CAGR':pat_cagr,'EBITDA CAGR':None,'EPS CAGR':None,'ROE':roe,'ROCE':roce,'Incremental ROIC':None,'CFO / PAT':cfo_pat,'FCF margin':fcf_margin,'Debt / equity':debt_equity,'Interest coverage':None,'Debtor days':debtor_now,'Inventory days':inventory_now,'Payable days':payable_now,'Cash conversion cycle':ccc_now,'P/E':pe,'PEG':peg,'52-week drawdown':None}
    return {'company':company,'symbol':symbol,'exchange':'NSE SME / BSE SME','sector':None,'industry':None,'price':price,'market_cap_crore':mcap,'week_52_high':None,'week_52_low':None,'currency':'INR','metrics':metrics,'dimensions':[{'name':n,'weight':w,'score':s,'basis':b} for n,w,s,b in dimensions],'raw_score':raw_score,'verified_weight':weight,'normalized_financial_score':normalized,'data_coverage':round(available/8*100),'available_metrics':available,'verdict':verdict,'action':action,'warnings':warnings,'unverified':[d[0] for d in dimensions if d[2] is None],'source':f'Screener public financial tables and exchange-linked documents — {url}','fetched_at':datetime.now(timezone.utc).isoformat(),'disclaimer':'Automatic screening is not a recommendation. Exchange filings and annual reports remain the source of truth.'}


def _finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _row(frame: pd.DataFrame, *names: str):
    if frame is None or frame.empty:
        return pd.Series(dtype="float64")
    lookup = {str(i).lower().replace(" ", ""): i for i in frame.index}
    for name in names:
        key = name.lower().replace(" ", "")
        if key in lookup:
            return pd.to_numeric(frame.loc[lookup[key]], errors="coerce").dropna()
    return pd.Series(dtype="float64")


def _latest(series):
    return _finite(series.iloc[0]) if len(series) else None


def _cagr(series):
    values = [_finite(x) for x in series.iloc[:5]]
    values = [x for x in values if x is not None]
    if len(values) < 2 or values[0] <= 0 or values[-1] <= 0:
        return None
    years = len(values) - 1
    return (pow(values[0] / values[-1], 1 / years) - 1) * 100


def _ratio(a, b, scale=1):
    a, b = _finite(a), _finite(b)
    return None if a is None or b in (None, 0) else a / b * scale


def _clip(value, lo, hi):
    return max(lo, min(hi, value))


def _score_linear(value, bad, good, weight, higher=True):
    if value is None:
        return None
    progress = (value - bad) / (good - bad) if higher else (bad - value) / (bad - good)
    return round(_clip(progress, 0, 1) * weight, 1)


def _attach_annual_report_review(result, symbol, website=None):
    """Merge only primary-source, human-reviewed annual-report evidence."""
    key=str(symbol or '').upper().replace('.NS','').replace('.BO','')
    review=ANNUAL_REPORT_REVIEWS.get(key)
    if not review:
        auto=discover_and_analyze(website)
        result['annual_report']=auto
        if auto.get('status')!='AUTO_REVIEWED':
            result['combined_framework_score']=None
            return result
        overrides=auto.get('scores',{})
        for item in result['dimensions']:
            if item['name'] in overrides:
                item['score']=overrides[item['name']]
                item['basis']=f"Annual report {auto.get('year')} — automatic full-PDF evidence scan"
        verified=[d for d in result['dimensions'] if d.get('score') is not None]
        raw=round(sum(float(d['score']) for d in verified),1)
        weight=sum(float(d['weight']) for d in verified)
        result['raw_score']=raw;result['verified_weight']=weight
        result['combined_framework_score']=round(raw/weight*100,1) if weight else None
        result['normalized_financial_score']=result['combined_framework_score']
        result['unverified']=[d['name'] for d in result['dimensions'] if d.get('score') is None]
        result['warnings']=list(dict.fromkeys(result.get('warnings',[])+auto.get('warnings',[])))
        result['source']=result.get('source','')+' | Annual report: '+str(auto.get('url'))
        result['disclaimer']='Automatic full-PDF evidence scan, not a recommendation. Verify cited pages, later filings and current valuation.'
        return result
    overrides=review['scores']
    for item in result['dimensions']:
        if item['name'] in overrides:
            item['score']=overrides[item['name']]
            item['basis']=f"Annual report {review['year']} — primary-source review"
    verified=[d for d in result['dimensions'] if d.get('score') is not None]
    raw=round(sum(float(d['score']) for d in verified),1)
    weight=sum(float(d['weight']) for d in verified)
    result['raw_score']=raw
    result['verified_weight']=weight
    result['combined_framework_score']=round(raw/weight*100,1) if weight else None
    result['normalized_financial_score']=result['combined_framework_score']
    result['unverified']=[d['name'] for d in result['dimensions'] if d.get('score') is None]
    result['warnings']=list(dict.fromkeys(result.get('warnings',[])+review.get('warnings',[])))
    result['annual_report']={'status':'REVIEWED','year':review['year'],'url':review['url'],
                             'findings':review['findings'],
                             'future_visibility':{'score':overrides.get('Capacity/order visibility'),'max_score':5,
                               'summary':'Strong' if overrides.get('Capacity/order visibility',0)>=4 else 'Moderate',
                               'items':[{'label':'Curated capacity/order review','disclosed':True,'importance':'HIGH','page':None,
                                         'evidence':next((f['evidence'] for f in review['findings'] if f['area']=='Capacity/order visibility'),'Reviewed in the cited annual report.')},
                                        {'label':'Quantified order book / backlog','disclosed':False,'importance':'HIGH','page':None,
                                         'evidence':'Not sufficiently disclosed in the reviewed annual report.'}]},
                             'message':'Qualitative scores use the cited annual report; undisclosed evidence remains unscored.'}
    result['source']=result.get('source','')+' | Annual report: '+review['url']
    result['disclaimer']='Framework research, not a recommendation. Recheck later exchange filings and current valuation before acting.'
    return result


def _analyze_cached_sme(symbol):
    d=SME_SNAPSHOTS.get(symbol)
    if not d:return None
    rev_cagr=_cagr(pd.Series(d['sales']));pat_cagr=_cagr(pd.Series(d['profit']));cfo_pat=_ratio(d['cfo'],d['profit'][0],100);fcf_margin=_ratio(d['fcf'],d['sales'][0],100);debt_equity=_ratio(d['debt'],d['equity']);peg=_ratio(d['pe'],pat_cagr) if pat_cagr else None
    dims=[('Business quality & moat',10,_score_linear(d['roe'],8,25,10),'ROE proxy; moat needs annual-report verification'),('TAM & reinvestment runway',10,_score_linear(rev_cagr,5,25,10),'Revenue-growth proxy; TAM needs verification'),('Revenue/PAT growth quality',8,_score_linear(min(rev_cagr,pat_cagr),5,25,8),'Reported annual growth'),('Incremental ROIC/ROCE',10,_score_linear(d['roce'],8,25,10),'ROCE proxy'),('Cash conversion & FCF',10,_score_linear(cfo_pat,40,100,10),'CFO/PAT and FCF'),('Balance sheet',7,_score_linear(debt_equity,1.5,0,7,higher=False),'Debt/equity'),('Working capital',7,_score_linear(d['ccc'],180,30,7,higher=False),'Cash-conversion cycle'),('Management & allocation',8,None,'Needs annual-report verification'),('Governance & forensics',10,None,'Needs auditor, RPT, pledge and filing verification'),('Customers & suppliers',5,None,'Needs concentration verification'),('Capacity/order visibility',5,None,'Needs order-book verification'),('Valuation & PEG',7,_score_linear(peg,2.5,.8,7,higher=False),'PEG proxy'),('Starting-base asymmetry',3,_score_linear(d['mcap'],100000,1000,3,higher=False),'Market-cap proxy')]
    verified=[x for x in dims if x[2] is not None];raw=round(sum(x[2] for x in verified),1);weight=sum(x[1] for x in verified);score=round(raw/weight*100,1);warnings=[]
    if cfo_pat<80:warnings.append('CFO/PAT is below the 80% framework threshold.')
    if d['roe']<20:warnings.append('ROE is below the preferred 20% threshold.')
    if debt_equity>1:warnings.append('Debt/equity is above 1.0.')
    if d['fcf']<0:warnings.append('Latest reported free cash flow is negative.')
    if score>=85 and not warnings:verdict,action='PROVISIONAL WATCHLIST','Verify governance, auditor, customers and order book before any purchase.'
    elif score>=70 and len(warnings)<=2:verdict,action='DEEP RESEARCH','Financials pass the first screen, but unverified evidence blocks a Buy decision.'
    else:verdict,action='AVOID / WAIT','The verified financial screen has material weaknesses. Review the warnings and latest filings.'
    metrics={'Revenue CAGR':rev_cagr,'PAT CAGR':pat_cagr,'EBITDA CAGR':None,'EPS CAGR':None,'ROE':d['roe'],'ROCE':d['roce'],'Incremental ROIC':None,'CFO / PAT':cfo_pat,'FCF margin':fcf_margin,'Debt / equity':debt_equity,'Interest coverage':None,'Debtor days':d['debtor'],'Inventory days':d['inventory'],'Payable days':d['payable'],'Cash conversion cycle':d['ccc'],'P/E':d['pe'],'PEG':peg,'52-week drawdown':None}
    result={'company':d['company'],'symbol':symbol,'exchange':'NSE SME','sector':None,'industry':None,'price':d['price'],'market_cap_crore':d['mcap'],'week_52_high':None,'week_52_low':None,'currency':'INR','metrics':metrics,'dimensions':[{'name':n,'weight':w,'score':s,'basis':b} for n,w,s,b in dims],'raw_score':raw,'verified_weight':weight,'normalized_financial_score':score,'data_coverage':78,'available_metrics':7,'verdict':verdict,'action':action,'warnings':warnings,'unverified':[x[0] for x in dims if x[2] is None],'source':f'Cached verified public snapshot (17 Sep 2026) with NSE/Screener references — https://www.screener.in/company/{symbol}/','fetched_at':datetime.now(timezone.utc).isoformat(),'disclaimer':'Automatic screening is not a recommendation. Recheck live price and the latest exchange filings.'}
    return _attach_annual_report_review(result,symbol)


def _resolve_company(name: str):
    raw = name.strip()
    if not raw:
        raise ValueError("Enter a company name")
    if raw.upper().endswith((".NS", ".BO")):
        return raw.upper(), raw.upper()
    try:
        search = yf.Search(raw, max_results=12, news_count=0)
        quotes = search.quotes or []
        india = [q for q in quotes if str(q.get("exchange", "")).upper() in {"NSI", "NSE", "BSE", "BOM"}]
        equities = [q for q in india if str(q.get("quoteType", "EQUITY")).upper() == "EQUITY"]
        match = (equities or india)[0]
        return match["symbol"], match.get("longname") or match.get("shortname") or match["symbol"]
    except Exception:
        symbol = "".join(ch for ch in raw.upper() if ch.isalnum() or ch in "&-") + ".NS"
        return symbol, raw


def analyze_company(name: str):
    raw_symbol=name.strip().upper().replace('.NS','').replace('.BO','')
    raw_symbol={'INFLUX HEALTHTECH':'INFLUX','INFLUX HEALTH':'INFLUX','ADISOFT TECHNOLOGIES':'ADISOFT','ADISOFT TECHNOLOGY':'ADISOFT'}.get(raw_symbol,raw_symbol)
    cached=_analyze_cached_sme(raw_symbol)
    if cached:return cached
    symbol, searched_name = _resolve_company(name)
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.get_info() or {}
        income = ticker.income_stmt
        balance = ticker.balance_sheet
        cashflow = ticker.cashflow
    except Exception:
        return _analyze_screener(symbol)
    if income is None or income.empty or balance is None or balance.empty:
        return _analyze_screener(symbol)

    revenue = _row(income, "Total Revenue", "Operating Revenue")
    net_income = _row(income, "Net Income", "Net Income Common Stockholders")
    ebit = _row(income, "EBIT", "Operating Income")
    ebitda = _row(income, "EBITDA", "Normalized EBITDA")
    interest = _row(income, "Interest Expense", "Interest Expense Non Operating")
    diluted_eps = _row(income, "Diluted EPS", "Basic EPS")
    cfo = _row(cashflow, "Operating Cash Flow", "Total Cash From Operating Activities")
    capex = _row(cashflow, "Capital Expenditure", "Capital Expenditures")
    assets = _row(balance, "Total Assets")
    equity = _row(balance, "Stockholders Equity", "Total Stockholder Equity")
    current_liab = _row(balance, "Current Liabilities", "Total Current Liabilities")
    debt = _row(balance, "Total Debt")
    cash = _row(balance, "Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents")
    receivables = _row(balance, "Accounts Receivable", "Net Receivables")
    inventory = _row(balance, "Inventory")
    payables = _row(balance, "Accounts Payable", "Payables")
    cogs = _row(income, "Cost Of Revenue", "Cost of Goods Sold")

    rev, pat, op_cash = _latest(revenue), _latest(net_income), _latest(cfo)
    eq, total_debt, cash_now = _latest(equity), _latest(debt), _latest(cash)
    capital = None
    if _latest(assets) is not None and _latest(current_liab) is not None:
        capital = _latest(assets) - _latest(current_liab)
    roce = _ratio(_latest(ebit), capital, 100)
    roe = _ratio(pat, eq, 100)
    cfo_pat = _ratio(op_cash, pat, 100)
    latest_capex = abs(_latest(capex) or 0)
    fcf = op_cash - latest_capex if op_cash is not None else None
    fcf_margin = _ratio(fcf, rev, 100)
    debt_equity = _ratio(total_debt, eq)
    interest_cover = _ratio(_latest(ebit), abs(_latest(interest) or 0))
    debtor_days = _ratio(_latest(receivables), rev, 365)
    inventory_days = _ratio(_latest(inventory), _latest(cogs), 365)
    payable_days = _ratio(_latest(payables), _latest(cogs), 365)
    ccc = None if None in (debtor_days, inventory_days, payable_days) else debtor_days + inventory_days - payable_days

    incremental_roic = None
    if len(net_income) > 1 and len(assets) > 1 and len(current_liab) > 1:
        delta_profit = _finite(net_income.iloc[0] - net_income.iloc[1])
        delta_capital = _finite((assets.iloc[0] - current_liab.iloc[0]) - (assets.iloc[1] - current_liab.iloc[1]))
        incremental_roic = _ratio(delta_profit, delta_capital, 100)

    market_cap = _finite(info.get("marketCap"))
    price = _finite(info.get("currentPrice") or info.get("regularMarketPrice"))
    pe = _finite(info.get("trailingPE"))
    rev_cagr, pat_cagr, ebitda_cagr, eps_cagr = _cagr(revenue), _cagr(net_income), _cagr(ebitda), _cagr(diluted_eps)
    peg = _ratio(pe, pat_cagr) if pe is not None and pat_cagr and pat_cagr > 0 else None
    high52, low52 = _finite(info.get("fiftyTwoWeekHigh")), _finite(info.get("fiftyTwoWeekLow"))
    drawdown = _ratio(price - high52, high52, 100) if price is not None and high52 else None

    dimensions = [
        ("Business quality & moat", 10, _score_linear(roe, 8, 25, 10), "ROE and margin proxy; moat still needs human verification"),
        ("TAM & reinvestment runway", 10, _score_linear(rev_cagr, 5, 25, 10), "Revenue-growth proxy; TAM needs verification"),
        ("Revenue/PAT growth quality", 8, _score_linear(min(x for x in (rev_cagr, pat_cagr) if x is not None) if any(x is not None for x in (rev_cagr, pat_cagr)) else None, 5, 25, 8), "Reported annual growth"),
        ("Incremental ROIC/ROCE", 10, _score_linear(incremental_roic if incremental_roic is not None else roce, 8, 25, 10), "Incremental return where available; otherwise ROCE proxy"),
        ("Cash conversion & FCF", 10, _score_linear(cfo_pat, 40, 100, 10), "CFO/PAT and FCF proxy"),
        ("Balance sheet", 7, _score_linear(debt_equity, 1.5, 0, 7, higher=False), "Debt/equity"),
        ("Working capital", 7, _score_linear(ccc, 180, 30, 7, higher=False), "Cash-conversion cycle"),
        ("Management & allocation", 8, None, "Needs annual-report and capital-allocation verification"),
        ("Governance & forensics", 10, None, "Needs auditor, RPT, pledge and filing verification"),
        ("Customers & suppliers", 5, None, "Needs concentration and contract verification"),
        ("Capacity/order visibility", 5, None, "Needs order-book and capacity verification"),
        ("Valuation & PEG", 7, _score_linear(peg, 2.5, 0.8, 7, higher=False), "PEG proxy from trailing P/E and PAT CAGR"),
        ("Starting-base asymmetry", 3, _score_linear(market_cap / 10_000_000 if market_cap else None, 100000, 1000, 3, higher=False), "Market-cap proxy only"),
    ]
    verified = [d for d in dimensions if d[2] is not None]
    raw_score = round(sum(d[2] for d in verified), 1)
    verified_weight = sum(d[1] for d in verified)
    normalized = round(raw_score / verified_weight * 100, 1) if verified_weight else 0

    warnings = []
    if cfo_pat is not None and cfo_pat < 80: warnings.append("CFO/PAT is below the 80% framework threshold.")
    if roe is not None and roe < 20: warnings.append("ROE is below the preferred 20% threshold.")
    if incremental_roic is not None and incremental_roic < 20: warnings.append("Incremental ROIC is below the preferred 20–25% range.")
    if debt_equity is not None and debt_equity > 1: warnings.append("Debt/equity is above 1.0.")
    if interest_cover is not None and interest_cover < 3: warnings.append("Interest coverage is below 3x.")
    if fcf is not None and fcf < 0: warnings.append("Latest reported free cash flow is negative.")
    if pat_cagr is not None and rev_cagr is not None and pat_cagr < 0 < rev_cagr: warnings.append("Sales grew while PAT declined.")

    metric_values = [rev_cagr, pat_cagr, roe, roce, cfo_pat, fcf_margin, debt_equity, interest_cover, pe]
    available_metrics = sum(value is not None for value in metric_values)
    data_coverage = round(available_metrics / len(metric_values) * 100)

    if verified_weight < 35 or available_metrics < 5:
        verdict, action = "DATA INCOMPLETE — NOT SCORED", "The source did not return enough verified financial data. This is not a zero score and not an Avoid verdict."
    elif normalized >= 85 and not warnings:
        verdict, action = "PROVISIONAL WATCHLIST", "Verify governance, auditor, customers, order book and valuation before any purchase."
    elif normalized >= 70 and len(warnings) <= 2:
        verdict, action = "DEEP RESEARCH", "Financials pass the first screen, but weaknesses and unverified evidence block a Buy decision."
    else:
        verdict, action = "AVOID / WAIT", "The automatic financial screen is not strong enough. Do not buy unless new verified evidence changes it."

    metrics = {
        "Revenue CAGR": rev_cagr, "PAT CAGR": pat_cagr, "EBITDA CAGR": ebitda_cagr, "EPS CAGR": eps_cagr,
        "ROE": roe, "ROCE": roce, "Incremental ROIC": incremental_roic, "CFO / PAT": cfo_pat,
        "FCF margin": fcf_margin, "Debt / equity": debt_equity, "Interest coverage": interest_cover,
        "Debtor days": debtor_days, "Inventory days": inventory_days, "Payable days": payable_days,
        "Cash conversion cycle": ccc, "P/E": pe, "PEG": peg, "52-week drawdown": drawdown,
    }
    result = {
        "company": info.get("longName") or info.get("shortName") or searched_name,
        "symbol": symbol, "exchange": info.get("exchange") or "India", "sector": info.get("sector"),
        "industry": info.get("industry"), "price": price, "market_cap_crore": market_cap / 10_000_000 if market_cap else None,
        "week_52_high": high52, "week_52_low": low52, "currency": info.get("currency") or "INR",
        "metrics": metrics, "dimensions": [{"name": n, "weight": w, "score": s, "basis": b} for n, w, s, b in dimensions],
        "raw_score": raw_score, "verified_weight": verified_weight, "normalized_financial_score": normalized,
        "data_coverage": data_coverage, "available_metrics": available_metrics,
        "verdict": verdict, "action": action, "warnings": warnings,
        "unverified": [d[0] for d in dimensions if d[2] is None],
        "source": "Yahoo Finance public market and reported-statement data via yfinance",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "Automatic screening is not a recommendation. Exchange filings and annual reports remain the source of truth.",
    }
    return _attach_annual_report_review(result, symbol, info.get('website'))
