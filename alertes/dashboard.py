from core.catalog_access import can_manage_catalog
from retours.access import manager
from .monitor import all_rows
from .stock import stock_snapshot


def attention(actor):
    mail_count = sum(row['status'] in ('failed', 'blocked') for row in all_rows(actor)) if manager(actor) else None
    stock_count = stock_snapshot()['low_count'] if can_manage_catalog(actor) else None
    return {'mail_count': mail_count, 'stock_count': stock_count}
