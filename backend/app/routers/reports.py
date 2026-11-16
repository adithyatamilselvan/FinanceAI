from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse

from ..config import get_settings
from ..db import get_db
from ..deps import CurrentUser
from ..services import analytics as an
from ..services import reports as rp
from ..util import PERIOD_RE

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/monthly")
def monthly(user: CurrentUser, db=Depends(get_db), period: str | None = Query(None, pattern=PERIOD_RE)):
    return rp.monthly_report(db, user, period or an.default_period(db, user["_id"]))


@router.get("/monthly.pdf")
def monthly_pdf(user: CurrentUser, db=Depends(get_db), period: str | None = Query(None, pattern=PERIOD_RE)):
    rep = rp.monthly_report(db, user, period or an.default_period(db, user["_id"]))
    return Response(rp.pdf_bytes(rep), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="report-{rep["period"]}.pdf"'})


@router.get("/transactions.csv")
def transactions_csv(user: CurrentUser, db=Depends(get_db), date_from: date | None = None, date_to: date | None = None):
    gen = rp.csv_stream(db, user["_id"], date_from, date_to, get_settings().max_export_rows)
    return StreamingResponse(gen, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="transactions.csv"'})


@router.get("/transactions.xlsx")
def transactions_xlsx(user: CurrentUser, db=Depends(get_db), date_from: date | None = None, date_to: date | None = None):
    data = rp.excel_bytes(db, user["_id"], date_from, date_to, get_settings().max_export_rows)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="transactions.xlsx"'})
