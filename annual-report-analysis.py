from __future__ import annotations

import io
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from pypdf import PdfReader


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self._href=None; self._text=[]
    def handle_starttag(self, tag, attrs):
        if tag=='a':
            self._href=dict(attrs).get('href'); self._text=[]
    def handle_data(self, data):
        if self._href is not None:self._text.append(data)
    def handle_endtag(self, tag):
        if tag=='a' and self._href is not None:
            self.links.append((self._href,' '.join(self._text).strip()))
            self._href=None; self._text=[]


def _request(url,accept='text/html,application/pdf',limit=35_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 MohitResearchOS/2.0','Accept':accept})
    with urllib.request.urlopen(req,timeout=25) as response:
        ctype=response.headers.get('Content-Type','').lower(); data=response.read(limit+1)
    if len(data)>limit:raise ValueError('Annual report exceeds the 35 MB safety limit')
    return data,ctype


def _links(url):
    data,ctype=_request(url,limit=3_000_000)
    if 'html' not in ctype and b'<html' not in data[:500].lower():return []
    p=_Links();p.feed(data.decode('utf-8','replace'))
    return [(urllib.parse.urljoin(url,h),t) for h,t in p.links if h and not h.startswith(('#','mailto:','javascript:'))]


def discover_annual_report(website):
    """Find the latest report only on the company's official web domain."""
    if not website:return None
    if not website.startswith(('http://','https://')):website='https://'+website
    origin=urllib.parse.urlsplit(website).netloc.lower().removeprefix('www.')
    pages=[website]
    first=_links(website)
    for url,text in first:
        host=urllib.parse.urlsplit(url).netloc.lower().removeprefix('www.')
        hay=(text+' '+url).lower()
        if host==origin and any(k in hay for k in ('investor','financial','annual-report','annual_report')):
            pages.append(url)
    candidates=[]
    for page in list(dict.fromkeys(pages))[:8]:
        try:links=_links(page)
        except Exception:continue
        for url,text in links:
            host=urllib.parse.urlsplit(url).netloc.lower().removeprefix('www.')
            hay=(text+' '+url).lower()
            if host==origin and '.pdf' in url.lower() and 'annual' in hay and 'report' in hay:
                years=[int(y) for y in re.findall(r'20\d{2}',hay)]
                candidates.append((max(years or [0]),url,text))
    if not candidates:return None
    candidates.sort(key=lambda x:(x[0],x[1]),reverse=True)
    return candidates[0][1]


def _extract_pdf(url):
    data,ctype=_request(url,accept='application/pdf,*/*')
    if not data.startswith(b'%PDF') and 'pdf' not in ctype:raise ValueError('Discovered annual-report link is not a PDF')
    reader=PdfReader(io.BytesIO(data),strict=False)
    pages=[]
    for number,page in enumerate(reader.pages[:350],start=1):
        try:text=' '.join((page.extract_text() or '').split())
        except Exception:text=''
        if text:pages.append((number,text))
    if sum(len(t) for _,t in pages)<5000:raise ValueError('Annual report PDF has insufficient extractable text')
    return pages


def _evidence(pages,patterns):
    rx=re.compile('|'.join(patterns),re.I)
    for page,text in pages:
        m=rx.search(text)
        if m:
            lo=max(0,m.start()-150);hi=min(len(text),m.end()+260)
            return {'page':page,'excerpt':text[lo:hi]}
    return None


def _visibility_item(label,evidence,importance):
    return {'label':label,'disclosed':bool(evidence),'importance':importance,
            'page':evidence.get('page') if evidence else None,
            'evidence':evidence.get('excerpt') if evidence else 'Not found in the latest annual report.'}


def analyze_annual_report(url):
    pages=_extract_pdf(url); all_text=' '.join(t for _,t in pages)
    years=[int(y) for y in re.findall(r'(?:annual report|financial year|year ended)[^\n]{0,50}(20\d{2})',all_text,re.I)]
    year=str(max(years)) if years else 'Latest available'
    audit=_evidence(pages,[r'true and fair view',r'give a true and fair',r'aforesaid financial statements give'])
    qualified=_evidence(pages,[r'qualified opinion',r'adverse opinion',r'disclaimer of opinion',r'basis for qualified'])
    controls=_evidence(pages,[r'internal financial controls.{0,120}operating effectively',r'adequate internal financial controls'])
    rpt=_evidence(pages,[r'related party transactions?',r'related party disclosure'])
    allocation=_evidence(pages,[r'capital allocation',r'capital expenditure',r'capex',r'expansion programme',r'new facility'])
    order_book=_evidence(pages,[r'order book',r'order backlog',r'unexecuted order',r'orders? in hand'])
    contracts=_evidence(pages,[r'letter of intent',r'letter of award',r'awarded contract',r'purchase orders?',r'long[- ]term contract'])
    capacity=_evidence(pages,[r'capacity expansion',r'capacity utilisation',r'new facility',r'greenfield',r'brownfield'])
    capex=_evidence(pages,[r'capital expenditure',r'capex',r'investment programme'])
    outlook=_evidence(pages,[r'management outlook',r'business outlook',r'future outlook',r'growth outlook',r'demand outlook'])
    pipeline=_evidence(pages,[r'product pipeline',r'new product launch',r'commerciali[sz]ation',r'research and development pipeline'])
    exports=_evidence(pages,[r'export market',r'geographical expansion',r'international expansion',r'overseas market'])
    concentration=_evidence(pages,[r'customer concentration',r'(?:top|largest) (?:five|ten|5|10) customers',r'major customer.{0,80}%'])
    findings=[];scores={}
    if audit:
        governance=7.0+(1.0 if controls else 0.0)-(3.0 if qualified else 0.0)
        scores['Governance & forensics']=max(0,min(10,governance))
        findings.append({'area':'Governance & forensics','status':'WATCH' if qualified else 'POSITIVE','page':audit['page'],'evidence':audit['excerpt']})
    if rpt:
        findings.append({'area':'Related-party transactions','status':'VERIFY','page':rpt['page'],'evidence':rpt['excerpt']})
    if allocation:
        scores['Management & allocation']=5.0
        findings.append({'area':'Management & allocation','status':'VERIFY','page':allocation['page'],'evidence':allocation['excerpt']})
    visibility=[_visibility_item('Order book / backlog',order_book,'HIGH'),
                _visibility_item('Awarded contracts / purchase orders',contracts,'HIGH'),
                _visibility_item('Capacity expansion / utilisation',capacity,'HIGH'),
                _visibility_item('Committed capex',capex,'MEDIUM'),
                _visibility_item('Management demand outlook',outlook,'MEDIUM'),
                _visibility_item('Product pipeline / launches',pipeline,'MEDIUM'),
                _visibility_item('Export / geographic expansion',exports,'MEDIUM')]
    visibility_points=(2.0 if order_book else 0)+(1.0 if contracts else 0)+(1.0 if capacity else 0)+(.5 if capex else 0)+(.5 if any((outlook,pipeline,exports)) else 0)
    if visibility_points:
        scores['Capacity/order visibility']=min(5.0,visibility_points)
        best=order_book or contracts or capacity or capex or outlook or pipeline or exports
        findings.append({'area':'Orders & future visibility','status':'POSITIVE' if order_book else 'VERIFY','page':best['page'],'evidence':best['excerpt']})
    if concentration:
        scores['Customers & suppliers']=2.5
        findings.append({'area':'Customers & suppliers','status':'VERIFY','page':concentration['page'],'evidence':concentration['excerpt']})
    warnings=[]
    if qualified:warnings.append('Annual report contains qualified/adverse-opinion language requiring manual auditor-note review.')
    if not order_book:warnings.append('No quantified order book/backlog was found in the latest annual report.')
    if not concentration:warnings.append('Annual report did not yield reliable customer-concentration evidence; this dimension remains unscored.')
    return {'status':'AUTO_REVIEWED','year':year,'url':url,'pages_read':len(pages),'scores':scores,'findings':findings,
            'future_visibility':{'score':scores.get('Capacity/order visibility'),'max_score':5,'items':visibility,
                                 'summary':'Strong' if visibility_points>=4 else ('Moderate' if visibility_points>=2.5 else 'Limited')},
            'warnings':warnings,'message':'Full PDF text was scanned automatically. Page excerpts are evidence leads; verify the cited pages before investing.'}


def discover_and_analyze(website):
    try:
        url=discover_annual_report(website)
        if not url:return {'status':'NOT_FOUND','year':None,'url':None,'findings':[],'scores':{},'warnings':[],'future_visibility':None,
                           'message':'No official annual-report PDF was found automatically on the company website.'}
        return analyze_annual_report(url)
    except Exception as exc:
        return {'status':'ERROR','year':None,'url':None,'findings':[],'scores':{},'warnings':[],'future_visibility':None,
                'message':f'Annual-report scan could not complete: {exc}'}
