"""Regenerate the JSON fixtures in this folder.

Run from the repo root:  python3 tests/fixtures/generate_fixtures.py

- open_payables_sample.json: payable_data.json rebuilt in the live API shape
  (client/designer/separator objects, approved_at, completed_at null).
- paid_payables_sample.json: 8 Completed designs (Contract A) split into two
  Laravel pagination envelopes: {"pages": [page1, page2]}.
- ui_fixture.json: {"open": [...], "paid": [...]} for MW_FIXTURE_PATH.
"""
import json
import os
from datetime import datetime, timedelta, timezone

ISO = "%Y-%m-%dT%H:%M:%S.000000Z"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def parse(s):
    return datetime.strptime(s, ISO).replace(tzinfo=timezone.utc)


def fmt(d):
    return d.astimezone(timezone.utc).strftime(ISO)


def person(pid, name):
    return {"id": pid, "title": name, "name": name, "email": "", "payment_method": 1, "paypal_email": ""}


def client(cid, name):
    return {"id": cid, "title": name, "name": name}


def build_open_rows():
    with open(os.path.join(ROOT, "payable_data.json")) as fh:
        src = json.load(fh)
    client_ids = {}
    designer_ids = {}
    rows = []
    with_designer = [r["id"] for r in sorted(src, key=lambda r: r["created_at"]) if r["designer"]]
    pending_ids = set(with_designer[-3:])  # newest three become "awaiting approval"
    for i, r in enumerate(src):
        cid = client_ids.setdefault(r["client"], 5000 + len(client_ids))
        created = parse(r["created_at"])
        row = {
            "id": r["id"],
            "name": r["name"],
            "client_id": cid,
            "client": client(cid, r["client"]),
            "status": 2,
            "created_at": r["created_at"],
            "updated_at": fmt(created + timedelta(days=1)),
            "approved_at": None,
            "completed_at": None,
            "submission_fee": r["submission_fee"],
            "balance_fee": r["balance_fee"],
            "separation_fee": r["separation_fee"],
            "submission_invoice": r["submission_invoice"],
            "balance_invoice": r["balance_invoice"],
            "separation_invoice": r["separation_invoice"],
            "order": r["order"],
        }
        if r["designer"]:
            did = designer_ids.setdefault(r["designer"], 9000 + len(designer_ids))
            row["designer_id"] = did
            row["designer"] = person(did, r["designer"])
        else:
            row["separator_id"] = 9900
            row["separator"] = person(9900, "Alex Wexler")
        if r["id"] in pending_ids:
            row["status"] = 0
        elif i % 5 == 1:
            row["approved_at"] = "2026-10-20T14:00:00.000000Z"  # < 30 days old on 2026-11-02
        elif i % 5 == 2:
            row["approved_at"] = "2026-09-25T16:00:00.000000Z"  # 30-59 days old
        else:
            row["approved_at"] = fmt(created + timedelta(days=3))  # 60+ days old
        rows.append(row)
    return rows


def pay(pid, designer_id, method, ptype, amount, created, payment_id="", paid_by="Karina Ruiz", payee=""):
    p = {
        "id": pid,
        "designerId": designer_id,
        "paymentId": payment_id,
        "paymentMethod": method,
        "paymentType": ptype,
        "payeeName": payee,
        "paidByName": paid_by,
        "status": 1,
        "invoice": "",
        "createdAt": created,
    }
    if amount:  # DesignPaymentsResource omits amount when it is 0
        p["amount"] = amount
    return p


def paid(did, name, cl, designer, fees, created, approved, completed, payments, separator=None):
    row = {
        "id": did,
        "name": name,
        "client_id": cl[0],
        "client": client(cl[0], cl[1]),
        "status": 5,
        "created_at": created,
        "updated_at": completed or created,
        "approved_at": approved,
        "completed_at": completed,
        "submission_fee": fees[0],
        "balance_fee": fees[1],
        "separation_fee": fees[2],
        "submission_invoice": "S%d" % did if fees[0] else "",
        "balance_invoice": "B%d" % did if fees[1] else "",
        "separation_invoice": "P%d" % did if fees[2] else "",
        "payments": payments,
    }
    if designer:
        row["designer_id"] = designer[0]
        row["designer"] = person(designer[0], designer[1])
    if separator:
        row["separator_id"] = separator[0]
        row["separator"] = person(separator[0], separator[1])
    return row


DEFTONES = (5001, "DEFTONES: 2026")
OD = (5002, "OD: Barbara Album Release")
PS = (5003, "PAUL SIMON: Catalog")


def build_paid_rows():
    return [
        # 300001: two PayPal payments; most recent decides batch id / paid by.
        paid(300001, "DT_paidpaypal_tee", DEFTONES, (9001, "Jeff Rigsby"), (100, 200, 0),
             "2026-09-20T15:00:00.000000Z", "2026-10-01T15:00:00.000000Z", "2026-10-15T18:00:00.000000Z",
             [pay(70001, 9001, 1, 1, 100, "10-02-2026 12:00:00", "BATCH-A1", paid_by="Karina Ruiz", payee="Jeff Rigsby"),
              pay(70002, 9001, 1, 2, 200, "10-15-2026 17:59:00", "BATCH-B2", paid_by="Eugene L", payee="Jeff Rigsby")]),
        # 300002: one check payment (paymentId null).
        paid(300002, "OD_checkpaid_tee", OD, (9002, "Laura Price"), (150, 0, 0),
             "2026-09-01T12:00:00.000000Z", "2026-09-05T13:00:00.000000Z", "2026-10-20T19:00:00.000000Z",
             [pay(70003, 9002, 2, 1, 150, "10-20-2026 18:55:00", None, payee="Laura Price")]),
        # 300003: older PayPal + newer check -> method Check, batch id from the PayPal one.
        # Listed newest first to prove ordering is by createdAt, not list position.
        paid(300003, "PS_mixed_tee", PS, (9003, "Matt Cliff"), (100, 100, 50),
             "2026-08-10T12:00:00.000000Z", "2026-08-15T12:00:00.000000Z", "2026-09-28T16:00:00.000000Z",
             [pay(70005, 9003, 2, 2, 100, "09-28-2026 15:30:00", "", paid_by="Karina Ruiz", payee="Matt Cliff"),
              pay(70004, 9003, 1, 1, 100, "09-01-2026 10:00:00", "BATCH-C3", paid_by="Eugene L", payee="Matt Cliff")]),
        # 300004: in-house designer, no payment records.
        paid(300004, "DT_inhouse_tee", DEFTONES, (9004, "Manhead (Maria Fulan)"), (0, 250, 0),
             "2026-09-02T12:00:00.000000Z", "2026-09-03T12:00:00.000000Z", "2026-09-30T20:00:00.000000Z",
             []),
        # 300005: 2026-11-01 03:30Z is 2026-10-31 23:30 in New York.
        paid(300005, "OD_midnight_tee", OD, (9005, "Tom Gordon"), (75, 75, 0),
             "2026-10-01T12:00:00.000000Z", "2026-10-05T14:00:00.000000Z", "2026-11-01T03:30:00.000000Z",
             [pay(70006, 9005, 1, 1, 150, "11-01-2026 03:29:00", "BATCH-D4", payee="Tom Gordon")]),
        # 300006: 2026-01-01 02:15Z is 2025-12-31 21:15 in New York (year boundary). Bank transfer.
        paid(300006, "PS_newyear_tee", PS, (9006, "Leanna White"), (200, 0, 0),
             "2025-12-01T12:00:00.000000Z", "2025-12-10T15:00:00.000000Z", "2026-01-01T02:15:00.000000Z",
             [pay(70007, 9006, 4, 1, 200, "01-01-2026 02:10:00", "", payee="Leanna White")]),
        # 300007: separator-only design (designer null), PayPal.
        paid(300007, "DT_sepfee_AW1500SEPFEE", DEFTONES, None, (0, 0, 120),
             "2026-01-05T12:00:00.000000Z", "2026-01-06T12:00:00.000000Z", "2026-01-15T17:00:00.000000Z",
             [pay(70008, 9900, 1, 3, 120, "01-15-2026 16:50:00", "BATCH-E5", payee="Alex Wexler")],
             separator=(9900, "Alex Wexler")),
        # 300008: legacy undated paid design (completed_at and approved_at null), cash.
        paid(300008, "OD_legacy_tee", OD, (9001, "Jeff Rigsby"), (300, 0, 0),
             "2024-05-10T12:00:00.000000Z", None, None,
             [pay(70009, 9001, 3, 1, 300, "06-01-2024 12:00:00", "", paid_by="", payee="Jeff Rigsby")]),
    ]


BASE = "https://api.merchwizard.com/api/dashboard/payable/history"


def envelope(rows, page, last_page, per_page, total, start):
    return {
        "data": rows,
        "links": {
            "first": BASE + "?page=1",
            "last": BASE + "?page=%d" % last_page,
            "prev": BASE + "?page=%d" % (page - 1) if page > 1 else None,
            "next": BASE + "?page=%d" % (page + 1) if page < last_page else None,
        },
        "meta": {
            "current_page": page,
            "from": start,
            "last_page": last_page,
            "path": BASE,
            "per_page": per_page,
            "to": start + len(rows) - 1,
            "total": total,
        },
    }


def main():
    open_rows = build_open_rows()
    paid_rows = build_paid_rows()
    pages = [envelope(paid_rows[:5], 1, 2, 5, 8, 1), envelope(paid_rows[5:], 2, 2, 5, 8, 6)]

    def dump(obj, name):
        with open(os.path.join(HERE, name), "w") as fh:
            json.dump(obj, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    dump({"pages": pages}, "paid_payables_sample.json")
    dump({"data": open_rows}, "open_payables_sample.json")
    dump({"open": open_rows, "paid": paid_rows}, "ui_fixture.json")
    print("open=%d paid=%d" % (len(open_rows), len(paid_rows)))


if __name__ == "__main__":
    main()
