"""收盘缓存与周涨幅前置筛选。只有少量候选再获取52周历史。"""
from datetime import date
from decimal import Decimal
from .strategy import monday
from .data import DataError

def remember(cache,today,quotes):
    # 记录真实收盘快照；停牌证券保留最后成交价，不伪造当日交易。
    cache[str(today)]={c:q['price'] for c,q in quotes.items() if Decimal(q['price'])>0}
    for key in sorted(cache)[:-20]:del cache[key]

def price_candidates(codes,q,ranks,today,sessions,cache):
    previous=max(x for x in sessions if x<monday(today))
    saved=cache.get(str(previous),{})
    week_days=[x for x in sessions if monday(x)==monday(today) and x<=today]
    result=[];counts={'cached':0,'missing':0,'growth_rejected':0}
    for code in codes:
        current=Decimal(q[code]['price'])
        if code in saved and Decimal(saved[code])>0:
            change=current/Decimal(saved[code])-1;counts['cached']+=1
            keep=Decimal('.07')<=change<=Decimal('.10')
        else:
            # 节假日周不可把5日涨幅冒充周涨幅；无缓存的个别标的回退精确历史。
            counts['missing']+=1;result.append(code);continue
        if keep:result.append(code)
        else:counts['growth_rejected']+=1
    return result,counts
