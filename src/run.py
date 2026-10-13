"""盘后运行入口。先保存账本，再由工作流提交，然后发送已持久化的提醒。"""
from __future__ import annotations
import argparse, copy, json, os, traceback, urllib.parse, urllib.request
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from . import data, market_cache
from .strategy import new_state, signal, monday, next_session, execute_day, money

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'data/state.json'
REPORT=ROOT/'reports/latest.md'

def load():
    state=json.loads(STATE.read_text()) if STATE.exists() else new_state()
    if state['schema_version']!=1:raise ValueError('账本版本不兼容')
    if Decimal(state['cash'])<0:raise ValueError('账本现金为负')
    return state

def atomic(path: Path, content: str):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(content,encoding='utf-8');temp.replace(path)

def save(state):atomic(STATE,json.dumps(state,ensure_ascii=False,indent=2)+'\n')

def summary(text):
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as f:f.write(text+'\n')
    print(text,flush=True)

def render(state,messages,status,today):
    cash=Decimal(state['cash']);value=sum(Decimal(p['last_price'])*p['shares'] for p in state['positions'].values())
    text=f'# W1 · 10万元模拟仓\n\n日期：{today}（北京时间）\n\n状态：{status}\n\n现金：{money(cash)}元；持仓市值：{money(value)}元；权益：{money(cash+value)}元。\n\n'
    text+='仅为公开开盘价模拟记账，没有券商订单；非实际开盘成交保证。无止损，只有TIME2退出。\n\n'
    text+='## 本次运行\n\n'+('\n'.join('- '+m for m in messages) or '- 无交易或新信号')+'\n\n'
    text+='## 持仓\n\n|股票|名称|股数|买入日期|计划退出日期|最新记账价格|\n|---|---|---:|---|---|---:|\n'
    for code,p in sorted(state['positions'].items()):
        text+=f"|{code}|{p['name']}|{p['shares']}|{p['buy_date']}|{p['due_date']}|{p['last_price']}|\n"
    if not state['positions']:text+='|—|空仓|0|—|—|—|\n'
    text+='\n## 下一次买点\n\n|股票|名称|信号周末|买入日|单票预算上限|状态|\n|---|---|---|---|---:|---|\n'
    pending=[p for p in state['plans'] if p['status']=='PENDING']
    for p in pending:text+=f"|{p['code']}|{p['name']}|{p['signal_date']}|{p['buy_date']} 开盘|10000元含费用|待模拟买入|\n"
    if not pending:text+='|—|无待执行买点|—|—|—|—|\n'
    text+='\n完整条件：阳线、严格光脚、上影>0且≤实体15%、周涨幅7%至10%、52周收盘位置≤40%、主板非ST、信号日总市值≤250亿元。\n'
    return text

def queue(state,event_id,title,body):
    if not any(e['id']==event_id for e in state['outbox']):
        state['outbox'].append(dict(id=event_id,title=title,body=body,sent=False))

def close_quotes(codes,today):
    q=data.quotes(codes)
    for code,item in q.items():
        stamp=item['timestamp']
        if stamp[:8]!=today.strftime('%Y%m%d') or stamp[8:12]<'1500':
            raise data.DataError(f'{code} 行情不是当日收盘: {stamp}')
    return q

def collect_signals(state,today,sessions,messages,market=None):
    key=str(monday(today));buy=next_session(sessions,today)
    if key in state['scanned_weeks']:
        messages.append('本周已完整扫描，避免重复买点');return
    if market is None:
        ranks=data.rank_snapshot();q=data.quotes(sorted(ranks))
    else:ranks,q=market
    codes=sorted(ranks);eligible=[];excluded=Counter()
    for code in codes:
        item=q[code]
        if item['timestamp'][:8]!=today.strftime('%Y%m%d') or item['timestamp'][8:12]<'1500':
            excluded['当日停牌或行情过时']+=1;continue
        if not item['name']:raise data.DataError('股票简称为空 '+code)
        if item['name'].upper().startswith(('ST','*ST')):excluded['ST']+=1;continue
        cap=Decimal(item['cap_yi'])
        if not cap.is_finite() or cap<=0:raise data.DataError('总市值无效 '+code)
        if cap>250:excluded['总市值超250亿']+=1;continue
        eligible.append(code)
    before=len(eligible)
    eligible,growth_counts=market_cache.price_candidates(eligible,q,ranks,today,sessions,state.setdefault('market_closes',{}))
    messages.append(f'周涨幅前筛：市值/ST过滤后{before}只，需历史核验{len(eligible)}只；缓存统计{growth_counts}')
    print(f'SCAN_START universe={len(codes)} eligible={len(eligible)} excluded={dict(excluded)} growth={growth_counts}',flush=True)
    histories,errors=data.histories(eligible)
    if errors:
        # 只要发生下载错误就拒绝发布“完整扫描”，不把接口失败说成无信号。
        raise data.DataError(f'周扫描未完成: 成功{len(histories)}/{len(eligible)}; 失败示例{list(errors.items())[:8]}')
    candidates=[];reasons=Counter()
    for code in eligible:
        bars=histories[code]
        if bars and bars[-1]['date']!=str(today):raise data.DataError('日线未更新 '+code)
        if bars and abs(Decimal(bars[-1]['close'])-Decimal(q[code]['price']))>Decimal('.01'):
            raise data.DataError('日线收盘与快照不一致 '+code)
        ok,reason,metrics=signal(bars,today);reasons[reason]+=1
        if ok:
            candidates.append(dict(code=code,name=q[code]['name'],signal_date=str(today),
                buy_date=str(buy),cap_yi=q[code]['cap_yi'],metrics=metrics,status='PENDING'))
    state['scanned_weeks'][key]=dict(signal_date=str(today),buy_date=str(buy),
        universe=len(codes),eligible=len(eligible),excluded=dict(excluded),growth_prefilter=growth_counts,reasons=dict(reasons),signals=candidates)
    for candidate in candidates:
        if candidate['code'] not in state['positions']:state['plans'].append(copy.deepcopy(candidate))
        else:messages.append('持仓期间忽略新信号 '+candidate['code'])
    messages.append(f'完整周扫描完成：主板{len(codes)}只；规则候选{len(candidates)}只；买点{buy}开盘')
    lines=[f"{x['code']} {x['name']}；周涨幅{Decimal(x['metrics']['change'])*100:.2f}%；52周位置{Decimal(x['metrics']['level'])*100:.2f}%；市值{x['cap_yi']}亿" for x in candidates]
    for line in lines:print('W1_SIGNAL '+line,flush=True)
    details='\n\n'.join(lines) if lines else '完整扫描后，本周无符合W1的股票。'
    body=f'信号周最后交易日：{today}\n\n买点：{buy}开盘。\n\n'+details+'\n\n10万元独立模拟仓，最多10只，单票含费不超过1万元；已有持仓忽略新信号。TIME2为买入周周一+14天后的首个交易日开盘退出。'
    queue(state,'signals-'+key,'W1周线买点 '+str(buy),body)
    atomic(ROOT/f'reports/signals-{today}.json',json.dumps(state['scanned_weeks'][key],ensure_ascii=False,indent=2)+'\n')

def settle(state,today,sessions,messages):
    if str(today) in state['processed_days']:messages.append('今日模拟账本已记账');return
    tracked=set(state['positions'])|{x['code'] for x in state['plans'] if x['status']=='PENDING' and x['buy_date']==str(today)}
    histories,errors=data.histories(sorted(tracked))
    if errors:raise data.DataError('模拟记账行情下载失败: '+str(errors))
    q=data.quotes(sorted(tracked)) if tracked else {}
    today_bars={}
    for code,bars in histories.items():
        bar=next((b for b in bars if b['date']==str(today)),None)
        if bar is None:
            if q[code]['timestamp'][:8]==today.strftime('%Y%m%d') and Decimal(q[code]['volume'])>0:
                raise data.DataError('快照已交易但缺当日日线 '+code)
            continue  # 有效查询无当日日线且无当日成交，视为停牌。
        if q[code]['timestamp'][:8]!=today.strftime('%Y%m%d'):
            raise data.DataError('模拟持仓快照过时 '+code)
        bar=dict(bar,name=q[code]['name'])
        if code in state['positions'] and bar['previous_close'] and abs(Decimal(bar['previous_close'])-Decimal(q[code]['previous_close']))>Decimal('.02'):
            raise data.DataError('持仓可能除权除息，暂停账本需核对 '+code)
        bar['previous_close']=q[code]['previous_close']
        today_bars[code]=bar
    traded=execute_day(state,today,sessions,today_bars);messages.extend(traded)
    if any(x.startswith(('模拟','卖出顺延')) for x in traded):
        queue(state,'paper-'+str(today),'W1模拟仓日报 '+str(today),'\n\n'.join(traded))

def run(now=None):
    now=now or datetime.now(ZoneInfo('Asia/Shanghai'));today=now.date()
    state=load();working=copy.deepcopy(state);messages=[]
    try:
        sessions=data.calendar(today)
        if today not in sessions:
            text=render(state,['今日非交易日，不扫描、不记成交'],'休市',today)
            atomic(REPORT,text);summary(text);return
        if now.hour<16:raise data.DataError('仅收盘后16:00起运行，禁止使用未结束日线')
        close_quotes(['sh000001'],today)  # 基准行情时间戳核对日期，防日历/免费接口过时。
        # 每日缓存收盘，避免每周给所有股票重复下载长历史。
        ranks=data.rank_snapshot();q=data.quotes(sorted(ranks))
        market_cache.remember(working.setdefault('market_closes',{}),today,q)
        # 先模拟执行已有计划，随后才生成今天完整周的新计划。
        settle(working,today,sessions,messages)
        if monday(next_session(sessions,today))!=monday(today):
            collect_signals(working,today,sessions,messages,market=(ranks,q))
        else:messages.append('本周尚未结束，等待本周最后交易日收盘生成W1')
        save(working)
        text=render(working,messages,'正常',today)
        atomic(REPORT,text);summary(text)
    except Exception as exc:
        text=render(state,[f'数据异常，账本未改动：{exc}'],'失败：不能解释为无信号',today)
        atomic(REPORT,text);summary(text)
        queue(state,'error-'+str(today),'W1运行异常 '+str(today),str(exc))
        save(state)
        raise

def notify():
    # 显式允许通过用户配置的Server酱向用户推送买点；不记录密钥。
    state=load();sendkey=os.environ.get('SERVERCHAN_SENDKEY') or os.environ.get('SENDKEY')
    if not sendkey:
        print('NOTIFY_NOT_CONFIGURED: 买点可见于reports/latest.md和Actions摘要；配置SERVERCHAN_SENDKEY开启推送')
        return
    if not all(c.isalnum() or c in '_-' for c in sendkey):raise ValueError('Server酱SendKey格式异常')
    changed=False
    for event in state['outbox']:
        if event['sent']:continue
        # 网络边界只能做到重试时可能重复，不能承诺外部消息exactly-once。
        form=urllib.parse.urlencode({'title':event['title'],'desp':event['body']}).encode()
        try:
            with urllib.request.urlopen('https://sctapi.ftqq.com/'+sendkey+'.send',data=form,timeout=20) as response:
                result=json.loads(response.read())
            if result.get('code')!=0:raise RuntimeError('Server酱返回失败状态')
        except Exception:
            raise RuntimeError('Server酱发送失败（密钥及响应不输出）') from None
        event['sent']=True;changed=True
    if changed:save(state)

def health():
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    sessions=data.calendar(today)
    q=data.quotes(['sh000001','sz003001'])
    bars=data.history('sz003001')
    if len(bars)<250:raise data.DataError('腾讯历史覆盖不足')
    print(json.dumps({'calendar_end':str(sessions[-1]),'quote_date':q['sh000001']['timestamp'],
                      'sample_name':q['sz003001']['name'],'daily_bars':len(bars),'latest_bar':bars[-1]['date']},ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['run','notify','health'],default='run',nargs='?')
    args=parser.parse_args()
    try:{'run':run,'notify':notify,'health':health}[args.command]()
    except Exception:
        traceback.print_exc();raise SystemExit(1)
