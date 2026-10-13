"""只读单周复核：不写data/state.json、不补买、不触发推送。"""
from __future__ import annotations
import argparse,json,urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import date
from decimal import Decimal
from . import data
from .strategy import signal, next_session, due_session, fees, money
from .run import ROOT,atomic,summary

def weekly(code):
    param=urllib.parse.urlencode({'param':f'{code},week,,,70,'})
    p=json.loads(data.fetch('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?'+param))
    rows=p.get('data',{}).get(code,{}).get('week')
    if p.get('code')!=0 or rows is None:raise data.DataError('缺少不复权周线 '+code)
    result=[]
    for row in rows:
        if len(row)<6:raise data.DataError('周线字段不足 '+code)
        b=dict(zip(['date','open','close','high','low','volume'],[str(x) for x in row[:6]]))
        date.fromisoformat(b['date'])
        o,c,h,l=[Decimal(b[x]) for x in ('open','close','high','low')]
        if any(not x.is_finite() or x<=0 for x in [o,c,h,l]) or h<max(o,c) or l>min(o,c):raise data.DataError('周线价格错误 '+code)
        result.append(b)
    return result

def main(day):
    sessions=data.calendar(date.today())
    if day not in sessions:raise ValueError('指定日期不是交易日')
    buy=next_session(sessions,day)
    from .strategy import monday
    if monday(buy)==monday(day):raise ValueError('指定日期不是信号周最后交易日')
    codes=data.universe();technical=[];errors={};reasons=Counter()
    with ThreadPoolExecutor(max_workers=6) as pool:
        pending={pool.submit(weekly,c):c for c in codes}
        for i,f in enumerate(as_completed(pending),1):
            code=pending[f]
            try:
                bars=f.result();ok,reason,metrics=signal(bars,day);reasons[reason]+=1
                if ok:
                    technical.append(dict(code=code,metrics=metrics))
                    print('TECHNICAL_SIGNAL '+code+' '+json.dumps(metrics),flush=True)
            except Exception as exc:errors[code]=str(exc)
            if i%100==0:print(f'WEEK_REVIEW_PROGRESS {i}/{len(codes)} technical={len(technical)} errors={len(errors)}',flush=True)
    details=[]
    # 未按今天的市值或ST提前过滤；全部主板先检查历史价格条件。
    q=data.quotes([x['code'] for x in technical]) if technical else {}
    for t in sorted(technical,key=lambda x:x['code']):
        code=t['code'];item=q[code]
        bars=data.history(code)
        ok,reason,metrics=signal(bars,day)
        if not ok:raise data.DataError('周线与日线聚合判定不一致 '+code+' '+reason)
        if any(Decimal(metrics[k])!=Decimal(t['metrics'][k]) for k in ('open','high','low','close')):
            raise data.DataError('日线与周线OHLC不一致 '+code)
        estimate=Decimal(item['cap_yi'])/Decimal(item['price'])*Decimal(metrics['close'])
        b=next((x for x in bars if x['date']==str(buy)),None)
        shares=0;fee=None
        if b:
            price=Decimal(b['open']);shares=int(Decimal('10000')/price/100)*100
            while shares>0 and money(price*shares)+fees(money(price*shares))>10000:shares-=100
            fee=str(fees(money(price*shares))) if shares else None
        t.update(name=item['name'],signal_date=str(day),buy_date=str(buy),due_date=str(due_session(sessions,buy)),
                 estimated_signal_cap_yi=str(money(estimate)),cap_method='当前市值/当前股价*信号日收盘，假设股本未变',
                 name_method='当前简称，尚未核实历史ST状态',approximate_eligible=(estimate<=250 and not item['name'].upper().startswith(('ST','*ST'))),
                 observed_buy_open=b['open'] if b else None,hypothetical_shares=shares,hypothetical_buy_fee=fee)
        details.append(t)
    result=dict(mode='RETROSPECTIVE_SINGLE_WEEK_READ_ONLY',signal_date=str(day),buy_date=str(buy),
        due_date=str(due_session(sessions,buy)),universe_count=len(codes),completed=len(codes)-len(errors),
        errors=errors,reasons=dict(reasons),technical_signals=details,
        approximate_eligible_count=sum(x['approximate_eligible'] for x in details),
        limitations=['使用当前主板股票列表，未重建信号日已退市股票',
                     '历史总市值先按当前股本不变估算；历史简称未核实时不能认定严格完整信号',
                     '纯价格条件经不复权周线及日线聚合交叉核对；真实模拟仓不改动'])
    text=f'# W1 上周只读复核\n\n信号周最后交易日：{day}；对应买点：{buy}开盘；TIME2：{result["due_date"]}开盘。\n\n'
    text+=f'主板股票：{len(codes)}；成功：{result["completed"]}；接口失败：{len(errors)}。\n\n'
    text+=f'纯价格条件通过：{len(details)}；以当前简称和估算历史市值进一步筛选：{result["approximate_eligible_count"]}。\n\n'
    text+='历史市值及简称核验程度见下表；该复核不改动10万元模拟仓，也不补买历史信号。\n\n'
    text+='|股票|名称（当前）|周涨幅|52周位置|估算市值（亿）|10月12日开盘|单票1万元含费模拟股数|近似合格|\n|---|---|---:|---:|---:|---:|---:|---|\n'
    for t in details:
        text+=f"|{t['code']}|{t['name']}|{Decimal(t['metrics']['change'])*100:.2f}%|{Decimal(t['metrics']['level'])*100:.2f}%|{t['estimated_signal_cap_yi']}|{t['observed_buy_open']}|{t['hypothetical_shares']}|{'是' if t['approximate_eligible'] else '否'}|\n"
    if not details:text+='|—|纯价格条件无信号|—|—|—|—|—|—|\n'
    if errors:text+='\n**下载未完成，不能据此认定没有信号。**\n'
    text+='\n限制：'+'；'.join(result['limitations'])+'。\n'
    atomic(ROOT/f'reports/review-{day}.json',json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    atomic(ROOT/f'reports/review-{day}.md',text);summary(text)
    if errors:raise data.DataError('周复核存在接口失败，请重新运行')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--date',required=True)
    main(date.fromisoformat(p.parse_args().date))
