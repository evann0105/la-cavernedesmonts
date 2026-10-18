from django.contrib import messages
from django.contrib.admin.models import CHANGE, LogEntry
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from core.catalog_access import catalog_required
from .forms import OpeningDaysFormSet, OpeningHoursForm
from .models import OpeningHours


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
    return render(request, 'boutique/hours.html', {
        'hours_form': hours_form, 'days_formset': days_formset, 'schedule': schedule,
    })
