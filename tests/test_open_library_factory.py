import importlib.util,json,sqlite3,tempfile,unittest
from pathlib import Path
TOOL=Path(__file__).resolve().parents[3]/"tool/run_open_library_factory.py"
if not TOOL.exists(): raise unittest.SkipTest(f"host integration tool unavailable: {TOOL}")
S=importlib.util.spec_from_file_location("factory",TOOL); M=importlib.util.module_from_spec(S); S.loader.exec_module(M)
class FactoryTest(unittest.TestCase):
 def test_restart_recovers_materializing(self):
  with tempfile.TemporaryDirectory() as d:
   f=M.Factory(Path(d)); now=1; f.db.execute("insert into candidates values('x',1,'materializing',0,'{}',NULL,?,?)",(now,now)); f.db.close()
   g=M.Factory(Path(d)); self.assertEqual(1,g.recover()); self.assertEqual('retry',g.db.execute("select state from candidates").fetchone()[0])
 def test_pending_backpressure_count_is_durable(self):
  with tempfile.TemporaryDirectory() as d:
   f=M.Factory(Path(d),max_pending=1); f.db.execute("insert into candidates values('x',1,'pending',0,'{}',NULL,1,1)"); self.assertEqual(1,f.pending()); f.db.close(); self.assertEqual(1,M.Factory(Path(d),1).pending())
 def test_materialization_failure_isolated_for_retry(self):
  with tempfile.TemporaryDirectory() as d:
   f=M.Factory(Path(d)); f.db.execute("insert into candidates values('x',1,'pending',0,?,NULL,1,1)",(json.dumps({'workSnapshot':{}}),)); result=f.materialize_one(); self.assertEqual('retry',result['state']); self.assertEqual('retry',f.db.execute("select state from candidates").fetchone()[0])
