from datetime import timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select, func, text, Integer, or_
from ..models import Task, UsageRecord, User, now
from .models import Event, Order, Hold, PaymentReceipt, CreditLot


def overview(db):
    counts = {name: int(count) for name, count in db.execute(select(Event.name, func.count()).where(Event.category == "fact").group_by(Event.name))}
    users = db.scalar(select(func.count()).select_from(User))
    active = db.scalar(select(func.count(func.distinct(Event.owner_id))).where(Event.created_at >= now() - timedelta(days=7)))
    task_states = {state: count for state, count in db.execute(select(Task.state, func.count()).group_by(Task.state))}
    usage = {state: count for state, count in db.execute(select(UsageRecord.status, func.count()).group_by(UsageRecord.status))}
    first = db.scalar(select(func.min(Event.created_at)))
    # Cohort starts with first collected event, not historical signup dates.
    cohorts = db.execute(text("""
        WITH first_seen AS (
          SELECT owner_id, min((created_at AT TIME ZONE 'Asia/Shanghai')::date) AS day
          FROM product_events WHERE owner_id IS NOT NULL GROUP BY owner_id
        ) SELECT f.day, count(DISTINCT f.owner_id) AS cohort,
          count(DISTINCT e.owner_id) FILTER (WHERE (e.created_at AT TIME ZONE 'Asia/Shanghai')::date=f.day+1) AS returned
        FROM first_seen f LEFT JOIN product_events e ON e.owner_id=f.owner_id
        GROUP BY f.day ORDER BY f.day DESC LIMIT 30
    """)).mappings().all()
    today = now().astimezone(ZoneInfo("Asia/Shanghai")).date()
    retention = [{"day": c["day"].isoformat(), "cohort": c["cohort"],
                  "returned": c["returned"] if c["day"] + timedelta(days=1) < today else None,
                  "d1": round(c["returned"] / c["cohort"], 4) if c["day"] + timedelta(days=1) < today else None} for c in cohorts]
    revenue = db.execute(text("""
        SELECT r.details->>'currency' AS currency, r.details->>'environment' AS environment,
          sum((r.details->>'amountMinor')::bigint) AS amount,
          count(DISTINCT r.payment_key) AS payments
        FROM product_payment_receipts r WHERE verified AND details ? 'amountMinor'
        GROUP BY r.details->>'currency', r.details->>'environment'
    """)).mappings().all()
    trends = db.execute(text("""
        SELECT (created_at AT TIME ZONE 'Asia/Shanghai')::date AS day, count(*) AS events,
          count(DISTINCT owner_id) AS users
        FROM product_events WHERE created_at >= now()-interval '30 days'
        GROUP BY day ORDER BY day
    """)).mappings().all()
    pages = db.execute(select(Event.path, func.count()).where(Event.category == "page_view").group_by(Event.path).order_by(func.count().desc()).limit(20)).all()
    errors = db.execute(select(Event.name, func.count()).where(or_(Event.category == 'error',
        (Event.category == 'api') & (Event.data["statusCode"].astext.cast(Integer) >= 400))).group_by(Event.name).limit(20)).all()
    costs = db.execute(text("""SELECT billing_metadata->'billing'->>'currency' AS currency,
        sum((billing_metadata->'billing'->>'cost')::numeric) AS amount, count(*) AS calls
        FROM usage WHERE billing_metadata->'billing'->>'costState'='known'
        AND billing_metadata->'billing'->>'currency' ~ '^[A-Z]{3}$'
        GROUP BY billing_metadata->'billing'->>'currency'""")).mappings().all()
    return {"users": users, "active7d": active, "facts": counts, "taskStates": task_states, "usageStates": usage,
            "unknownHolds": db.scalar(select(func.count()).select_from(Hold).where(Hold.state == "unknown")),
            "collectionStartedAt": first.isoformat() if first else None, "retention": retention,
            "revenue": [{**r} for r in revenue], "revenueBasis": "已核验的支付回执；内测赠送不计收入；测试与生产、不同币种分开",
            "trends": [{"day": t["day"].isoformat(), "events": t["events"], "users": t["users"]} for t in trends],
            "pages": [{"path": p, "views": n} for p, n in pages], "errors": [{"name": p, "count": n} for p, n in errors],
            "providerCost": [{**c, 'amount': str(c['amount'])} for c in costs], "providerCostReason": "积分是产品计价；实际成本只统计供应商明确返回的金额，缺失值保留未知",
            "quality": {"clientEventsAreAuthoritative": False, "unknownUsage": usage.get("unknown", 0) + usage.get("intent", 0),
                        "reviewOrders": db.scalar(select(func.count()).select_from(Order).where(Order.state == "review"))}}
