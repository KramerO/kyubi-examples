"""Kunden bereinigen: Namen normalisieren, Region und Alter berechnen."""

from pylib.regions import region_of

from kyubi.lib import dates, reject, strings

MIN_AGE = 18


def setup(ctx):
    ctx.log.info(f"Stichtag {ctx.params.RUN_DATE}")
    return {"seen": 0}


def transform(rec, ctx):
    ctx.state["seen"] += 1
    if rec.birth_date is None:
        raise reject("birth_date fehlt")
    age = dates.years_between(rec.birth_date, ctx.params.RUN_DATE)
    if age < MIN_AGE:
        return None
    return {
        "customer_id": rec.customer_id,
        "name": strings.normalize_name(rec.name),
        "revenue": rec.amount,
        "birth_date": rec.birth_date,
        "created_at": rec.created_at,
        "status": rec.status,
        "zip": rec.zip,
        "city": rec.city,
        "region": region_of(rec.zip)[:1] if region_of(rec.zip) else None,
        "age": age,
    }


def teardown(ctx):
    print(f"{ctx.state['seen']} Datensätze verarbeitet")
