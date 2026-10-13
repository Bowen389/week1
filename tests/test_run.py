import tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
from datetime import date,datetime
from zoneinfo import ZoneInfo
from src import run
from src.strategy import new_state

class Runner(unittest.TestCase):
    def test_scan_error_preserves_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state.json';report=root/'latest.md'
            s=new_state();state.write_text(json.dumps(s))
            day=date(2026,1,9)
            with patch.object(run,'STATE',state),patch.object(run,'REPORT',report),patch.object(run.data,'calendar',return_value=[day,date(2026,1,12)]),patch.object(run,'close_quotes',return_value={}),patch.object(run,'collect_signals',side_effect=run.data.DataError('下载失败')),patch.object(run,'summary'):
                with self.assertRaises(run.data.DataError):run.run(datetime(2026,1,9,17,20,tzinfo=ZoneInfo('Asia/Shanghai')))
            after=json.loads(state.read_text());self.assertEqual(after['processed_days'],[]);self.assertEqual(after['cash'],'100000.00')
            self.assertIn('不能解释为无信号',report.read_text())
    def test_duplicate_week_no_plan(self):
        s=new_state();s['scanned_weeks']['2026-01-05']={};messages=[]
        with patch.object(run.data,'universe',side_effect=AssertionError('不应再次下载')):
            run.collect_signals(s,date(2026,1,9),[date(2026,1,12)],messages)
        self.assertFalse(s['plans'])
    def test_before_close_refuses_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/'state.json';report=Path(tmp)/'report.md'
            with patch.object(run,'STATE',state),patch.object(run,'REPORT',report),patch.object(run.data,'calendar',return_value=[date(2026,1,9)]),patch.object(run,'summary'):
                with self.assertRaises(run.data.DataError):run.run(datetime(2026,1,9,14,tzinfo=ZoneInfo('Asia/Shanghai')))
                self.assertFalse(json.loads(state.read_text())['processed_days'])
