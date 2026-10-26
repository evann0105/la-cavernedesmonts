from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from retours.access import returns_required
from .models import SupportCase
from .relationships import (SupportRelationshipsForm, relationship_context,
                            relationship_options, update_relationships)
from .selectors import get_customer


@returns_required
@require_http_methods(['GET', 'POST'])
def relationships_edit(request, pk):
    from .views import _form_error, _private
    case = get_object_or_404(SupportCase, pk=pk)
    customer = get_customer(f'c-{case.pk}')
    options = relationship_options(case, customer)
    form = SupportRelationshipsForm(request.POST if request.method == 'POST' else None,
        instance=case, options=options)
    status = 200
    if request.method == 'POST':
        if form.is_valid():
            try:
                update_relationships(request.user, case.pk, form.cleaned_data)
            except ValidationError as error:
                _form_error(form, error)
                status = 409
            else:
                messages.success(request, 'Les retours et avoirs liés au dossier ont été enregistrés.')
                return redirect('clients:case_detail', pk=case.pk)
        else:
            status = 400
    return _private(render(request, 'clients/relationships_edit.html', {
        'case': case, 'customer': customer, 'form': form, 'workspace': 'sav',
        **relationship_context(case, request.user),
    }, status=status))
