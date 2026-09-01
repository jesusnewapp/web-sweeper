import hashlib, json, tempfile, unittest
from pathlib import Path
from sweeper.ultra import OverflowDock, OverflowIntegrityError, OverflowPressure

def write(path,value): path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value)+"\n")

class OverflowTest(unittest.TestCase):
    def unit(self,base,name="u"):
        root=base/name; write(root/"catalog.json",{"books":[{"id":"a"}]}); write(root/"manuscripts/a.json",{"id":"a","content":"x"})
        for name in ("review.json","validation.json","routes.json"): write(root/name,{"passed":True})
        exact={"bookIds":["a"],"catalogSha256":hashlib.sha256((root/"catalog.json").read_bytes()).hexdigest(),"manuscriptSetSha256":"b"*64,"membershipSha256":"c"*64}
        return root,exact
    def queued(self,base,dock,name="u"):
        root,exact=self.unit(base,name); return root,exact,dock.enqueue(name,root,exact,root/"review.json",root/"validation.json",root/"routes.json")
    def test_uploader_busy_or_failure_does_not_block_queue(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); dock=OverflowDock(base/"pending.json"); self.queued(base,dock,"u1"); self.queued(base,dock,"u2")
            dock.isolate_uploader_failure("u1","writer busy"); self.assertEqual(2,len(dock.units())); self.assertEqual("local-pending-staging",dock.units()[1]["state"])
    def test_staging_unavailable_preserves_payload(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); dock=OverflowDock(base/"pending.json"); root,_,_=self.queued(base,dock)
            dock.staging_unavailable("u","offline"); self.assertTrue((root/"manuscripts/a.json").is_file())
    def test_verified_cleanup_and_restart_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); index=base/"pending.json"; dock=OverflowDock(index); root,exact,_=self.queued(base,dock)
            receipt={**{k:exact[k] for k in ("catalogSha256","manuscriptSetSha256","membershipSha256")},"itemCount":1,"memberIds":["a"],"verified":True,"byteIdenticalToValidatedLocalArtifacts":True}
            write(root/"staging.json",receipt); dock.staging_verified("u",root/"staging.json"); dock.cleanup_verified("u")
            self.assertFalse((root/"manuscripts/a.json").exists()); self.assertTrue((root/"overflow_recovery_log.json").is_file())
            recovered=OverflowDock(index); self.assertEqual("u",recovered.oldest_verified()["unitId"])
    def test_queue_pressure_never_drops_existing_unit(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); dock=OverflowDock(base/"pending.json",max_units=1); self.queued(base,dock,"u1")
            root,exact=self.unit(base,"u2")
            with self.assertRaises(OverflowPressure): dock.enqueue("u2",root,exact,root/"review.json",root/"validation.json",root/"routes.json")
            self.assertEqual(["u1"],[x["unitId"] for x in dock.units()])
    def test_unverified_cleanup_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); dock=OverflowDock(base/"pending.json"); root,_,_=self.queued(base,dock)
            with self.assertRaises(OverflowIntegrityError): dock.cleanup_verified("u")
            self.assertTrue((root/"manuscripts/a.json").exists())
