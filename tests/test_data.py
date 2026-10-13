import unittest
from unittest.mock import patch
from datetime import date
from src import data

class DataTests(unittest.TestCase):
    def test_quote_fields_gb18030(self):
        f=['0']*50
        for index,value in {1:'中岩大地',3:'18.19',4:'17.85',5:'17.37',6:'86608',30:'20261013161433',33:'18.56',34:'16.97',45:'31.75',47:'19.64',48:'16.07'}.items():f[index]=value
        with patch.object(data,'fetch',return_value='v_sz003001="'+'~'.join(f)+'";'):
            q=data.quotes(['sz003001'])['sz003001']
            self.assertEqual(q['cap_yi'],'31.75');self.assertEqual(q['name'],'中岩大地')
    def test_missing_quote_fails(self):
        with patch.object(data,'fetch',return_value=''):
            with self.assertRaises(data.DataError):data.quotes(['sz003001'])
    def test_calendar_2026_national_holiday(self):
        c=data.calendar(date(2026,10,13))
        self.assertIn(date(2026,9,30),c);self.assertIn(date(2026,10,9),c)
        self.assertIn(date(2026,10,8),c)
        for d in range(1,8):self.assertNotIn(date(2026,10,d),c)
    def test_history_ohlc_mapping(self):
        import json
        payload=dict(code=0,data={'sz003001':{'day':[['2026-01-09','11.5','11.88','11.92','11.5','100']]}})
        with patch.object(data,'fetch',return_value=json.dumps(payload)):
            b=data.history('sz003001')[0];self.assertEqual(b['high'],'11.92');self.assertEqual(b['close'],'11.88')
