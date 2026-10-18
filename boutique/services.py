from zoneinfo import ZoneInfo
from django.utils import timezone
from .models import OpeningDay, OpeningHours


def public_hours(today=None):
    """Read the published schedule on each request, including missing-day fallbacks."""
    schedule = OpeningHours.objects.filter(pk=1).prefetch_related('days').first()
    if schedule is None:
        return {'opening_days': [], 'opening_notice': '', 'opening_period': None}
    if today is None:
        today = timezone.localdate(timezone=ZoneInfo('Europe/Paris'))
    period = schedule.periods.filter(is_cancelled=False, start_date__lte=today,
                                     end_date__gte=today).prefetch_related('days').first()
    source = period or schedule
    saved_days = {day.weekday: day for day in source.days.all()}
    days = []
    for weekday, name in OpeningDay.WEEKDAYS:
        day = saved_days.get(weekday)
        periods = []
        if day and not day.is_closed:
            for start, end in [(day.opens_at, day.closes_at), (day.reopens_at, day.recloses_at)]:
                if start and end:
                    periods.append({'start': start, 'end': end})
        days.append({'name': name, 'closed': bool(day and day.is_closed),
                     'known': day is not None, 'periods': periods})
    return {'opening_days': days, 'opening_notice': source.notice, 'opening_period': period}
