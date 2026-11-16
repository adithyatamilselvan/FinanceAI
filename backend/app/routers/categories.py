from fastapi import APIRouter, Depends, HTTPException

from ..db import get_db
from ..deps import CurrentUser
from ..schemas import CategoryIn
from ..services import analytics as an
from ..util import clean_text, now_utc, to_oid

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("")
def list_categories(user: CurrentUser, db=Depends(get_db)):
    meta = an.category_meta(db, user["_id"])
    return {"categories": [{"name": n, **m} for n, m in meta.items()]}


@router.post("", status_code=201)
def create_category(body: CategoryIn, user: CurrentUser, db=Depends(get_db)):
    name = clean_text(body.name, 40)
    meta = an.category_meta(db, user["_id"])
    if not name or name.lower() in {k.lower() for k in meta} | {"transfer"}:
        raise HTTPException(status_code=409, detail="A category with that name already exists")
    db.categories.insert_one({"user_id": user["_id"], "kind": "category", "name": name, "type": body.type,
                              "fixed": body.fixed, "essential": body.essential, "created_at": now_utc()})
    return {"name": name, "type": body.type, "fixed": body.fixed, "essential": body.essential, "custom": True}


@router.delete("/{name}", status_code=204)
def delete_category(name: str, user: CurrentUser, db=Depends(get_db)):
    uid = user["_id"]
    doc = db.categories.find_one({"user_id": uid, "kind": "category", "name": name})
    if not doc:
        raise HTTPException(status_code=404, detail="Custom category not found")
    if db.transactions.count_documents({"user_id": uid, "category": name}, limit=1):
        raise HTTPException(status_code=409, detail="Category is used by transactions; recategorise them first")
    db.categories.delete_one({"_id": doc["_id"]})
    db.categories.delete_many({"user_id": uid, "kind": "rule", "category": name})


@router.get("/rules")
def list_rules(user: CurrentUser, db=Depends(get_db)):
    return {"rules": [{"id": str(r["_id"]), "keyword": r["keyword"], "category": r["category"]}
                      for r in db.categories.find({"user_id": user["_id"], "kind": "rule"}).sort("keyword", 1)]}


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: str, user: CurrentUser, db=Depends(get_db)):
    res = db.categories.delete_one({"_id": to_oid(rule_id, "Rule"), "user_id": user["_id"], "kind": "rule"})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Rule not found")
