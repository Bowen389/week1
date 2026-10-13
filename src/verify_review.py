"""用带历史日期的东方财富估值表补充只读复核，保留原始价格筛选证据。"""
import argparse,json,urllib.parse
from datetime import date
from decimal import Decimal
from .data import fetch,DataError
from .run import ROOT,atomic

def metadata(code,day):
    params=dict(reportName='RPT_VALUEANALYSIS_DET',
        columns='SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,CLOSE_PRICE,TOTAL_MARKET_CAP,TOTAL_SHARES',
        filter=f'(SECURITY_CODE="{code[2:]}")(TRADE_DATE=\'{day}\')',
        pageSize=10,pageNumber=1,source='WEB',client='WEB')
    p=json.loads(fetch('https://datacenter-web.eastmoney.com/api/data/v1/get?'+urllib.parse.urlencode(params)))
    rows=(p.get('result') or {}).get('data') or []
    if not p.get('success') or len(rows)!=1:raise DataError('历史估值无唯一记录 '+code)
    row=rows[0]
    if row['TRADE_DATE'][:10]!=str(day) or row['SECURITY_CODE']!=code[2:]:raise DataError('历史估值日期或代码不符 '+code)
    cap=Decimal(str(row['TOTAL_MARKET_CAP']))
    if not cap.is_finite() or cap<=0:raise DataError('历史市值无效 '+code)
    return row

def main(day):
    path=ROOT/f'reports/review-{day}.json';p=json.loads(path.read_text())
    errors={}
    for item in p['technical_signals']:
        try:
            row=metadata(item['code'],day)
            if abs(Decimal(str(row['CLOSE_PRICE']))-Decimal(item['metrics']['close']))>Decimal('.01'):
                raise DataError('历史估值收盘与周线不符')
            cap=Decimal(str(row['TOTAL_MARKET_CAP']))/100000000
            name=row['SECURITY_NAME_ABBR']
            item.update(historical_valuation=row,historical_cap_yi=str(cap),valuation_name=name,
                        dated_metadata_eligible=(cap<=250 and not name.upper().startswith(('ST','*ST'))))
        except Exception as exc:errors[item['code']]=str(exc)
    p['historical_metadata_errors']=errors
    p['dated_metadata_eligible_count']=sum(x.get('dated_metadata_eligible',False) for x in p['technical_signals'])
    p['limitations']=['当前主板股票池，未补充近期退市股票',
      f'通过价格条件的股票经周线及日线聚合交叉核对，历史市值以{day}估值表为准',
      '简称来自带日期估值记录；若供应商回填名称，仍需独立核实历史ST状态',
      '只读复核；10万元模拟账本不改动、不补买']
    text=f'# W1 上周复核（历史市值核验）\n\n信号周最后交易日：{day}；买点：{p["buy_date"]}开盘；TIME2：{p["due_date"]}开盘。\n\n'
    text+=f'检查主板{p["universe_count"]}只；价格下载成功{p["completed"]}只、失败{len(p["errors"])}只。纯周线价格条件通过{len(p["technical_signals"])}只；历史市值及带日期简称筛选通过{p["dated_metadata_eligible_count"]}只。\n\n'
    text+='|股票|估值记录简称|周O/H/L/C|周涨幅|52周位置|信号日总市值（亿元）|买入日开盘|单票1万元含费股数|筛选通过|\n|---|---|---|---:|---:|---:|---:|---:|---|\n'
    for x in p['technical_signals']:
        m=x['metrics'];ohlc='/'.join(m[k] for k in ('open','high','low','close'))
        text+=f"|{x['code']}|{x.get('valuation_name',x['name'])}|{ohlc}|{Decimal(m['change'])*100:.2f}%|{Decimal(m['level'])*100:.2f}%|{x.get('historical_cap_yi','未取得')}|{x['observed_buy_open']}|{x['hypothetical_shares']}|{'是' if x.get('dated_metadata_eligible') else '未通过或未核验'}|\n"
    if not p['technical_signals']:text+='|—|无价格条件信号|—|—|—|—|—|—|—|\n'
    text+='\n限制：'+'；'.join(p['limitations'])+'。\n'
    if errors:text+='\n历史市值核验失败：'+json.dumps(errors,ensure_ascii=False)+'\n'
    atomic(path,json.dumps(p,ensure_ascii=False,indent=2)+'\n');atomic(ROOT/f'reports/review-{day}.md',text)
    print(text)
    if errors:raise DataError('历史市值核验不完整')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--date',required=True)
    main(date.fromisoformat(parser.parse_args().date))
