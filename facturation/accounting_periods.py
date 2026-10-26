"""Validated calendar periods for private accounting reports in Europe/Paris."""
import re
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.utils import timezone


PARIS = ZoneInfo('Europe/Paris')
MONTHS = ('Janvier', 'Février', 'Mars', 'Avril', 'Mai', 'Juin', 'Juillet',
          'Août', 'Septembre', 'Octobre', 'Novembre', 'Décembre')
MAX_CUSTOM_DAYS = 366


def _number(value, lower, upper, message):
    try:
        if isinstance(value, bool) or not str(value).isdigit():
            raise ValueError
        value = int(value)
        if not lower <= value <= upper:
            raise ValueError
        return value
    except (TypeError, ValueError, OverflowError) as error:
        raise ValidationError(message) from error


def _date(value, label):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
            raise ValueError
        parsed = date.fromisoformat(value)
        if not 2000 <= parsed.year <= 2100:
            raise ValueError
        return parsed
    except (TypeError, ValueError, OverflowError) as error:
        raise ValidationError(f'Choisissez une date de {label} valide entre le 01/01/2000 et le 31/12/2100.') from error


def select_period(params, today=None):
    """Select a month, quarter, year or inclusive custom range from GET values.

    Only fields relevant to the chosen kind are read. Supplied empty or invalid
    relevant values never silently become today's period. ``params`` in the
    result contains only the normalized selection and is safe for URL encoding.
    """
    if not isinstance(params, Mapping):
        raise ValidationError('Choisissez une période valide.')
    today = today or timezone.localtime(timezone.now(), PARIS).date()
    kind = params.get('periode', 'month')
    if kind not in ('month', 'quarter', 'year', 'custom'):
        raise ValidationError('Choisissez un mois, un trimestre, une année ou une période personnalisée.')

    if kind == 'custom':
        start_date = _date(params.get('debut'), 'début')
        end_date = _date(params.get('fin'), 'fin')
        if end_date < start_date:
            raise ValidationError('La date de fin doit être égale ou postérieure à la date de début.')
        if (end_date - start_date).days + 1 > MAX_CUSTOM_DAYS:
            raise ValidationError('Choisissez une période de 366 jours maximum, dates de début et de fin incluses.')
        year, month = start_date.year, start_date.month
        label = f'Du {start_date:%d/%m/%Y} au {end_date:%d/%m/%Y}'
        slug = f'{start_date.isoformat()}_au_{end_date.isoformat()}'
        canonical = {'periode': 'custom', 'debut': start_date.isoformat(), 'fin': end_date.isoformat()}
    else:
        error = ('Choisissez un mois valide entre janvier 2000 et décembre 2100.' if kind == 'month'
                 else 'Choisissez une année valide entre 2000 et 2100.')
        year = _number(params.get('annee', today.year), 2000, 2100, error)
        if kind == 'month':
            month = _number(params.get('mois', today.month), 1, 12, error)
            start_date = date(year, month, 1)
            after = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
            end_date = after - timedelta(days=1)
            label, slug = f'{MONTHS[month - 1]} {year}', f'{year:04d}-{month:02d}'
            canonical = {'annee': year, 'mois': month}
        elif kind == 'quarter':
            quarter = _number(params.get('trimestre', (today.month - 1) // 3 + 1), 1, 4,
                              'Choisissez un trimestre valide, de 1 à 4.')
            month = (quarter - 1) * 3 + 1
            start_date = date(year, month, 1)
            after = date(year + 1, 1, 1) if quarter == 4 else date(year, month + 3, 1)
            end_date = after - timedelta(days=1)
            label, slug = f'{quarter}{"er" if quarter == 1 else "e"} trimestre {year}', f'{year:04d}-T{quarter}'
            canonical = {'periode': 'quarter', 'annee': year, 'trimestre': quarter}
        else:
            month = 1
            start_date, end_date = date(year, 1, 1), date(year, 12, 31)
            label, slug = f'Année {year}', f'{year:04d}'
            canonical = {'periode': 'year', 'annee': year}

    # Construct each midnight in the shop timezone; a day at the DST change may
    # contain 23 or 25 hours. Do not turn dates into UTC by adding 24-hour spans.
    start = datetime.combine(start_date, time.min, tzinfo=PARIS)
    end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=PARIS)
    return {'kind': kind, 'year': year, 'month': month, 'quarter': (month - 1) // 3 + 1,
            'start': start, 'end': end, 'start_date': start_date, 'end_date': end_date,
            'label': label, 'slug': slug, 'params': canonical}


def validated_selection(selection):
    """Rebuild bounds from canonical inputs, not caller-supplied date objects."""
    if not isinstance(selection, Mapping) or not isinstance(selection.get('params'), Mapping):
        raise ValidationError('Choisissez une période valide.')
    return select_period(selection['params'])
