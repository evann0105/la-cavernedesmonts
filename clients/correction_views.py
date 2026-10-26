"""Private seller screen for correcting the information captured during a call."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from retours.access import returns_required
from .corrections import SupportCorrectionForm, correct_case, correction_orders
from .models import SupportCase
from .selectors import get_customer


@returns_required
@require_http_methods(['GET', 'POST'])
def case_edit(request, pk):
    from .views import _form_error, _private

    case = get_object_or_404(SupportCase.objects.select_related('user', 'order'), pk=pk)
    customer = get_customer(f'c-{case.pk}')
    form = SupportCorrectionForm(request.POST if request.method == 'POST' else None,
                                 instance=case, orders=correction_orders(case, customer))
    status = 200
    if request.method == 'POST':
        if form.is_valid():
            try:
                correct_case(request.user, case.pk, form.cleaned_data)
            except ValidationError as error:
                _form_error(form, error)
                status = 409
            else:
                messages.success(request, 'Les informations du dossier ont été enregistrées. Les corrections sont conservées dans son historique.')
                return redirect('clients:case_detail', pk=case.pk)
        else:
            status = 400
    return _private(render(request, 'clients/case_edit.html', {
        'case': case, 'customer': customer, 'form': form, 'workspace': 'sav',
        'contact_email': case.user.email if case.user_id else case.customer_email,
    }, status=status))
