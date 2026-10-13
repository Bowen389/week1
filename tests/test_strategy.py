import copy, unittest
from datetime import date, timedelta
from decimal import Decimal
from src.strategy import signal, execute_day, new_state, due_session, fees, weeks

def candles():
    start=date(2024,1,1);bars=[]
    for week in range(60):
        close='20'
        if week==7:close='10'
        if week==8:close='30'
        if week==58:close='11'
        for day in range(5):
            row=dict(date=str(start+timedelta(weeks=week,days=day)),open=close,close=close,high=close,low=close,volume='100')
            if week==59:row.update(open='11.50',close='11.88',high='11.92',low='11.50')
            bars.append(row)
    return bars,date.fromisoformat(bars[-1]['date'])

class Rules(unittest.TestCase):
    def test_positive(self):
        b,d=candles();self.assertTrue(signal(b,d)[0])
    def test_strict_low(self):
        b,d=candles();b[-1]['low']='11.499';self.assertEqual(signal(b,d)[1],'非严格光脚')
    def test_upper_shadow(self):
        b,d=candles()
        for x in b[-5:]:x['high']='11.88'
        self.assertEqual(signal(b,d)[1],'无上影')
        b[-1]['high']='12';self.assertEqual(signal(b,d)[1],'上影过长')
    def test_change_boundaries(self):
        b,d=candles()
        for x in b[-5:]:x.update(open='11.50',low='11.50',close='11.77',high='11.78')
        self.assertTrue(signal(b,d)[0])
        for x in b[-5:]:x.update(close='11.769',high='11.78')
        self.assertEqual(signal(b,d)[1],'涨幅越界')
        for x in b[-5:]:x.update(close='12.10',high='12.11')
        self.assertTrue(signal(b,d)[0])
        for x in b[-5:]:x.update(close='12.101',high='12.11')
        self.assertEqual(signal(b,d)[1],'涨幅越界')
    def test_position_filter(self):
        b,d=candles()
        for x in b[:-10]:x.update(close='11',open='11',low='11',high='11')
        self.assertEqual(signal(b,d)[1],'位置过高')
    def test_current_week_excluded(self):
        b,d=candles();before=signal(b,d)
        b.append(dict(date=str(d+timedelta(days=3)),open='99',close='100',low='99',high='101',volume='10'))
        self.assertEqual(signal(b,d),before)
    def test_52_required(self):
        b,d=candles();self.assertEqual(signal(b[-250:],d)[1],'不足52根完整周线')
    def test_time2_holidays(self):
        days=[date(2026,9,28),date(2026,9,30),date(2026,10,9),date(2026,10,12)]
        self.assertEqual(due_session(days,date(2026,9,28)),date(2026,10,12))
        self.assertEqual(due_session(days,date(2026,9,30)),date(2026,10,12))
    def test_fees(self):
        self.assertEqual(fees(Decimal('10000')),Decimal('5'))
        self.assertEqual(fees(Decimal('10000'),True),Decimal('10'))

class Ledger(unittest.TestCase):
    def setUp(self):
        self.day=date(2026,1,12)
        self.sessions=[self.day+timedelta(days=i) for i in range(25) if (self.day+timedelta(days=i)).weekday()<5]
        self.s=new_state();self.s['plans']=[dict(code='sz003001',name='中岩大地',buy_date=str(self.day),signal_date='2026-01-09',status='PENDING')]
        self.b=dict(open='10',close='10.20',volume='100',previous_close='10',name='中岩大地')
    def test_buy_budget_idempotence_and_due_sell(self):
        execute_day(self.s,self.day,self.sessions,{'sz003001':self.b})
        self.assertEqual(self.s['positions']['sz003001']['shares'],900)
        self.assertEqual(self.s['positions']['sz003001']['due_date'],'2026-01-26')
        before=copy.deepcopy(self.s);execute_day(self.s,self.day,self.sessions,{'sz003001':self.b});self.assertEqual(self.s,before)
        execute_day(self.s,date(2026,1,26),self.sessions,{'sz003001':self.b})
        self.assertFalse(self.s['positions']);self.assertEqual(len(self.s['trades']),2)
        self.assertEqual(Decimal(self.s['cash']),Decimal('99985.50'))
    def test_no_premature_exit(self):
        execute_day(self.s,self.day,self.sessions,{'sz003001':self.b})
        execute_day(self.s,date(2026,1,23),self.sessions,{'sz003001':self.b})
        self.assertIn('sz003001',self.s['positions'])
    def test_limit_up_skip(self):
        self.b['open']='11';execute_day(self.s,self.day,self.sessions,{'sz003001':self.b})
        self.assertFalse(self.s['positions']);self.assertEqual(self.s['plans'][0]['status'],'SKIPPED_OPEN')
    def test_limit_down_defers_exit(self):
        execute_day(self.s,self.day,self.sessions,{'sz003001':self.b});self.b['open']='9'
        execute_day(self.s,date(2026,1,26),self.sessions,{'sz003001':self.b});self.assertTrue(self.s['positions'])
        self.b['open']='10';execute_day(self.s,date(2026,1,27),self.sessions,{'sz003001':self.b});self.assertFalse(self.s['positions'])
    def test_missed_buy_not_replayed(self):
        execute_day(self.s,date(2026,1,13),self.sessions,{'sz003001':self.b})
        self.assertEqual(self.s['plans'][0]['status'],'EXPIRED');self.assertFalse(self.s['positions'])
    def test_held_signal_ignored(self):
        execute_day(self.s,self.day,self.sessions,{'sz003001':self.b})
        self.s['plans'].append(dict(code='sz003001',name='中岩大地',buy_date='2026-01-13',status='PENDING'))
        execute_day(self.s,date(2026,1,13),self.sessions,{'sz003001':self.b})
        self.assertEqual(self.s['plans'][-1]['status'],'IGNORED_HELD');self.assertEqual(len(self.s['trades']),1)
    def test_10_slots_and_no_overspend(self):
        self.s['plans']=[dict(code=f'sh600{i:03d}',name='测试',buy_date=str(self.day),status='PENDING') for i in range(12)]
        bars={p['code']:self.b for p in self.s['plans']};execute_day(self.s,self.day,self.sessions,bars)
        self.assertEqual(len(self.s['positions']),10);self.assertGreaterEqual(Decimal(self.s['cash']),0)
        self.assertTrue(all(Decimal(p['buy_cost'])<=10000 for p in self.s['positions'].values()))
