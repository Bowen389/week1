"""东方财富免费历史估值日截面：全量分页核对后返回主板数据。"""
import json,urllib.parse
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from .data import fetch,DataError
from .strategy import PREFIXES

def day_snapshot(day):
    def page(number):
        params=dict(reportName='RPT_VALUEANALYSIS_DET',
            columns='SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,CLOSE_PRICE,TOTAL_MARKET_CAP,TOTAL_SHARES',
            filter=f'(TRADE_DATE=\'{day}\')',pageSize=5000,pageNumber=number,
            sortColumns='SECURITY_CODE',sortTypes='1',source='WEB',client='WEB')
        p=json.loads(fetch('https://datacenter-web.eastmoney.com/api/data/v1/get?'+urllib.parse.urlencode(params)))
        if not p.get('success') or not p.get('result'):raise DataError('历史日截面失败 '+str(day))
        return p['result']
    first=page(1);count=int(first['count']);pages=int(first['pages']);all_rows=list(first['data'])
    if count<4000 or pages>100:raise DataError('历史日截面覆盖异常')
    with ThreadPoolExecutor(max_workers=4) as pool:
        for p in pool.map(page,range(2,pages+1)):
            if int(p['count'])!=count:raise DataError('历史日截面分页总数不一致')
            all_rows.extend(p['data'])
    rows={r['SECURITY_CODE']:r for r in all_rows}
    if len(rows)!=count:raise DataError(f'历史日截面不完整 {len(rows)}/{count}')
    output={}
    for code,row in rows.items():
        if row['TRADE_DATE'][:10]!=str(day):raise DataError('历史截面日期错位')
        if code[:3] not in PREFIXES:continue
        normalized=('sh' if code.startswith('6') else 'sz')+code
        output[normalized]=row
    print(f'HISTORICAL_SNAPSHOT date={day} total={count} mainboard={len(output)}',flush=True)
    return output
