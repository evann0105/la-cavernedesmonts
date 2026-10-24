from django.contrib import messages
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .catalog_access import catalog_required
from .category_forms import CategoryForm, CategoryMoveForm, CategoryVisibilityForm
from .category_services import (CategoryConflict, CategoryError, category_order_snapshot,
                               move_category, save_category, set_category_visibility)
from .models import Category, Product


def _listing(request, *, action_error='', status=200):
    product_counts = (Product.objects.filter(Q(category_id=OuterRef('pk')) | Q(collections__id=OuterRef('pk')))
        .order_by().values(constant=Value(1)).annotate(total=Count('pk', distinct=True)).values('total'))
    categories = list(Category.objects.annotate(product_count=Coalesce(
        Subquery(product_counts, output_field=IntegerField()), Value(0))))
    return render(request, 'core/catalog/categories/list.html', {
        'categories': categories, 'move_snapshot': category_order_snapshot(categories, request.user),
        'menu_count': sum(row.show_in_menu for row in categories), 'action_error': action_error,
    }, status=status)


@catalog_required
@require_http_methods(['GET'])
def category_list(request):
    return _listing(request)


@catalog_required
@require_http_methods(['GET', 'POST'])
def category_edit(request, pk=None):
    category = get_object_or_404(Category, pk=pk) if pk else None
    initial = {key: getattr(category, key) for key in ('name', 'menu_label', 'show_in_menu', 'revision')} if category else {'revision': 0}
    form = CategoryForm(request.POST if request.method == 'POST' else None, initial=initial)
    status = 200
    if request.method == 'POST':
        if form.is_valid():
            try:
                saved = save_category(pk, form.cleaned_data, request.user)
            except CategoryError as exc:
                form.add_error(None, str(exc))
                status = 409 if isinstance(exc, CategoryConflict) else 400
            else:
                messages.success(request, 'Rubrique créée.' if category is None else 'Rubrique mise à jour.')
                return redirect(reverse('core:category_list') + f'#rubrique-{saved.pk}')
        else:
            status = 400
    return render(request, 'core/catalog/categories/form.html', {'form': form, 'category': category}, status=status)


# The same guarded editor serves an empty creation form and an existing rubric.
category_create = category_edit


@catalog_required
@require_http_methods(['POST'])
def category_move(request):
    form = CategoryMoveForm(request.POST)
    if not form.is_valid():
        return _listing(request, action_error='La demande n’a pas pu être vérifiée. Utilisez les boutons de la liste ci-dessous.', status=400)
    pk, direction = form.cleaned_data['move'].split(':')
    try:
        move_category(int(pk), direction, form.cleaned_data['snapshot'], request.user)
    except CategoryError as exc:
        return _listing(request, action_error=str(exc), status=409 if isinstance(exc, CategoryConflict) else 400)
    messages.success(request, 'L’ordre des rubriques a été mis à jour dans le catalogue et dans le menu.')
    return redirect(reverse('core:category_list') + f'#rubrique-{pk}')


@catalog_required
@require_http_methods(['POST'])
def category_visibility(request, pk):
    form = CategoryVisibilityForm(request.POST)
    if not form.is_valid():
        return _listing(request, action_error='La demande n’a pas pu être vérifiée. Utilisez les boutons de la liste ci-dessous.', status=400)
    try:
        row = set_category_visibility(pk, form.cleaned_data['revision'],
                                      form.cleaned_data['show_in_menu'] == '1', request.user)
    except CategoryError as exc:
        return _listing(request, action_error=str(exc), status=409 if isinstance(exc, CategoryConflict) else 400)
    messages.success(request, 'La rubrique apparaît maintenant dans le menu.' if row.show_in_menu else
                     'La rubrique est retirée du menu. Ses produits restent accessibles dans le catalogue.')
    return redirect(reverse('core:category_list') + f'#rubrique-{pk}')
