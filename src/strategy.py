"""W1精确价格规则和TIME2；不包含回测、券商接口或网络请求。"""
from __future__ import annotations
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

D = Decimal
PREFIXES = ('600','601','603','605','000','001','002','003')

def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())

def money(value) -> Decimal:
    return D(str(value)).quantize(D('.01'), rounding=ROUND_HALF_UP)

def weeks(bars: list[dict], signal_date: date) -> list[dict]:
    groups = defaultdict(list)
    for b in bars:
        day = date.fromisoformat(b['date'])
        if day <= signal_date:
            groups[monday(day)].append(b)
    result = []
    for week, items in sorted(groups.items()):
        items.sort(key=lambda x:x['date'])
        # 初始请求可能截断首周，丢掉最早一组；400日线有足够冗余。
        result.append({'week':str(week), 'open':D(items[0]['open']),
                       'high':max(D(b['high']) for b in items),
                       'low':min(D(b['low']) for b in items),
                       'close':D(items[-1]['close']),
                       'volume':sum(D(b['volume']) for b in items)})
    return result[1:]

def signal(bars: list[dict], day: date) -> tuple[bool,str,dict]:
    w = weeks(bars,day)
    if len(w) < 52: return False,'不足52根完整周线',{}
    last=w[-1]
    if last['week'] != str(monday(day)): return False,'信号周无行情',{}
    o,h,l,c = [last[x] for x in ('open','high','low','close')]
    previous=w[-2]['close'];closes=[x['close'] for x in w[-52:]]
    span=max(closes)-min(closes)
    if span <= 0 or previous <= 0: return False,'历史价格无效',{}
    change=c/previous-1;level=(c-min(closes))/span
    metrics={k:str(last[k]) for k in ('open','high','low','close')}
    metrics.update(change=str(change),level=str(level),shadow_ratio=str((h-c)/(c-o)) if c>o else None)
    for ok,reason in [(c>o,'非阳线'),(l==o,'非严格光脚'),(h>c,'无上影'),
                      (h-c<=D('.15')*(c-o),'上影过长'),
                      (D('.07')<=change<=D('.10'),'涨幅越界'),
                      (level<=D('.40'),'位置过高'),(last['volume']>0,'全周无成交')]:
        if not ok:return False,reason,metrics
    return True,'W1',metrics

def next_session(sessions: list[date], day: date) -> date:
    return next(x for x in sessions if x > day)

def due_session(sessions: list[date], buy_date: date) -> date:
    target=monday(buy_date)+timedelta(days=14)
    return next(x for x in sessions if x >= target)

def fees(value: Decimal, selling=False) -> Decimal:
    commission=max(D('5'),money(value*D('.0003')))
    return commission+(money(value*D('.0005')) if selling else D('0'))

def tradable(bar: dict, side: str) -> bool:
    # 精确开盘可成交性不能由日K证明；保守跳过开盘触及涨/跌停。
    if D(bar['volume']) <= 0:return False
    if not bar.get('previous_close'):return False
    opening=D(bar['open']);prev=D(bar['previous_close'])
    rate=D('.05') if bar.get('name','').upper().startswith(('ST','*ST')) else D('.10')
    upper=money(prev*(1+rate));lower=money(prev*(1-rate))
    return opening < upper if side=='buy' else opening > lower

def execute_day(state: dict, day: date, sessions: list[date], bars: dict, max_positions=10) -> list[str]:
    """盘后按当日公开开盘价记模拟成交；单日幂等，失败标的延期/过期。"""
    key=str(day)
    if key in state['processed_days']:return ['今日模拟记账已完成，不重复执行']
    # 行情获取异常必须在调用前抛出；此处不得把网络失败作为停牌。
    messages=[];positions=state['positions'];cash=money(state['cash'])
    for code in sorted(list(positions)):
        p=positions[code]
        if day < date.fromisoformat(p['due_date']):continue
        b=bars.get(code)
        if b is None or not tradable(b,'sell'):
            messages.append(f"卖出顺延 {code}：停牌/开盘跌停/缺可交易行情")
            continue
        price=D(b['open']);value=money(price*p['shares']);fee=fees(value,True)
        cash += value-fee
        pnl=money(value-fee-D(p['buy_cost']))
        state['trades'].append(dict(side='SELL',code=code,name=p['name'],date=key,
            shares=p['shares'],price=str(price),fee=str(fee),pnl=str(pnl),reason='TIME2'))
        messages.append(f"模拟卖出 {code} {p['name']} {p['shares']}股 开盘{price} 净盈亏{pnl}")
        del positions[code]
    for plan in sorted(state['plans'],key=lambda x:(x['buy_date'],x['code'])):
        if plan['status']!='PENDING':continue
        if date.fromisoformat(plan['buy_date']) < day:
            plan['status']='EXPIRED';messages.append(f"买点过期 {plan['code']}：不补买")
            continue
        if plan['buy_date']!=key:continue
        code=plan['code'];b=bars.get(code)
        if code in positions:
            plan['status']='IGNORED_HELD';continue
        if len(positions)>=max_positions:
            plan['status']='SKIPPED_SLOTS';messages.append(f"跳过买入 {code}：仓位已满");continue
        if b is None or not tradable(b,'buy'):
            plan['status']='SKIPPED_OPEN';messages.append(f"跳过买入 {code}：停牌/开盘涨停/无可交易行情");continue
        if b.get('name','').upper().startswith(('ST','*ST')):
            plan['status']='SKIPPED_ST';continue
        price=D(b['open']);budget=min(D('10000'),cash)
        qty=int(budget/price/100)*100
        while qty>0 and money(price*qty)+fees(money(price*qty))>budget:qty-=100
        if qty<=0:plan['status']='SKIPPED_CASH';continue
        value=money(price*qty);fee=fees(value);cost=value+fee;cash-=cost
        due=str(due_session(sessions,day))
        positions[code]=dict(name=plan['name'],shares=qty,buy_date=key,due_date=due,
                             buy_cost=str(cost),last_price=str(price))
        state['trades'].append(dict(side='BUY',code=code,name=plan['name'],date=key,
            shares=qty,price=str(price),fee=str(fee),due_date=due))
        plan['status']='FILLED'
        messages.append(f"模拟买入 {code} {plan['name']} {qty}股 开盘{price} TIME2退出{due}")
    for code,p in positions.items():
        if bars.get(code):p['last_price']=str(bars[code]['close'])
    state['cash']=str(money(cash));state['processed_days'].append(key)
    return messages

def new_state() -> dict:
    return dict(schema_version=1,initial_capital='100000.00',cash='100000.00',
                positions={},plans=[],trades=[],processed_days=[],scanned_weeks={},outbox=[])
