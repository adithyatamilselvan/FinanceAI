import hashlib
import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pymongo.errors import BulkWriteError

from ..config import get_settings
from ..db import get_db
from ..deps import CurrentUser
from ..schemas import ColumnMapping, ImportCommit
from ..services import categorizer as cz
from ..services import importer as imp
from ..util import day_start, merchant_key, normalize_description, now_utc, to_oid

router = APIRouter(prefix="/imports", tags=["imports"])

PREVIEW_ROWS = 500


def _summary(rows: list[dict], errors: list[dict]) -> dict:
    new = [r for r in rows if r["status"] == "new"]
    return {
        "valid_rows": len(rows), "new": len(new), "duplicates": len(rows) - len(new), "errors": len(errors),
        "income_total": sum(r["amount"] for r in new if r["type"] == "income"),
        "expense_total": sum(r["amount"] for r in new if r["type"] == "expense"),
        "refund_total": sum(r["amount"] for r in new if r["type"] == "refund"),
        "transfers": sum(1 for r in new if r["type"] == "transfer"),
    }


def _mark_duplicates(db, uid, rows: list[dict]) -> None:
    existing: set[str] = set()
    fps = [r["fingerprint"] for r in rows]
    for i in range(0, len(fps), 1000):
        for d in db.transactions.find({"user_id": uid, "fingerprint": {"$in": fps[i:i + 1000]}}, projection={"fingerprint": 1}):
            existing.add(d["fingerprint"])
    for r in rows:
        r["status"] = "duplicate" if r["fingerprint"] in existing else "new"


@router.post("/preview")
def preview_import(
    user: CurrentUser, db=Depends(get_db), file: UploadFile = File(...),
    mapping: str | None = Form(None, max_length=2000),
):
    s = get_settings()
    uid = user["_id"]
    data = file.file.read(s.max_upload_bytes + 1)
    if len(data) > s.max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"File too large (limit {s.max_upload_bytes // (1024 * 1024)} MB)")
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")
    try:
        rows = imp.read_rows(file.filename or "", data, s.max_import_rows)
        header_idx, headers, recognised = imp.detect_header(rows)
        user_map: ColumnMapping | None = None
        if mapping:
            try:
                user_map = ColumnMapping.model_validate(json.loads(mapping))
            except (ValueError, TypeError) as e:
                raise HTTPException(status_code=422, detail="Invalid column mapping") from e
        suggested = imp.suggest_mapping(headers)
        sample = rows[header_idx + 1: header_idx + 6]
        if user_map is None:
            ok = recognised and "date" in suggested and "description" in suggested and ("amount" in suggested or "debit" in suggested or "credit" in suggested)
            if not ok:
                return {"needs_mapping": True, "headers": headers, "suggested_mapping": suggested, "sample_rows": sample, "import_id": None}
            m = dict(suggested)
            dayfirst = True
        else:
            m = user_map.model_dump(exclude_none=True)
            dayfirst = m.pop("dayfirst", True)
        result = imp.normalize(rows, header_idx, m, user.get("currency", "INR"), dayfirst, s.max_import_rows)
    except imp.ImportError_ as e:
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        del data  # release the upload bytes promptly
    good = result["rows"]
    if not good and result["errors"]:
        raise HTTPException(status_code=422, detail={"message": "No valid transactions found", "errors": result["errors"][:20]})
    imp.pair_internal_transfers(good)
    imp.categorize_rows(good, cz.load_rules(db, uid))
    _mark_duplicates(db, uid, good)
    del rows
    file_hash = hashlib.sha256(json.dumps([r["fingerprint"] for r in good]).encode()).hexdigest()
    summary = _summary(good, result["errors"])
    now = now_utc()
    doc = {
        "user_id": uid, "status": "staged", "filename": (file.filename or "upload")[:120], "content_hash": file_hash,
        "mapping": {**m, "dayfirst": dayfirst}, "rows": good, "errors": result["errors"][:500], "summary": summary,
        "created_at": now, "expires_at": now + timedelta(hours=s.import_preview_ttl_hours),
    }
    already = db.bank_imports.find_one({"user_id": uid, "status": "committed", "content_hash": file_hash}, projection={"_id": 1})
    doc["_id"] = db.bank_imports.insert_one(doc).inserted_id
    return {
        "needs_mapping": False, "import_id": str(doc["_id"]), "headers": headers, "mapping": doc["mapping"], "summary": summary,
        "already_imported": already is not None, "rows": good[:PREVIEW_ROWS], "rows_truncated": len(good) > PREVIEW_ROWS,
        "errors": result["errors"][:100],
    }


def _staged(db, uid, import_id: str) -> dict:
    d = db.bank_imports.find_one({"_id": to_oid(import_id, "Import"), "user_id": uid})
    if not d:
        raise HTTPException(status_code=404, detail="Import not found")
    return d


@router.get("/{import_id}/rows")
def staged_rows(import_id: str, user: CurrentUser, db=Depends(get_db), page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=500)):
    d = _staged(db, user["_id"], import_id)
    if d["status"] != "staged":
        raise HTTPException(status_code=409, detail="This import is no longer staged")
    rows = d["rows"]
    return {"rows": rows[(page - 1) * page_size: page * page_size], "total": len(rows), "page": page, "page_size": page_size}


@router.post("/{import_id}/commit")
def commit_import(import_id: str, body: ImportCommit, user: CurrentUser, db=Depends(get_db)):
    uid = user["_id"]
    d = _staged(db, uid, import_id)
    claimed = db.bank_imports.update_one({"_id": d["_id"], "user_id": uid, "status": "staged"}, {"$set": {"status": "committing"}})
    if claimed.modified_count != 1:
        raise HTTPException(status_code=409, detail="This import was already committed or is being processed")
    try:
        excluded = set(body.exclude_rows)
        now = now_utc()
        docs = []
        skipped_dup = 0
        for r in d["rows"]:
            if r["row"] in excluded:
                continue
            if r["status"] == "duplicate":
                skipped_dup += 1
                continue
            docs.append({
                "user_id": uid, "date": day_start(date.fromisoformat(r["date"])), "description": r["description"],
                "normalized_description": normalize_description(r["description"]), "merchant_key": merchant_key(r["description"]),
                "amount": r["amount"], "type": r["type"], "category": r["category"], "notes": None, "source": "import",
                "import_id": d["_id"], "fingerprint": r["fingerprint"], "currency": r.get("currency", "INR"),
                "created_at": now, "updated_at": now,
            })
        inserted = 0
        for i in range(0, len(docs), 500):
            batch = docs[i:i + 500]
            try:
                inserted += len(db.transactions.insert_many(batch, ordered=False).inserted_ids)
            except BulkWriteError as e:
                dup_only = all(err.get("code") == 11000 for err in e.details.get("writeErrors", []))
                if not dup_only:
                    raise
                inserted += e.details.get("nInserted", 0)
                skipped_dup += len(e.details.get("writeErrors", []))
    except Exception:
        db.bank_imports.update_one({"_id": d["_id"], "status": "committing"}, {"$set": {"status": "staged"}})
        raise
    result = {"inserted": inserted, "skipped_duplicates": skipped_dup, "excluded": len(excluded), "error_rows": len(d.get("errors", []))}
    db.bank_imports.update_one(
        {"_id": d["_id"]},
        {"$set": {"status": "committed", "committed_at": now_utc(), "result": result}, "$unset": {"rows": "", "expires_at": ""}})
    return {"import_id": str(d["_id"]), **result}


@router.get("")
def import_history(user: CurrentUser, db=Depends(get_db)):
    cur = db.bank_imports.find({"user_id": user["_id"], "status": {"$in": ["committed", "staged"]}},
                               projection={"rows": 0, "errors": 0}).sort("created_at", -1).limit(50)
    return {"items": [{"id": str(d["_id"]), "filename": d["filename"], "status": d["status"], "created_at": d["created_at"].isoformat(),
                       "summary": d.get("summary"), "result": d.get("result")} for d in cur]}


@router.delete("/{import_id}", status_code=204)
def discard_import(import_id: str, user: CurrentUser, db=Depends(get_db)):
    d = _staged(db, user["_id"], import_id)
    if d["status"] != "staged":
        raise HTTPException(status_code=409, detail="Only staged imports can be discarded")
    db.bank_imports.delete_one({"_id": d["_id"]})
