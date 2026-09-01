from __future__ import annotations

import hashlib, json, os
from pathlib import Path

class OverflowIntegrityError(RuntimeError): pass
class OverflowPressure(RuntimeError): pass

def canonical(v): return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def atomic(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp.%s" % os.getpid())
    tmp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8"); tmp.replace(path)

class OverflowDock:
    """Restart-safe FIFO; production-writer state is deliberately irrelevant."""
    def __init__(self, path, max_units=8, max_local_bytes=20 * 1024**3):
        if max_units < 1 or max_local_bytes < 1: raise ValueError("bounds must be positive")
        self.path=Path(path); self.max_units=int(max_units); self.max_local_bytes=int(max_local_bytes)
        self.state=self._load()
    def policy(self):
        return {"maxUnits":self.max_units,"maxLocalBytes":self.max_local_bytes,
                "onPressure":"stop-new-acquisition-preserve-all-passing-units",
                "stagingUnavailable":"retain-local-manuscripts","drain":"oldest-verified-first"}
    def _load(self):
        if not self.path.exists(): return {"schemaVersion":1,"nextSequence":1,"units":[],"policy":self.policy()}
        value=json.loads(self.path.read_text()); ids=[x["unitId"] for x in value.get("units",[])]
        if value.get("schemaVersion") != 1 or len(ids) != len(set(ids)): raise OverflowIntegrityError("invalid index")
        return value
    def _save(self): self.state["policy"]=self.policy(); atomic(self.path,self.state)
    def units(self): return [dict(x) for x in sorted(self.state["units"],key=lambda x:x["sequence"])]
    def pressure(self,incoming=0):
        local=sum(x.get("localManuscriptBytes",0) for x in self.state["units"] if x.get("localPayloadPresent"))
        return {"blocked":len(self.state["units"])>=self.max_units or local+incoming>self.max_local_bytes,
                "queuedUnits":len(self.state["units"]),"localBytes":local,"incomingLocalBytes":incoming,**self.policy()}
    def enqueue(self,unit_id,root,exact,review_receipt,validation_receipt,source_routes_receipt):
        root=Path(root).resolve(); members=sorted(exact.get("bookIds") or []); files=sorted((root/"manuscripts").glob("*.json"))
        if not members or [x.stem for x in files] != members: raise OverflowIntegrityError("membership mismatch")
        if sha(root/"catalog.json") != exact.get("catalogSha256"): raise OverflowIntegrityError("catalog changed")
        receipts=[Path(review_receipt),Path(validation_receipt),Path(source_routes_receipt)]
        if not all(x.is_file() for x in receipts): raise OverflowIntegrityError("required receipt missing")
        binding={"unitId":unit_id,"root":str(root),"itemCount":len(members),"memberIds":members,
                 **{k:exact[k] for k in ("catalogSha256","manuscriptSetSha256","membershipSha256")}}
        old=next((x for x in self.state["units"] if x["unitId"]==unit_id),None)
        if old:
            if old["bindingSha256"] != digest(binding): raise OverflowIntegrityError("unit binding changed")
            return dict(old)
        size=sum(x.stat().st_size for x in files); pressure=self.pressure(size)
        if pressure["blocked"]: raise OverflowPressure(canonical(pressure))
        row={**binding,"bindingSha256":digest(binding),"sequence":self.state["nextSequence"],
             "state":"local-pending-staging","localPayloadPresent":True,"localManuscriptBytes":size,
             "reviewReceipt":str(receipts[0].resolve()),"reviewReceiptSha256":sha(receipts[0]),
             "validationReceipt":str(receipts[1].resolve()),"validationReceiptSha256":sha(receipts[1]),
             "sourceRoutesReceipt":str(receipts[2].resolve()),"sourceRoutesReceiptSha256":sha(receipts[2]),
             "stagingReceipt":None,"failure":None}
        self.state["nextSequence"]+=1; self.state["units"].append(row); self._save(); return dict(row)
    def _find(self,unit_id):
        for row in self.state["units"]:
            if row["unitId"]==unit_id:return row
        raise KeyError(unit_id)
    def staging_unavailable(self,unit_id,reason):
        row=self._find(unit_id); row["state"]="local-staging-unavailable"; row["failure"]={"station":"staging","reason":reason}; self._save(); return dict(row)
    def staging_verified(self,unit_id,receipt_path):
        row=self._find(unit_id); path=Path(receipt_path); receipt=json.loads(path.read_text())
        for key in ("itemCount","memberIds","catalogSha256","manuscriptSetSha256","membershipSha256"):
            if receipt.get(key)!=row.get(key): raise OverflowIntegrityError("staging differs on "+key)
        if receipt.get("verified") is not True or receipt.get("byteIdenticalToValidatedLocalArtifacts") is not True: raise OverflowIntegrityError("staging unverified")
        row.update(state="staging-verified",stagingReceipt=str(path.resolve()),stagingReceiptSha256=sha(path),failure=None); self._save(); return dict(row)
    def cleanup_verified(self,unit_id):
        row=self._find(unit_id)
        if row["state"]!="staging-verified": raise OverflowIntegrityError("verified staging required")
        paths=[Path(row["root"])/"manuscripts"/(x+".json") for x in row["memberIds"]]
        if not all(x.is_file() for x in paths): raise OverflowIntegrityError("payload missing before cleanup")
        removed=[{"id":x.stem,"sha256":sha(x),"bytes":x.stat().st_size} for x in paths]
        for path in paths:path.unlink()
        recovery=Path(row["root"])/"overflow_recovery_log.json"; atomic(recovery,{"unitId":unit_id,"removed":removed,"recoverFrom":"verified-firebase-staging","stagingReceipt":row["stagingReceipt"]})
        row.update(state="staging-verified-local-cleaned",localPayloadPresent=False,localManuscriptBytes=0,recoveryLog=str(recovery),recoveryLogSha256=sha(recovery)); self._save(); return dict(row)
    def oldest_verified(self): return next((x for x in self.units() if x["state"].startswith("staging-verified")),None)
    def isolate_uploader_failure(self,unit_id,reason):
        row=self._find(unit_id); row["state"]="uploader-failed-isolated"; row["failure"]={"station":"production-uploader","reason":reason}; self._save(); return dict(row)
