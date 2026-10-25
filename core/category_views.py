from django.contrib import messages
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .catalog_access import catalog_required
from .category_forms import CategoryArchiveForm, CategoryForm, CategoryMoveForm, CategoryVisibilityForm
from .category_services import (CategoryConflict, CategoryError, category_order_snapshot,
                               move_category, save_category, set_category_visibility, set_category_archived)
from .models import Category, Product


def _listing(request, *, action_error='', status=200):
    product_counts = (Product.objects.filter(Q(category_id=OuterRef('pk')) | Q(collections__id=OuterRef('pk')))
        .order_by().values(constant=Value(1)).annotate(total=Count('pk', distinct=True)).values('total'))
    all_categories = list(Category.objects.annotate(product_count=Coalesce(
        Subquery(product_counts, output_field=IntegerField()), Value(0))))
    active = [row for row in all_categories if not row.archived_at]
    archived = [row for row in all_categories if row.archived_at]
    archive_view = request.GET.get('scope') == 'archived'
    categories = archived if archive_view else active
    return render(request, 'core/catalog/categories/list.html', {
        'categories': categories, 'move_snapshot': category_order_snapshot(active, request.user),
        'menu_categories': [row for row in active if row.show_in_menu],
        'menu_count': sum(row.show_in_menu for row in active), 'action_error': action_error,
        'archive_view': archive_view, 'active_count': len(active), 'archive_count': len(archived),
    }, status=status)


@catalog_required
@require_http_methods(['GET'])
def category_list(request):
    return _listing(request)


@catalog_required
@require_http_methods(['GET', 'POST'])
def category_edit(request, pk=None):
    category = get_object_or_404(Category, pk=pk) if pk else None
    if category and category.archived_at and request.method == 'GET':
        return redirect('core:category_restore', pk=category.pk)
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


def _archive_confirmation(request, pk, *, archived):
    category = get_object_or_404(Category, pk=pk)
    return_url = reverse('core:category_list') + ('?scope=archived' if not archived else '')
    if request.method == 'GET' and bool(category.archived_at) == archived:
        messages.info(request, 'Cette rubrique est déjà archivée.' if archived else 'Cette rubrique est déjà active.')
        return redirect(reverse('core:category_list') + ('?scope=archived' if archived else ''))
    form = CategoryArchiveForm(request.POST if request.method == 'POST' else None,
                               initial={'revision': category.revision})
    status = 200
    if request.method == 'POST':
        if form.is_valid():
            try:
                row = set_category_archived(pk, form.cleaned_data['revision'], archived, request.user)
            except CategoryError as exc:
                form.add_error(None, str(exc))
                status = 409 if isinstance(exc, CategoryConflict) else 400
            else:
                messages.success(request, 'Rubrique archivée. Tous ses produits ont été conservés.' if archived else
                    'Rubrique restaurée. Vous pouvez maintenant la modifier ou l’afficher dans le menu.')
                destination = reverse('core:category_list') + ('?scope=archived' if archived else '')
                return redirect(destination + f'#rubrique-{row.pk}')
        else:
            status = 400
    products = Product.objects.filter(Q(category=category) | Q(collections=category)).distinct()
    return render(request, 'core/catalog/categories/archive.html', {
        'category': category, 'form': form, 'archiving': archived, 'return_url': return_url,
        'product_count': products.count(), 'product_examples': products.order_by('name', 'pk')[:5],
    }, status=status)


@catalog_required
@require_http_methods(['GET', 'POST'])
def category_archive(request, pk):
    return _archive_confirmation(request, pk, archived=True)


@catalog_required
@require_http_methods(['GET', 'POST'])
def category_restore(request, pk):
    return _archive_confirmation(request, pk, archived=False)
