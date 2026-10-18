from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.db import transaction
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from core.catalog_access import catalog_required
from .forms import (OpeningDaysFormSet, OpeningHoursForm, OpeningPeriodDaysFormSet,
                    OpeningPeriodForm, PeriodActionForm)
from .models import OpeningHours, OpeningPeriod
from .services import public_hours


@catalog_required
@require_http_methods(['GET', 'POST'])
def hours(request):
    # One lock and one transaction cover the note and all seven days.
    with transaction.atomic():
        queryset = OpeningHours.objects.all()
        if request.method == 'POST':
            queryset = queryset.select_for_update()
        schedule = get_object_or_404(queryset, pk=1)
        data = request.POST if request.method == 'POST' else None
        hours_form = OpeningHoursForm(data, instance=schedule)
        days_formset = OpeningDaysFormSet(data, instance=schedule, prefix='days')
        if request.method == 'POST':
            hours_valid = hours_form.is_valid()
            days_valid = days_formset.is_valid()
            if hours_valid and hours_form.cleaned_data['revision'] != schedule.revision:
                hours_form.add_error(None, 'Les horaires ont été modifiés dans un autre onglet. Rechargez la page avant de les enregistrer à nouveau.')
                hours_valid = False
            if hours_valid and days_valid:
                saved = hours_form.save(commit=False)
                saved.revision += 1
                saved.updated_by = request.user
                saved.save()
                days_formset.save()
                LogEntry.objects.log_actions(
                    user_id=request.user.pk,
                    queryset=OpeningHours.objects.filter(pk=saved.pk),
                    action_flag=CHANGE,
                    change_message='Horaires du magasin et information complémentaire mis à jour.',
                )
                messages.success(request, 'Les horaires du magasin ont été enregistrés. Ils sont à jour sur le site.')
                return redirect('boutique:hours')
    periods = sorted(schedule.periods.all(), key=lambda period: (
        {'active': 0, 'upcoming': 1, 'past': 2, 'cancelled': 3}[period.status],
        -period.start_date.toordinal() if period.status == 'past' else period.start_date.toordinal(),
        period.pk,
    ))
    return render(request, 'boutique/hours.html', {
        'hours_form': hours_form, 'days_formset': days_formset, 'schedule': schedule,
        'periods': periods, 'current_period': public_hours()['opening_period'],
    })


def _period_log(request, period, message, action_flag=CHANGE):
    LogEntry.objects.log_actions(user_id=request.user.pk,
        queryset=OpeningPeriod.objects.filter(pk=period.pk), action_flag=action_flag,
        change_message=message)


@catalog_required
@require_http_methods(['GET', 'POST'])
def period_edit(request, pk=None):
    with transaction.atomic():
        schedules = OpeningHours.objects.all()
        if request.method == 'POST':
            schedules = schedules.select_for_update()
        schedule = get_object_or_404(schedules, pk=1)
        period = get_object_or_404(OpeningPeriod, pk=pk, schedule=schedule) if pk else OpeningPeriod(schedule=schedule)
        is_new = period.pk is None
        initial_days = None
        if is_new:
            normal_days = {day.weekday: day for day in schedule.days.all()}
            fields = ('is_closed', 'opens_at', 'closes_at', 'reopens_at', 'recloses_at')
            initial_days = [{field: getattr(normal_days[weekday], field) for field in fields}
                            if weekday in normal_days else {'is_closed': True}
                            for weekday in range(7)]
        data = request.POST if request.method == 'POST' else None
        period_form = OpeningPeriodForm(data, instance=period)
        days_formset = OpeningPeriodDaysFormSet(data, instance=period, initial=initial_days, prefix='days')
        if request.method == 'POST':
            form_valid = period_form.is_valid()
            days_valid = days_formset.is_valid()
            if form_valid and period_form.cleaned_data['revision'] != (0 if is_new else period.revision):
                period_form.add_error(None, 'Cette période a été modifiée dans un autre onglet. Rechargez la page avant de l’enregistrer.')
                form_valid = False
            if form_valid and days_valid:
                period = period_form.save(commit=False)
                period.revision = 1 if is_new else period.revision + 1
                period.updated_by = request.user
                period.save()
                days_formset.instance = period
                days_formset.save()
                _period_log(request, period, 'Période d’horaires créée.' if is_new else 'Période d’horaires modifiée.',
                            action_flag=ADDITION if is_new else CHANGE)
                messages.success(request, 'La période a été enregistrée. Ses horaires s’appliqueront automatiquement aux dates choisies.'
                                 if not period.is_cancelled else 'La période a été modifiée. Elle reste annulée jusqu’à sa réactivation.')
                return redirect('boutique:hours')
    return render(request, 'boutique/period.html', {
        'period_form': period_form, 'days_formset': days_formset,
        'schedule': schedule, 'period': period, 'is_new': is_new,
    })


def _period_action(request, pk, is_restoring):
    with transaction.atomic():
        schedules = OpeningHours.objects.all()
        if request.method == 'POST':
            schedules = schedules.select_for_update()
        schedule = get_object_or_404(schedules, pk=1)
        period = get_object_or_404(OpeningPeriod, pk=pk, schedule=schedule)
        action_form = PeriodActionForm(request.POST if request.method == 'POST' else None,
                                       initial={'revision': period.revision})
        if request.method == 'POST' and action_form.is_valid():
            if action_form.cleaned_data['revision'] != period.revision:
                action_form.add_error(None, 'Cette période a été modifiée dans un autre onglet. Rechargez la page avant de continuer.')
            elif period.is_cancelled != is_restoring:
                action_form.add_error(None, 'L’état de cette période a déjà changé. Revenez à la liste des horaires.')
            else:
                period.is_cancelled = not is_restoring
                try:
                    period.full_clean()
                except ValidationError as error:
                    action_form.add_error(None, ValidationError(error.messages))
                else:
                    period.revision += 1
                    period.updated_by = request.user
                    period.save()
                    _period_log(request, period, 'Période réactivée.' if is_restoring else 'Période annulée.')
                    messages.success(request, 'La période a été réactivée.' if is_restoring else 'La période a été annulée. Les horaires habituels s’appliquent à nouveau aux dates concernées.')
                    return redirect('boutique:hours')
                # Keep the displayed state consistent when reactivation was refused.
                period.is_cancelled = is_restoring
    return render(request, 'boutique/period_cancel.html', {
        'period': period, 'action_form': action_form, 'is_restoring': is_restoring,
    })


@catalog_required
@require_http_methods(['GET', 'POST'])
def period_cancel(request, pk):
    return _period_action(request, pk, is_restoring=False)


@catalog_required
@require_http_methods(['GET', 'POST'])
def period_restore(request, pk):
    return _period_action(request, pk, is_restoring=True)
