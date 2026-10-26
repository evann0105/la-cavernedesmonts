"""Read-only support reminders, independent of the dashboard's sales period."""
from django.db.models import Case, Count, F, IntegerField, Q, Value, When
from django.urls import reverse
from django.utils import timezone

from .models import SupportCase


def dashboard_attention():
    """Return a bounded reminder queue for an already-authorized SAV manager."""
    today = timezone.localdate()
    active = SupportCase.objects.exclude(state='resolved')
    conditions = {
        'today': Q(due_date=today),
        'overdue': Q(due_date__lt=today),
        'urgent': Q(priority='urgent'),
    }
    relevant = conditions['today'] | conditions['overdue'] | conditions['urgent']
    counts = active.aggregate(
        **{key: Count('pk', filter=condition) for key, condition in conditions.items()},
        total=Count('pk', filter=relevant),
    )
    destination = reverse('clients:case_list')
    definitions = [
        ('today', 'À rappeler aujourd’hui', 'echeance=today'),
        ('overdue', 'Rappels en retard', 'echeance=overdue'),
        ('urgent', 'Dossiers urgents', 'priorite=urgent'),
    ]
    cards = [{'key': key, 'label': label, 'count': counts[key], 'url': f'{destination}?{query}'}
             for key, label, query in definitions]
    records = []
    if counts['total']:
        records = list(active.filter(relevant).only(
            'pk', 'customer_name', 'subject', 'due_date', 'priority', 'state', 'updated_at',
        ).annotate(
            attention_rank=Case(
                When(conditions['overdue'], then=Value(0)),
                When(conditions['today'], then=Value(1)),
                default=Value(2), output_field=IntegerField(),
            ),
            urgency_rank=Case(When(priority='urgent', then=Value(0)),
                              default=Value(1), output_field=IntegerField()),
        ).order_by('attention_rank', 'urgency_rank', F('due_date').asc(nulls_last=True),
                   'updated_at', 'pk')[:5])
    return {'today': today, 'cards': cards, 'records': records, 'total': counts['total']}
