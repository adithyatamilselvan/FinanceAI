import io
import json

from openpyxl import Workbook

from app.config import get_settings
from tests.conftest import API, csv_bytes, import_statement, make_statement


def preview(client, auth, content, filename="s.csv", mapping=None):
    data = {"mapping": json.dumps(mapping)} if mapping else None
    return client.post(f"{API}/imports/preview", headers=auth, files={"file": (filename, content, "application/octet-stream")}, data=data)


def test_csv_import_end_to_end_and_categories(client, auth):
    body, res = import_statement(client, auth, make_statement(4))
    s = body["summary"]
    assert (s["valid_rows"], s["new"], s["duplicates"], s["errors"]) == (28, 28, 0, 0)
    assert s["income_total"] == 4 * 10000000 and s["expense_total"] == sum(3814900 + 15000 * i for i in range(4))
    assert res["inserted"] == 28
    items = client.get(f"{API}/transactions", headers=auth, params={"page_size": 100}).json()["items"]
    cat = {t["description"]: t["category"] for t in items}
    assert cat["Salary ACME Corp"] == "Salary" and cat["House Rent Landlord"] == "Housing" and cat["Netflix Subscription"] == "Subscriptions"
    assert cat["BigBasket Groceries"] == "Groceries" and cat["Swiggy Order"] == "Food" and cat["Uber Ride"] == "Transport" and cat["PVR Cinemas"] == "Entertainment"
    assert {t["type"] for t in items if t["category"] == "Salary"} == {"income"}


def test_duplicate_import_prevented(client, auth):
    rows = make_statement(2)
    import_statement(client, auth, rows)
    r = preview(client, auth, csv_bytes(rows)).json()
    assert r["summary"]["duplicates"] == 14 and r["summary"]["new"] == 0 and r["already_imported"] is True
    c = client.post(f"{API}/imports/{r['import_id']}/commit", headers=auth, json={}).json()
    assert c["inserted"] == 0 and c["skipped_duplicates"] == 14
    assert client.get(f"{API}/transactions", headers=auth).json()["total"] == 14


def test_overlapping_statement_only_adds_new_rows(client, auth):
    import_statement(client, auth, make_statement(2, "2025-01"))
    _, res = import_statement(client, auth, make_statement(3, "2025-01"))
    assert res["inserted"] == 7 and res["skipped_duplicates"] == 14


def test_double_commit_rejected(client, auth):
    body, _ = import_statement(client, auth, make_statement(1))
    assert client.post(f"{API}/imports/{body['import_id']}/commit", headers=auth, json={}).status_code == 409


def test_identical_rows_in_one_file_both_kept_then_both_duplicates(client, auth):
    rows = ["2025-03-01,Coffee Shop,150.00,", "2025-03-01,Coffee Shop,150.00,"]
    _, res = import_statement(client, auth, rows)
    assert res["inserted"] == 2
    assert preview(client, auth, csv_bytes(rows)).json()["summary"]["duplicates"] == 2


def test_exclude_rows_on_commit(client, auth):
    r = preview(client, auth, csv_bytes(make_statement(1))).json()
    first_row = r["rows"][0]["row"]
    c = client.post(f"{API}/imports/{r['import_id']}/commit", headers=auth, json={"exclude_rows": [first_row]}).json()
    assert c["inserted"] == 6 and c["excluded"] == 1


def test_invalid_rows_reported_never_silently_imported(client, auth):
    rows = ["2025-03-01,Good Row,100.00,", "not-a-date,Bad Date,100.00,", "2025-03-02,Bad Amount,abc,", "2025-03-03,Both,10.00,10.00",
            "2025-03-04,,50.00,", "2025-03-05,Zero,0.00,", "2025-03-06,Neither,,"]
    r = preview(client, auth, csv_bytes(rows)).json()
    assert r["summary"]["valid_rows"] == 1 and r["summary"]["errors"] == 6
    reasons = " | ".join(e["reason"] for e in r["errors"])
    assert "date" in reasons.lower() and "amount" in reasons.lower() and "Both" in reasons and "description" in reasons.lower()
    c = client.post(f"{API}/imports/{r['import_id']}/commit", headers=auth, json={}).json()
    assert c["inserted"] == 1 and c["error_rows"] == 6


def test_all_rows_invalid_returns_422(client, auth):
    r = preview(client, auth, csv_bytes(["x,y,abc,"]))
    assert r.status_code == 422


def test_xlsx_import_with_preamble_and_signed_amount_column(client, auth):
    wb = Workbook()
    ws = wb.active
    ws.append(["Example Bank - Statement of Account"])
    ws.append(["Account: XXXX1234", "", ""])
    ws.append([])
    ws.append(["Txn Date", "Narration", "Amount"])
    from datetime import datetime

    ws.append([datetime(2025, 3, 1), "Salary ACME", 50000.5])
    ws.append([datetime(2025, 3, 2), "Zomato Order", -450.25])
    ws.append(["03/03/2025", "Electricity BESCOM", "(1,200.00)"])
    buf = io.BytesIO()
    wb.save(buf)
    r = preview(client, auth, buf.getvalue(), "stmt.xlsx").json()
    assert r["summary"]["valid_rows"] == 3 and r["summary"]["errors"] == 0, r
    rows = {x["description"]: x for x in r["rows"]}
    assert rows["Salary ACME"]["amount"] == 5000050 and rows["Salary ACME"]["type"] == "income"
    assert rows["Zomato Order"]["amount"] == 45025 and rows["Zomato Order"]["type"] == "expense" and rows["Zomato Order"]["category"] == "Food"
    assert rows["Electricity BESCOM"]["date"] == "2025-03-03" and rows["Electricity BESCOM"]["category"] == "Utilities"
    client.post(f"{API}/imports/{r['import_id']}/commit", headers=auth, json={})
    assert client.get(f"{API}/transactions", headers=auth).json()["total"] == 3


def test_dayfirst_dates_and_custom_mapping(client, auth):
    content = b"Booked,Payee,Money\n03/04/2025,Swiggy,-100.00\n13/04/2025,Salary Co,2000.00\n"
    r = preview(client, auth, content).json()
    assert r["needs_mapping"] is True and r["headers"] == ["Booked", "Payee", "Money"]
    m = {"date": "Booked", "description": "Payee", "amount": "Money", "dayfirst": True}
    ok = preview(client, auth, content, mapping=m).json()
    assert [x["date"] for x in ok["rows"]] == ["2025-04-03", "2025-04-13"]
    ok2 = preview(client, auth, content, mapping={**m, "dayfirst": False}).json()
    # ambiguous 03/04 follows the chosen order; an unambiguous 13/04 is still read correctly
    assert ok2["rows"][0]["date"] == "2025-03-04" and ok2["rows"][1]["date"] == "2025-04-13" and ok2["summary"]["errors"] == 0
    bad = preview(client, auth, content, mapping={**m, "date": "Nope"})
    assert bad.status_code == 422


def test_dr_cr_type_column(client, auth):
    content = b"Date,Details,Amount,Dr/Cr\n2025-05-01,Rent,5000.00,DR\n2025-05-02,Interest,10.00,CR\n"
    r = preview(client, auth, content).json()
    t = {x["description"]: x["type"] for x in r["rows"]}
    assert t == {"Rent": "expense", "Interest": "income"}


def test_refund_and_transfer_handling(client, auth):
    rows = ["2025-03-01,Amazon order,1000.00,", "2025-03-05,Refund Amazon order,,400.00",
            "2025-03-06,Self Transfer to own account,5000.00,", "2025-03-06,Transfer from savings neft,,5000.00",
            "2025-03-07,NEFT to Bob,200.00,", "2025-03-09,Salary,,9000.00"]
    r = preview(client, auth, csv_bytes(rows)).json()
    types = {x["description"]: x["type"] for x in r["rows"]}
    assert types["Refund Amazon order"] == "refund"
    assert types["Self Transfer to own account"] == "transfer" and types["Transfer from savings neft"] == "transfer"
    assert types["NEFT to Bob"] == "expense"
    client.post(f"{API}/imports/{r['import_id']}/commit", headers=auth, json={})
    d = client.get(f"{API}/analytics/monthly/2025-03", headers=auth).json()["totals"]
    assert d["income"] == 900000 and d["gross_expense"] == 120000 and d["refunds"] == 40000 and d["net_expense"] == 80000 and d["net_cash_flow"] == 820000


def test_invalid_files_rejected(client, auth):
    assert preview(client, auth, b"MZ\x90\x00binary", "evil.exe").status_code == 422
    assert preview(client, auth, b"a,b\n", "x.txt").status_code == 422
    assert preview(client, auth, b"PK\x03\x04notreallyxlsx", "x.xlsx").status_code == 422
    assert preview(client, auth, b"PK\x03\x04zipdisguised", "x.csv").status_code == 422
    assert preview(client, auth, b"", "x.csv").status_code == 400
    assert preview(client, auth, b"foo,bar\n1,2\n", "x.csv").json()["needs_mapping"] is True  # unknown headers: user maps columns
    assert preview(client, auth, b"foo\n1\n", "x.csv").status_code == 422  # not even two columns
    assert client.post(f"{API}/imports/preview", files={"file": ("a.csv", b"x")}).status_code == 401


def test_size_and_row_limits(client, auth, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "max_upload_bytes", 2000)
    assert preview(client, auth, b"Date,Description,Debit,Credit\n" + b"2025-01-01,x,1.00,\n" * 200).status_code == 413
    monkeypatch.setattr(s, "max_upload_bytes", 5 * 1024 * 1024)
    monkeypatch.setattr(s, "max_import_rows", 10)
    r = preview(client, auth, csv_bytes([f"2025-01-{d:02d},Shop {d},1.00," for d in range(1, 21)]))
    assert r.status_code == 422 and "10" in r.json()["detail"]


def test_large_import_within_limits(client, auth):
    n = 5000
    rows = [f"2024-{1 + i % 12:02d}-{1 + i % 28:02d},Merchant {i % 50} purchase ref{i},{10 + (i % 90)}.{i % 100:02d}," for i in range(n)]
    body, res = import_statement(client, auth, rows)
    assert body["summary"]["valid_rows"] == n and res["inserted"] == n
    assert client.get(f"{API}/transactions", headers=auth).json()["total"] == n
    r = client.get(f"{API}/imports/{body['import_id']}/rows")
    assert r.status_code == 401


def test_staged_rows_paging_and_discard(client, auth):
    r = preview(client, auth, csv_bytes(make_statement(2))).json()
    p = client.get(f"{API}/imports/{r['import_id']}/rows", headers=auth, params={"page_size": 5, "page": 2}).json()
    assert p["total"] == 14 and len(p["rows"]) == 5
    assert client.delete(f"{API}/imports/{r['import_id']}", headers=auth).status_code == 204
    assert client.get(f"{API}/imports/{r['import_id']}/rows", headers=auth).status_code == 404


def test_manual_transaction_crud(client, auth):
    r = client.post(f"{API}/transactions", headers=auth, json={"date": "2025-06-15", "description": "Dinner at Cafe", "amount": 85050, "type": "expense"})
    assert r.status_code == 201
    t = r.json()
    assert t["category"] == "Food" and t["amount"] == 85050 and t["source"] == "manual" and t["date"] == "2025-06-15"
    u = client.patch(f"{API}/transactions/{t['id']}", headers=auth, json={"amount": 90000, "notes": "with team", "category": "Entertainment"}).json()
    assert u["amount"] == 90000 and u["notes"] == "with team" and u["category"] == "Entertainment"
    assert client.get(f"{API}/transactions/{t['id']}", headers=auth).json()["amount"] == 90000
    assert client.delete(f"{API}/transactions/{t['id']}", headers=auth).status_code == 204
    assert client.get(f"{API}/transactions/{t['id']}", headers=auth).status_code == 404


def test_manual_transaction_validation(client, auth):
    base = {"date": "2025-06-15", "description": "X", "amount": 100, "type": "expense"}
    assert client.post(f"{API}/transactions", headers=auth, json={**base, "amount": 0}).status_code == 422
    assert client.post(f"{API}/transactions", headers=auth, json={**base, "amount": -5}).status_code == 422
    assert client.post(f"{API}/transactions", headers=auth, json={**base, "amount": 1.5}).status_code == 422
    assert client.post(f"{API}/transactions", headers=auth, json={**base, "type": "weird"}).status_code == 422
    assert client.post(f"{API}/transactions", headers=auth, json={**base, "date": "2025-13-45"}).status_code == 422
    assert client.post(f"{API}/transactions", headers=auth, json={**base, "category": "Nonexistent"}).status_code == 422
    assert client.post(f"{API}/transactions", headers=auth, json={**base, "type": "income", "category": "Housing"}).status_code == 422
    assert client.post(f"{API}/transactions", headers=auth, json={**base, "description": "   "}).status_code == 422
    assert client.get(f"{API}/transactions/not-an-id", headers=auth).status_code == 404


def test_category_correction_is_remembered(client, auth):
    t = client.post(f"{API}/transactions", headers=auth, json={"date": "2025-06-01", "description": "Quirky Bistro 123", "amount": 5000, "type": "expense"}).json()
    t2 = client.post(f"{API}/transactions", headers=auth, json={"date": "2025-06-02", "description": "Quirky Bistro 456", "amount": 6000, "type": "expense"}).json()
    assert t["category"] == "Other"
    u = client.patch(f"{API}/transactions/{t['id']}", headers=auth, json={"category": "Food", "apply_to_similar": True}).json()
    assert u["category"] == "Food" and u["similar_updated"] == 1
    assert client.get(f"{API}/transactions/{t2['id']}", headers=auth).json()["category"] == "Food"
    # a later import with the same merchant is categorised by the learned rule
    r = preview(client, auth, csv_bytes(["2025-07-01,Quirky Bistro 789,70.00,"])).json()
    assert r["rows"][0]["category"] == "Food"
    rules = client.get(f"{API}/categories/rules", headers=auth).json()["rules"]
    assert rules[0]["keyword"] == "quirky bistro" and rules[0]["category"] == "Food"
    assert client.delete(f"{API}/categories/rules/{rules[0]['id']}", headers=auth).status_code == 204


def test_custom_category(client, auth):
    assert client.post(f"{API}/categories", headers=auth, json={"name": "Pets", "type": "expense", "essential": True}).status_code == 201
    assert client.post(f"{API}/categories", headers=auth, json={"name": "pets"}).status_code == 409
    assert client.post(f"{API}/categories", headers=auth, json={"name": "Food"}).status_code == 409
    t = client.post(f"{API}/transactions", headers=auth, json={"date": "2025-06-01", "description": "Vet visit", "amount": 5000, "type": "expense", "category": "Pets"})
    assert t.status_code == 201 and t.json()["category"] == "Pets"
    assert client.delete(f"{API}/categories/Pets", headers=auth).status_code == 409  # in use
    client.delete(f"{API}/transactions/{t.json()['id']}", headers=auth)
    assert client.delete(f"{API}/categories/Pets", headers=auth).status_code == 204


def test_search_filter_pagination_sorting(client, auth):
    import_statement(client, auth, make_statement(4))
    g = lambda **p: client.get(f"{API}/transactions", headers=auth, params=p).json()  # noqa: E731
    assert g()["total"] == 28 and len(g(page_size=10)["items"]) == 10 and len(g(page_size=10, page=3)["items"]) == 8
    assert g(q="netflix")["total"] == 4
    assert g(category="Groceries")["total"] == 4 and g(type="income")["total"] == 4
    assert g(date_from="2025-02-01", date_to="2025-02-28")["total"] == 7
    assert g(date_from="2025-01-31", date_to="2025-02-01")["total"] == 1  # inclusive end date: only the 1st-Feb salary
    assert g(min_amount=3000000)["total"] == 4 + 4  # rent 30,000 and salary 100,000
    top = g(sort="amount_desc", page_size=1)["items"][0]
    assert top["amount"] == 10000000
    dates = [t["date"] for t in g(sort="date_asc", page_size=100)["items"]]
    assert dates == sorted(dates)
    assert client.get(f"{API}/transactions", headers=auth, params={"page_size": 1000}).status_code == 422
    assert client.get(f"{API}/transactions", headers=auth, params={"q": "(.*"}).status_code == 200  # regex chars are escaped
