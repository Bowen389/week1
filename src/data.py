"""公开免费腾讯行情；接口失败重试；格式、日期、覆盖率失败关闭。"""
from __future__ import annotations
import json, re, time, urllib.request, urllib.parse
from datetime import date, timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor, as_completed
from .strategy import PREFIXES

class DataError(RuntimeError):pass

def fetch(url: str, encoding='utf-8') -> str:
    error=None
    for attempt in range(3):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://gu.qq.com/'})
            with urllib.request.urlopen(req,timeout=20) as response:
                return response.read().decode(encoding)
        except Exception as exc:
            error=exc;time.sleep(1+attempt)
    raise DataError(f'免费行情请求失败: {type(error).__name__}: {error}')

def rank_snapshot() -> dict:
    def page(offset):
        params=urllib.parse.urlencode(dict(_appver='11.17.0',board_code='aStock',
            sort_type='price',direct='down',offset=offset,count=200))
        payload=json.loads(fetch('https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList?'+params))
        if payload.get('code')!=0:raise DataError('腾讯股票列表状态异常')
        return payload['data']
    first=page(0);total=int(first['total'])
    if total<4000 or total>12000:raise DataError('股票列表总数异常')
    pages=[first]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for result in pool.map(page,range(200,total,200)):
            if int(result['total'])!=total:raise DataError('分页过程中股票总数变化')
            pages.append(result)
    items=[row for result in pages for row in result['rank_list']]
    rows={row['code']:row for row in items}
    if len(rows)!=total:raise DataError(f'股票列表不完整: {len(rows)}/{total}')
    selected={c:row for c,row in rows.items() if re.fullmatch(r'(sh|sz)\d{6}',c) and c[2:5] in PREFIXES}
    if len(selected)<2500:raise DataError('主板覆盖不足2500只')
    print(f'UNIVERSE_COMPLETE total={total} mainboard={len(selected)}',flush=True)
    return dict(sorted(selected.items()))

def universe() -> list[str]:
    return sorted(rank_snapshot())

def quotes(codes: list[str]) -> dict:
    def batch(items):
        result={}
        text=fetch('https://qt.gtimg.cn/q='+','.join(items),'gb18030')
        for code,body in re.findall(r'v_((?:sh|sz)\d{6})="([^"]*)";',text):
            if not body:continue
            f=body.split('~')
            if len(f)<49:raise DataError('腾讯快照字段不完整 '+code)
            result[code]=dict(code=code,name=f[1].strip(),price=f[3],previous_close=f[4],
                open=f[5],volume=f[6],timestamp=f[30],high=f[33],low=f[34],
                cap_yi=f[45],upper=f[47],lower=f[48])
            if len(f[30])!=14:raise DataError('时间戳长度错误 '+code)
        missing=set(items)-set(result)
        if missing:raise DataError(f'快照缺失{len(missing)}只，示例{sorted(missing)[:5]}')
        return result
    result={}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for block in pool.map(batch,[codes[i:i+70] for i in range(0,len(codes),70)]):
            result.update(block)
    return result

def history(code: str, count=400) -> list[dict]:
    param=f'{code},day,,,{count},'
    url='https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?'+urllib.parse.urlencode({'param':param})
    payload=json.loads(fetch(url))
    if payload.get('code')!=0:raise DataError('腾讯日线状态异常 '+code)
    data=payload.get('data',{}).get(code,{})
    if 'day' not in data:raise DataError('缺不复权日线 '+code)
    bars=[]
    for row in data['day']:
        if len(row)<6:raise DataError('日线字段不足 '+code)
        bar=dict(zip(['date','open','close','high','low','volume'],[str(x) for x in row[:6]]))
        date.fromisoformat(bar['date'])
        prices=[Decimal(bar[k]) for k in ('open','close','high','low')]
        if any(not x.is_finite() or x<=0 for x in prices) or Decimal(bar['volume'])<0:
            raise DataError('无效日线价格 '+code)
        o,c,h,l=prices
        if h<max(o,c) or l>min(o,c):raise DataError('日线OHLC关系错误 '+code)
        bars.append(bar)
    bars.sort(key=lambda x:x['date'])
    if len({x['date'] for x in bars})!=len(bars):raise DataError('日线重复日期 '+code)
    for i,b in enumerate(bars):
        b['previous_close']=bars[i-1]['close'] if i else None
    return bars

def histories(codes: list[str], workers=4) -> tuple[dict,dict]:
    result={};errors={}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending={pool.submit(history,c):c for c in codes}
        for i,future in enumerate(as_completed(pending),1):
            code=pending[future]
            try:result[code]=future.result()
            except Exception as exc:errors[code]=str(exc)
            if i%100==0:print(f'HISTORY_PROGRESS {i}/{len(codes)} errors={len(errors)}',flush=True)
    return result,errors

def calendar(today: date) -> list[date]:
    import exchange_calendars as xcals
    # XSHG节假日由exchange-calendars维护；禁止工作日硬推算替代交易日历。
    cal=xcals.get_calendar('XSHG',start=str(today-timedelta(days=750)),
                          end=str(date(today.year,12,31)))
    days=[x.date() for x in cal.sessions]
    if days[-1]<today+timedelta(days=30):
        raise DataError('交易日历不足未来30天，请更新exchange-calendars年度日历，禁止猜测节假日')
    return days
