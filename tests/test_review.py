import json,unittest
from datetime import date
from unittest.mock import patch
from src import review_week,verify_review
from src.data import DataError

class Review(unittest.TestCase):
    def test_weekly_uses_unadjusted_and_parses(self):
        payload={'code':0,'data':{'sz003001':{'week':[['2026-10-09','19.31','18.65','20.5','17.74','100']]}}}
        with patch.object(review_week.data,'fetch',return_value=json.dumps(payload)) as f:
            b=review_week.weekly('sz003001')[0]
            self.assertEqual(b['close'],'18.65');self.assertEqual(b['low'],'17.74')
            self.assertIn('week',f.call_args.args[0]);self.assertNotIn('qfq',f.call_args.args[0])
    def test_valuation_date_checked(self):
        row={'SECURITY_CODE':'003001','TRADE_DATE':'2026-10-08 00:00:00','TOTAL_MARKET_CAP':1}
        with patch.object(verify_review,'fetch',return_value=json.dumps({'success':True,'result':{'data':[row]}})):
            with self.assertRaises(DataError):verify_review.metadata('sz003001',date(2026,10,9))
    def test_valuation_negative_fails(self):
        row={'SECURITY_CODE':'003001','TRADE_DATE':'2026-10-09 00:00:00','TOTAL_MARKET_CAP':-1}
        with patch.object(verify_review,'fetch',return_value=json.dumps({'success':True,'result':{'data':[row]}})):
            with self.assertRaises(DataError):verify_review.metadata('sz003001',date(2026,10,9))
