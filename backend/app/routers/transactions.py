import re
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import get_db
from ..deps import CurrentUser
from ..schemas import TransactionIn, TransactionUpdate
from ..services import analytics as an
from ..services import categorizer as cz
from ..util import clean_text, day_start, merchant_key, normalize_description, now_utc, to_oid

router = APIRouter(prefix="/transactions", tags=["transactions"])


def tx_out(d: dict) -> dict:
    return {
        "id": str(d["_id"]), "date": d["date"].date().isoformat(), "description": d["description"], "amount": d["amount"],
        "type": d["type"], "category": d["category"], "notes": d.get("notes"), "source": d.get("source", "manual"),
        "import_id": str(d["import_id"]) if d.get("import_id") else None, "currency": d.get("currency", "INR"),
        "created_at": d["created_at"].isoformat(), "slip": d.get("slip"),
    }


def _validate_category(db, uid, category: str, kind: str) -> str:
    if kind == "transfer":
        return "Transfer"
    meta = an.category_meta(db, uid)
    want = "income" if kind == "income" else "expense"
    c = next((k for k in meta if k.lower() == category.lower()), None)
    if c is None or meta[c]["type"] != want:
        raise HTTPException(status_code=422, detail=f"Unknown {want} category '{category}'")
    return c


@router.get("")
def list_transactions(
    user: CurrentUser, db=Depends(get_db),
    page: int = Query(1, ge=1, le=100000), page_size: int = Query(25, ge=1, le=100),
    q: str | None = Query(None, max_length=100), category: str | None = None, type: str | None = Query(None, pattern="^(income|expense|refund|transfer)$"),
    date_from: date | None = None, date_to: date | None = None,
    min_amount: int | None = Query(None, ge=0), max_amount: int | None = Query(None, ge=0),
    sort: str = Query("date_desc", pattern="^(date_desc|date_asc|amount_desc|amount_asc)$"),
):
    f: dict = {"user_id": user["_id"]}
    if q:
        f["normalized_description"] = {"$regex": re.escape(normalize_description(q)) or re.escape(q.lower())}
    if category:
        f["category"] = category
    if type:
        f["type"] = type
    if date_from or date_to:
        f["date"] = {}
        if date_from:
            f["date"]["$gte"] = day_start(date_from)
        if date_to:
            f["date"]["$lt"] = day_start(date_to) + timedelta(days=1)
    if min_amount is not None or max_amount is not None:
        f["amount"] = {}
        if min_amount is not None:
            f["amount"]["$gte"] = min_amount
        if max_amount is not None:
            f["amount"]["$lte"] = max_amount
    order = {"date_desc": [("date", -1), ("_id", -1)], "date_asc": [("date", 1), ("_id", 1)],
             "amount_desc": [("amount", -1), ("_id", -1)], "amount_asc": [("amount", 1), ("_id", 1)]}[sort]
    total = db.transactions.count_documents(f)
    cur = db.transactions.find(f).sort(order).skip((page - 1) * page_size).limit(page_size)
    return {"items": [tx_out(d) for d in cur], "total": total, "page": page, "page_size": page_size}


@router.post("", status_code=201)
def create_transaction(body: TransactionIn, user: CurrentUser, db=Depends(get_db)):
    desc = clean_text(body.description)
    if not desc:
        raise HTTPException(status_code=422, detail="Description is required")
    rules = cz.load_rules(db, user["_id"])
    if body.category:
        category = _validate_category(db, user["_id"], body.category, body.type)
    else:
        category, _ = cz.categorize(desc, body.type, rules)
    doc = {
        "user_id": user["_id"], "date": day_start(body.date), "description": desc,
        "normalized_description": normalize_description(desc), "merchant_key": merchant_key(desc),
        "amount": body.amount, "type": body.type, "category": category, "notes": clean_text(body.notes, 500) or None,
        "source": "manual", "fingerprint": f"manual:{uuid.uuid4().hex}", "currency": user.get("currency", "INR"),
        "created_at": now_utc(), "updated_at": now_utc(),
    }
    doc["_id"] = db.transactions.insert_one(doc).inserted_id
    return tx_out(doc)


def _get_own(db, uid, tx_id: str) -> dict:
    d = db.transactions.find_one({"_id": to_oid(tx_id, "Transaction"), "user_id": uid})
    if not d:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return d


@router.get("/{tx_id}")
def get_transaction(tx_id: str, user: CurrentUser, db=Depends(get_db)):
    return tx_out(_get_own(db, user["_id"], tx_id))


@router.patch("/{tx_id}")
def update_transaction(tx_id: str, body: TransactionUpdate, user: CurrentUser, db=Depends(get_db)):
    uid = user["_id"]
    cur = _get_own(db, uid, tx_id)
    ch = body.model_dump(exclude_unset=True, exclude={"remember", "apply_to_similar"})
    new_type = ch.get("type", cur["type"])
    sets: dict = {}
    if "date" in ch and ch["date"]:
        sets["date"] = day_start(ch["date"])
    if "description" in ch:
        desc = clean_text(ch["description"])
        if not desc:
            raise HTTPException(status_code=422, detail="Description is required")
        sets.update(description=desc, normalized_description=normalize_description(desc), merchant_key=merchant_key(desc))
    if "amount" in ch and ch["amount"] is not None:
        sets["amount"] = ch["amount"]
    if "notes" in ch:
        sets["notes"] = clean_text(ch["notes"], 500) or None
    if "type" in ch:
        sets["type"] = new_type
    if ch.get("category"):
        sets["category"] = _validate_category(db, uid, ch["category"], new_type)
    elif "type" in ch and new_type != cur["type"]:
        sets["category"], _ = cz.categorize(sets.get("description", cur["description"]), new_type, cz.load_rules(db, uid))
    if not sets:
        return tx_out(cur)
    sets["updated_at"] = now_utc()
    db.transactions.update_one({"_id": cur["_id"], "user_id": uid}, {"$set": sets})
    updated = db.transactions.find_one({"_id": cur["_id"]})
    similar = 0
    if ch.get("category") and body.remember and updated["type"] != "transfer":
        key = cz.learn_rule_key(updated["description"])
        if key:
            db.categories.update_one({"user_id": uid, "kind": "rule", "keyword": key},
                                     {"$set": {"category": updated["category"], "updated_at": now_utc()}, "$setOnInsert": {"created_at": now_utc()}}, upsert=True)
            if body.apply_to_similar:
                similar = db.transactions.update_many(
                    {"user_id": uid, "merchant_key": key, "type": updated["type"], "_id": {"$ne": updated["_id"]}},
                    {"$set": {"category": updated["category"], "updated_at": now_utc()}}).modified_count
    out = tx_out(updated)
    out["similar_updated"] = similar
    return out


@router.delete("/{tx_id}", status_code=204)
def delete_transaction(tx_id: str, user: CurrentUser, db=Depends(get_db)):
    res = db.transactions.delete_one({"_id": to_oid(tx_id, "Transaction"), "user_id": user["_id"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Transaction not found")
