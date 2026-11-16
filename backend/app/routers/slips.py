"""Payment Slip Scanner API. Flow: create batch -> upload files one by one (OCR) -> review/edit -> download workbook -> confirm import."""
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ..config import get_settings
from ..db import get_db
from ..deps import CurrentUser
from ..schemas import SlipCommit, SlipItemUpdate
from ..security import auth_limiter
from ..services import analytics as an
from ..services import categorizer as cz
from ..services import ocr
from ..services import slips as sl
from ..util import clean_text, now_utc, to_oid

router = APIRouter(prefix="/slips", tags=["payment slips"])
log = logging.getLogger("financeai.slips")


def _batch(db, uid, batch_id: str) -> dict:
    b = db.slip_batches.find_one({"_id": to_oid(batch_id, "Batch"), "user_id": uid})
    if not b:
        raise HTTPException(status_code=404, detail="Batch not found")
    if b["expires_at"] < now_utc():
        db.slip_batches.delete_one({"_id": b["_id"]})
        raise HTTPException(status_code=404, detail="This batch has expired and was deleted. Upload the slips again.")
    return b


def _batch_out(b: dict) -> dict:
    return {"id": str(b["_id"]), "status": b["status"], "created_at": b["created_at"].isoformat(), "expires_at": b["expires_at"].isoformat(),
            "items": [sl.public_item(i) for i in b["items"]], "summary": sl.summarize(b["items"]), "result": b.get("result")}


@router.get("/status")
def status(user: CurrentUser):
    """Is OCR available on this server, and what are the limits? (cheap: does not run OCR)"""
    st = ocr.ocr_status()
    s = get_settings()
    st["limits"]["batch_expiry_hours"] = s.slip_batch_ttl_hours
    st["limits"]["low_confidence_below"] = s.slip_low_confidence
    return st


@router.post("/batches", status_code=201)
def create_batch(user: CurrentUser, db=Depends(get_db)):
    s = get_settings()
    uid = user["_id"]
    sl.cleanup_expired(db)
    open_batches = list(db.slip_batches.find({"user_id": uid}, projection={"_id": 1}).sort("created_at", 1))
    for old in open_batches[: max(0, len(open_batches) - s.slip_max_active_batches_per_user + 1)]:
        db.slip_batches.delete_one({"_id": old["_id"]})  # keep storage bounded: oldest unfinished batches are discarded
    now = now_utc()
    doc = {"user_id": uid, "status": "open", "items": [], "created_at": now, "expires_at": now + timedelta(hours=s.slip_batch_ttl_hours)}
    doc["_id"] = db.slip_batches.insert_one(doc).inserted_id
    return {"id": str(doc["_id"]), "expires_at": doc["expires_at"].isoformat(), "limits": ocr.ocr_status()["limits"]}


@router.post("/batches/{batch_id}/files")
def upload_file(batch_id: str, user: CurrentUser, db=Depends(get_db), file: UploadFile = File(...)):
    """Process ONE file (OCR) and add it to the batch. The client sends files sequentially, which bounds memory and CPU."""
    s = get_settings()
    uid = user["_id"]
    if not auth_limiter.allow(f"slip:{uid}", 120):
        raise HTTPException(status_code=429, detail="Too many uploads. Please wait a minute.")
    b = _batch(db, uid, batch_id)
    if b["status"] != "open":
        raise HTTPException(status_code=409, detail="This batch was already imported. Start a new batch.")
    if len(b["items"]) >= s.slip_max_files_per_batch:
        raise HTTPException(status_code=409, detail=f"A batch can hold at most {s.slip_max_files_per_batch} files.")
    data = file.file.read(s.slip_max_file_bytes + 1)  # never read more than the limit + 1 byte
    try:
        item = sl.process_file(file.filename or "upload", file.content_type, data)
    finally:
        del data
    items = b["items"] + [item]
    sl.evaluate(db, uid, items)
    db.slip_batches.update_one({"_id": b["_id"], "status": "open"}, {"$set": {"items": items}})
    log.info("slip processed ok=%s method=%s", item["ok"], item["method"])  # no filenames, text or amounts in logs
    return {"item": sl.public_item(next(i for i in items if i["id"] == item["id"])), "summary": sl.summarize(items)}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, user: CurrentUser, db=Depends(get_db)):
    return _batch_out(_batch(db, user["_id"], batch_id))


@router.get("/batches/{batch_id}/items/{item_id}/text")
def item_text(batch_id: str, item_id: str, user: CurrentUser, db=Depends(get_db)):
    """The raw text read from the slip, for comparing against the extracted fields."""
    b = _batch(db, user["_id"], batch_id)
    it = next((i for i in b["items"] if i["id"] == item_id), None)
    if not it:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"file": it["file"], "method": it["method"], "text": it.get("raw_text", "")}


@router.patch("/batches/{batch_id}/items/{item_id}")
def edit_item(batch_id: str, item_id: str, body: SlipItemUpdate, user: CurrentUser, db=Depends(get_db)):
    uid = user["_id"]
    b = _batch(db, uid, batch_id)
    if b["status"] != "open":
        raise HTTPException(status_code=409, detail="This batch was already imported.")
    items = b["items"]
    it = next((i for i in items if i["id"] == item_id), None)
    if not it or not it["ok"]:
        raise HTTPException(status_code=404, detail="Item not found")
    if it.get("transaction_id"):
        raise HTTPException(status_code=409, detail="This slip was already imported.")
    ch = body.model_dump(exclude_unset=True)
    verified = ch.pop("verified", None)
    category = ch.pop("category", "__unset__")
    for k, v in ch.items():
        if k == "date":
            v = v.isoformat() if v else None
        elif isinstance(v, str):
            v = clean_text(v, 200) or None
        it["fields"][k] = v
        it["uncertain"].pop(k, None)
        it["notes"].pop(k, None)
        if k not in it["edited"]:
            it["edited"].append(k)
    if category != "__unset__":
        if category:
            meta = an.category_meta(db, uid)
            name = next((n for n in meta if n.lower() == category.lower()), None)
            if name is None:
                raise HTTPException(status_code=422, detail=f"Unknown category '{category}'")
            it["category"] = name
        else:
            it["category"] = None
        if "category" not in it["edited"]:
            it["edited"].append("category")
    it["verified"] = bool(verified) if verified is not None else (True if ch else it["verified"])
    sl.evaluate(db, uid, items)
    db.slip_batches.update_one({"_id": b["_id"], "status": "open"}, {"$set": {"items": items}})
    return {"item": sl.public_item(next(i for i in items if i["id"] == item_id)), "summary": sl.summarize(items)}


@router.get("/batches/{batch_id}/workbook.xlsx")
def workbook(batch_id: str, user: CurrentUser, db=Depends(get_db)):
    """Excel workbook generated in memory from this user's batch only (no shared or stored workbook)."""
    b = _batch(db, user["_id"], batch_id)
    if not b["items"]:
        raise HTTPException(status_code=409, detail="Upload at least one slip first.")
    data = sl.build_workbook(b)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="payment-slips-{str(b["_id"])[-6:]}.xlsx"'})


@router.post("/batches/{batch_id}/commit")
def commit(batch_id: str, body: SlipCommit, user: CurrentUser, db=Depends(get_db)):
    """Save the user's confirmed selection into `transactions`. All-or-nothing validation first, then insert."""
    uid = user["_id"]
    b = _batch(db, uid, batch_id)
    # Atomically claim the batch (open -> importing) so two simultaneous commits (double click, two tabs) cannot both run.
    claimed = db.slip_batches.find_one_and_update({"_id": b["_id"], "user_id": uid, "status": "open"}, {"$set": {"status": "importing"}}, return_document=ReturnDocument.AFTER)
    if not claimed:
        raise HTTPException(status_code=409, detail="This batch was already imported (or is being imported right now).")
    try:
        return _commit_claimed(db, uid, claimed, body, user)
    except BaseException:
        db.slip_batches.update_one({"_id": b["_id"], "status": "importing"}, {"$set": {"status": "open"}})  # nothing finished: let the user retry
        raise


def _commit_claimed(db, uid, b: dict, body: SlipCommit, user: dict) -> dict:
    items = b["items"]
    by_id = {i["id"]: i for i in items}
    chosen = []
    for iid in dict.fromkeys(body.item_ids):
        if iid not in by_id:
            raise HTTPException(status_code=404, detail="One of the selected items does not exist in this batch.")
        chosen.append(by_id[iid])
    problems = [{"id": i["id"], "file": i["file"], "reasons": r} for i in chosen if (r := sl.blockers(i, body.allow_non_successful, body.allow_possible_duplicates))]
    if problems:
        raise HTTPException(status_code=422, detail={"message": f"{len(problems)} selected slip(s) cannot be imported yet. Nothing was saved.", "items": problems})
    rules = cz.load_rules(db, uid)
    imported, skipped = [], []
    for it in chosen:
        doc = sl.transaction_doc(uid, b["_id"], it, user, rules)
        try:
            doc["_id"] = db.transactions.insert_one(doc).inserted_id
        except DuplicateKeyError:  # same fingerprint already saved (e.g. identical slip in the same click)
            it["duplicate"] = {"kind": "already_imported", "level": "strong", "detail": "Already in your transactions"}
            skipped.append(it["id"])
            continue
        it["transaction_id"] = str(doc["_id"])
        imported.append(str(doc["_id"]))
    for it in items:
        if it["id"] not in {c["id"] for c in chosen}:
            it["skipped"] = True
    sl.evaluate(db, uid, items)
    for it in items:
        it["raw_text"] = ""  # the OCR text is no longer needed once the user has confirmed
    result = {"imported": len(imported), "skipped_duplicates": len(skipped), "not_selected": sum(1 for i in items if i.get("skipped")), "transaction_ids": imported}
    db.slip_batches.update_one({"_id": b["_id"]}, {"$set": {"items": items, "status": "imported", "result": result, "imported_at": now_utc()}})
    return result


@router.delete("/batches/{batch_id}", status_code=204)
def cancel_batch(batch_id: str, user: CurrentUser, db=Depends(get_db)):
    """Cancel: delete the batch and everything extracted from it."""
    res = db.slip_batches.delete_one({"_id": to_oid(batch_id, "Batch"), "user_id": user["_id"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Batch not found")

