import unittest
from datetime import date,timedelta
from src.market_cache import price_candidates,remember
class Cache(unittest.TestCase):
    def test_cached_exact_and_boundaries(self):
        d=date(2026,10,16);sessions=[date(2026,10,9)]+[date(2026,10,12)+timedelta(days=i) for i in range(5)]
        q={c:{'price':p} for c,p in [('a','10.7'),('b','11'),('c','11.01')]}
        selected,counts=price_candidates(list(q),q,{},d,sessions,{'2026-10-09':{c:'10' for c in q}})
        self.assertEqual(selected,['a','b']);self.assertEqual(counts['cached'],3)
    def test_holiday_no_five_day_proxy(self):
        sessions=[date(2026,9,30),date(2026,10,8),date(2026,10,9)]
        selected,c=price_candidates(['a'],{'a':{'price':'11'}},{'a':{'zdf_d5':'-20'}},date(2026,10,9),sessions,{})
        self.assertEqual(selected,['a']);self.assertEqual(c['bootstrap_5day'],0)
    def test_bootstrap_wide_bounds(self):
        d=date(2026,10,16);sessions=[date(2026,10,9)]+[date(2026,10,12)+timedelta(days=i) for i in range(5)]
        q={c:{'price':'10'} for c in ['a','b','c']};ranks={c:{'zdf_d5':v} for c,v in [('a','6.5'),('b','10.5'),('c','15')]}
        self.assertEqual(price_candidates(list(q),q,ranks,d,sessions,{})[0],['a','b'])
    def test_remember_only_twenty_days(self):
        c={str(date(2026,8,1)+timedelta(days=i)):{'a':'10'} for i in range(25)}
        remember(c,date(2026,10,13),{'a':{'price':'11'}})
        self.assertEqual(len(c),20);self.assertEqual(c['2026-10-13']['a'],'11')
