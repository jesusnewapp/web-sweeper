from __future__ import annotations

import hashlib, json, os
from datetime import datetime, timezone
from pathlib import Path
from .activity import record as activity_record


def now() -> str: return datetime.now(timezone.utc).isoformat()


def preserve(root: Path, items: dict[str, str], stage: str, threshold: int = 30) -> dict:
    canonical=json.dumps(items,sort_keys=True,separators=(",",":"))
    digest=hashlib.sha256(canonical.encode()).hexdigest(); count=len(items)
    base=100 if count>=1000 else 95 if count>=300 else 85 if count>=100 else 70 if count>=50 else 55 if count>=threshold else 20
    bonus={"accepted":0,"reviewed":15,"validated":25,"staged":35,"upload-ready":45,"live-verified":50}.get(stage,0)
    result={"schemaVersion":2,"collectionId":"nurture-"+digest[:16],"members":count,
        "membershipSha256":digest,"lifecycleStage":stage,"threshold":threshold,
        "active":count>=threshold,"nurtureIntensityPercent":min(100,base+bonus),
        "tertiaryAuthority":"none","advisory":False,"executionCoupling":False,
        "singleItemNeverBlocksContinuation":True,
        "failedItemAction":"quarantine-and-bookkeep",
        "validSurvivorAction":"preserve-and-advance",
        "neverBypasses":["rights","quality","hash-membership","validation","writer-serialization","verification"]}
    if count<threshold:return result
    directory=root/"nurture"; directory.mkdir(parents=True,exist_ok=True)
    snapshot=directory/f"collection-{digest}.json"; payload=json.dumps({**result,"items":items},indent=2)+"\n"
    if not snapshot.exists():
        descriptor=os.open(snapshot,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o444)
        try:os.write(descriptor,payload.encode());os.fsync(descriptor)
        finally:os.close(descriptor)
    result["snapshot"]=str(snapshot); return result
