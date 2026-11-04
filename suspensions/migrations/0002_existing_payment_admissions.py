from django.db import migrations
from django.db.models import Q


def preserve_engaged_payments(apps, schema_editor):
    Order = apps.get_model('paiement', 'Order')
    Admission = apps.get_model('suspensions', 'PurchaseAdmission')
    alias = schema_editor.connection.alias
    # Before this feature a committed payload was the durable indication of a
    # possibly sent Stripe request. Never infer that such a request failed.
    rows = Order.objects.using(alias).filter(
        ~Q(checkout_payload={}) | (Q(stripe_session_id__isnull=False) & ~Q(stripe_session_id=''))
    ).values_list('pk', 'created_at').iterator(chunk_size=500)
    batch = []
    for pk, created_at in rows:
        batch.append(Admission(order_id=pk, admitted_at=created_at, migrated=True))
        if len(batch) >= 500:
            Admission.objects.using(alias).bulk_create(batch, ignore_conflicts=True)
            batch = []
    if batch:
        Admission.objects.using(alias).bulk_create(batch, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [('suspensions', '0001_initial')]
    operations = [migrations.RunPython(preserve_engaged_payments, migrations.RunPython.noop)]
