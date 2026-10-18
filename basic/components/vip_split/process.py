"""VIP-Kunden (Umsatz >= Schwelle) auf 'vip', alle anderen auf 'rest'."""

from decimal import Decimal

from kyubi.lib import log


def setup(ctx):
    return {"threshold": Decimal(str(ctx.component_params.threshold))}


def process(inputs, outputs, ctx):
    vip = 0
    for rec in inputs.in_:
        amount = rec.revenue if rec.revenue is not None else Decimal(0)
        if amount >= ctx.state["threshold"]:
            outputs.vip.write(rec)
            vip += 1
        else:
            outputs.rest.write(rec)
    log.info(f"{vip} VIP-Kunden")
