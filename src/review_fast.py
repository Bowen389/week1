"""批量历史日截面前筛的只读单周复核，不更改正式模拟仓。"""
import argparse,json,time
from collections import Counter
from datetime import date,datetime
from decimal import Decimal
from . import data,valuation
from .strategy import monday,signal,next_session,due_session,tradable
from .run import ROOT,atomic,summary

def main(day):
    started=time.monotonic();sessions=data.calendar(date.today())
    buy=next_session(sessions,day)
    if day not in sessions or monday(buy)==monday(day):raise ValueError('不是完整信号周末交易日')
    previous=max(x for x in sessions if x<monday(day))
    current=valuation.day_snapshot(day);prior=valuation.day_snapshot(previous)
    selected=[];counts=Counter();errors={}
    for code,row in current.items():
        try:
            name=row['SECURITY_NAME_ABBR'];cap=Decimal(str(row['TOTAL_MARKET_CAP']))
            if not name or not cap.is_finite() or cap<=0:raise ValueError('无有效历史名称或市值')
            if name.upper().startswith(('ST','*ST')):counts['ST']+=1;continue
            if cap>Decimal('25000000000'):counts['市值超250亿']+=1;continue
            base=prior.get(code)
            if not base or base['CLOSE_PRICE'] is None:
                selected.append(code);counts['缺周涨幅基准，回退历史']+=1;continue
            c=Decimal(str(row['CLOSE_PRICE']));p=Decimal(str(base['CLOSE_PRICE']))
            if not c.is_finite() or not p.is_finite() or min(c,p)<=0:raise ValueError('历史收盘无效')
            growth=c/p-1
            if Decimal('.07')<=growth<=Decimal('.10'):
                selected.append(code);counts['周涨幅合格']+=1
            else:counts['周涨幅不符']+=1
        except Exception as exc:errors[code]=str(exc)
    print(f'FAST_REVIEW_PREFILTER mainboard={len(current)} need_history={len(selected)} counts={dict(counts)}',flush=True)
    histories,download_errors=data.histories(sorted(selected),workers=6);errors.update(download_errors)
    matches=[];reasons=Counter()
    for code,bars in histories.items():
        try:
            ok,reason,metrics=signal(bars,day);reasons[reason]+=1
            if not ok:continue
            row=current[code]
            if abs(Decimal(metrics['close'])-Decimal(str(row['CLOSE_PRICE'])))>Decimal('.01'):
                raise ValueError('历史日截面与日线收盘不一致')
            opening=next((b for b in bars if b['date']==str(buy)),None)
            allowed=tradable(dict(opening,name=row['SECURITY_NAME_ABBR']),'buy') if opening else False
            matches.append(dict(code=code,name=row['SECURITY_NAME_ABBR'],metrics=metrics,
                cap_yi=str(Decimal(str(row['TOTAL_MARKET_CAP']))/100000000),
                buy_date=str(buy),due_date=str(due_session(sessions,buy)),
                actual_open=opening['open'] if opening else None,opening_buy_allowed=allowed,
                historical_valuation=row))
        except Exception as exc:errors[code]=str(exc)
    elapsed=round(time.monotonic()-started,2)
    result=dict(mode='READ_ONLY_FAST_HISTORICAL_REVIEW',signal_date=str(day),prior_week_close_date=str(previous),
        buy_date=str(buy),due_date=str(due_session(sessions,buy)),mainboard_count=len(current),
        long_history_requests=len(selected),prefilter_counts=dict(counts),rule_counts=dict(reasons),
        signals=matches,errors=errors,elapsed_seconds=elapsed,
        limitations=['简称采用带历史日期的估值记录，供应商可能回填简称；未独立取得历史ST状态',
                     '模拟仓不修改，历史买点不补买；开盘可成交性仅作日线保守模拟判断'])
    text=f'# W1 上周快速复核\n\n信号周最后交易日：{day}；前周收盘基准：{previous}。买点：{buy}开盘；TIME2：{result["due_date"]}开盘。\n\n'
    text+=f'历史主板池{len(current)}只；需要长历史核验{len(selected)}只；W1通过{len(matches)}只；接口/数据异常{len(errors)}只。耗时{elapsed}秒。\n\n'
    text+='|股票|估值记录简称|周O/H/L/C|周涨幅|52周位置|历史市值（亿）|买入日开盘|开盘模拟可买|\n|---|---|---|---:|---:|---:|---:|---|\n'
    for x in matches:
        m=x['metrics'];ohlc='/'.join(m[k] for k in ('open','high','low','close'))
        text+=f"|{x['code']}|{x['name']}|{ohlc}|{Decimal(m['change'])*100:.2f}%|{Decimal(m['level'])*100:.2f}%|{x['cap_yi']}|{x['actual_open']}|{'是' if x['opening_buy_allowed'] else '否/未取得'}|\n"
    if not matches:text+='|—|没有通过全部W1条件的股票|—|—|—|—|—|—|\n'
    text+='\n前置筛选：'+json.dumps(dict(counts),ensure_ascii=False)+'\n\n其余规则：'+json.dumps(dict(reasons),ensure_ascii=False)+'\n\n'
    text+='限制：'+'；'.join(result['limitations'])+'。\n'
    if errors:text+='\n**数据异常存在，不能认定全池无信号。**\n'
    atomic(ROOT/f'reports/review-fast-{day}.json',json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    atomic(ROOT/f'reports/review-fast-{day}.md',text);summary(text)
    if errors:raise data.DataError('快速复核有数据异常，检查报告后重试失败标的')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--date',required=True);main(date.fromisoformat(p.parse_args().date))
