"""Small private editorial screens; defaults and publication rules live in services."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from core.catalog_access import catalog_required
from .configuration import configuration
from .forms import SectionForm, SectionsVisibilityForm, FeaturedProductsForm
from .models import HeroSlide
from .schema import SECTION_SPECS, SECTIONS
from .services import save_section, save_visibility, save_featured, HomepageError, HomepageConflict
from .slideshow import SlidesForm, SlideFormSet, save_slideshow


def _private(response):
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['Referrer-Policy'] = 'same-origin'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _error(form, exc):
    if isinstance(exc, ValidationError) and hasattr(exc, 'message_dict'):
        for name, errors in exc.message_dict.items():
            for error in errors:
                form.add_error(name if name in form.fields else None, error)
    elif isinstance(exc, ValidationError):
        for error in exc.messages:
            form.add_error(None, error)
    else:
        form.add_error(None, str(exc))


def _sections(current, form=None):
    rows = []
    for spec in SECTION_SPECS:
        key = spec['key']
        field_name = key if form is not None and key in form.fields else 'visible_' + key
        field = form[field_name] if form is not None and field_name in form.fields else None
        rows.append({**spec, 'visible': bool(current['visible'].get(key, True)), 'field': field,
                     'url': reverse('homepage:section', kwargs={'section': key})})
    return rows


def _panels(form, current, section, request):
    result = []
    image_fields = getattr(form, 'image_fields', {})
    panels = getattr(form, 'panels', None) or [{'title': 'Les informations de ce bloc', 'fields': [field.name for field in form.visible_fields()]}]
    for panel in panels:
        items = []
        for name in panel['fields']:
            if name not in form.fields or form.fields[name].widget.is_hidden:
                continue
            item = {'field': form[name]}
            if name in image_fields:
                image = image_fields[name]
                item.update(image=image, reset_checked=bool(request.POST.get(image['reset_field'])),
                    preview_alt=current['values'][section].get(image.get('alt_field', ''), ''))
            items.append(item)
        if items:
            result.append({'title': panel['title'], 'items': items,
                           'has_errors': any(bool(item['field'].errors) for item in items),
                           'has_images': any('image' in item for item in items)})
    return result


@catalog_required
@require_GET
def overview(request):
    current = configuration()
    sections = _sections(current)
    return _private(render(request, 'accueil/manage/overview.html', {
        'configuration': current, 'visible_count': sum(section['visible'] for section in sections),
        'section_count': len(sections), 'current_page': 'overview',
    }))


@catalog_required
@require_http_methods(['GET', 'POST'])
def sections(request):
    current = configuration()
    initial = {**current['visible'], 'revision': current['revision']}
    form = SectionsVisibilityForm(request.POST if request.method == 'POST' else None, initial=initial, configuration=current)
    status = 200
    if request.method == 'POST' and form.is_valid():
        try:
            save_visibility(form.cleaned_data, actor=request.user)
        except HomepageConflict as exc:
            _error(form, exc); status = 409
        except (HomepageError, ValidationError) as exc:
            _error(form, exc)
        else:
            messages.success(request, 'Les sections visibles sur l’accueil ont été enregistrées.')
            return _private(redirect('homepage:sections'))
    return _private(render(request, 'accueil/manage/sections.html', {
        'configuration': current, 'sections': _sections(current, form), 'form': form,
        'page_title': 'Les sections de l’accueil', 'current_page': 'sections',
    }, status=status))


@catalog_required
@require_http_methods(['GET', 'POST'])
def section(request, section):
    if section not in SECTIONS:
        raise Http404
    current = configuration()
    initial = {**current['values'][section], 'revision': current['revision']}
    form = SectionForm(section, request.POST if request.method == 'POST' else None,
                       request.FILES if request.method == 'POST' else None,
                       initial=initial, configuration=current)
    status = 200
    if request.method == 'POST' and form.is_valid():
        try:
            save_section(section, form.cleaned_data, actor=request.user)
        except HomepageConflict as exc:
            _error(form, exc); status = 409
        except (HomepageError, ValidationError) as exc:
            _error(form, exc)
        else:
            messages.success(request, 'Le bloc « ' + SECTIONS[section]['title'] + ' » a été enregistré sur l’accueil.')
            return _private(redirect('homepage:section', section=section))
    return _private(render(request, 'accueil/manage/section.html', {
        'configuration': current, 'section': SECTIONS[section], 'form': form,
        'panels': _panels(form, current, section, request),
        'section_visible': bool(current['visible'].get(section, True)),
        'page_title': SECTIONS[section]['title'], 'current_page': 'hero' if section == 'hero' else 'sections',
    }, status=status))


@catalog_required
@require_http_methods(['GET', 'POST'])
def featured(request):
    current = configuration()
    initial = {'revision': current['revision'], **{
        'position_' + str(index): product_id for index, product_id in enumerate(current['featured_ids'], 1)}}
    form = FeaturedProductsForm(request.POST if request.method == 'POST' else None, initial=initial, configuration=current)
    status = 200
    if request.method == 'POST' and form.is_valid():
        try:
            save_featured(form.cleaned_data, actor=request.user)
        except HomepageConflict as exc:
            _error(form, exc); status = 409
        except (HomepageError, ValidationError) as exc:
            _error(form, exc)
        else:
            messages.success(request, 'La sélection et l’ordre des produits de l’accueil ont été enregistrés.')
            return _private(redirect('homepage:featured'))
    # All choices are already published catalogue data. Reuse one shared map
    # for four previews rather than exposing private or unsaved product data.
    first = form.fields.get('position_1')
    products = list(first.queryset) if first is not None else []
    choices = {str(product.pk): {'name': product.name, 'image': product.image_url} for product in products}
    positions = []
    for number in range(1, 5):
        field = form['position_' + str(number)]
        positions.append({'number': number, 'field': field, 'product': choices.get(str(field.value() or ''))})
    return _private(render(request, 'accueil/manage/featured.html', {
        'configuration': current, 'form': form, 'positions': positions,
        'product_choices': choices, 'current_page': 'featured', 'page_title': 'Produits mis en avant',
    }, status=status))


def _slide_rows(formset, request):
    """Capture saved previews before ModelForm validation updates its instances."""
    rows = []
    for form in formset.forms:
        instance = form.instance
        image = instance.image if instance.pk else None
        mobile = instance.mobile_image if instance.pk else None
        mobile_clear = form['mobile_image'].html_name + '-clear'
        rows.append({'form': form, 'image_url': image.url if image else '',
                     'mobile_url': mobile.url if mobile else '',
                     'saved_alt': instance.alt if instance.pk else '',
                     'mobile_clear_name': mobile_clear,
                     'mobile_clear_checked': bool(request.POST.get(mobile_clear))})
    return rows


@catalog_required
@require_http_methods(['GET', 'POST'])
def slides(request):
    current = configuration()
    data = request.POST if request.method == 'POST' else None
    files = request.FILES if request.method == 'POST' else None
    form = SlidesForm(data, files, configuration=current)
    formset = SlideFormSet(data, files, prefix='slides',
        queryset=HeroSlide.objects.filter(configuration_id=1).order_by('position', 'pk'))
    rows = _slide_rows(formset, request)
    status = 200
    if request.method == 'POST':
        settings_valid = form.is_valid()
        slides_valid = formset.is_valid()
        if settings_valid and slides_valid:
            try:
                save_slideshow(form.cleaned_data, formset.cleaned_data, actor=request.user)
            except HomepageConflict as exc:
                _error(form, exc)
                status = 409
            except (HomepageError, ValidationError) as exc:
                _error(form, exc)
            else:
                messages.success(request, 'Les photos du diaporama et ses réglages ont été enregistrés.')
                return _private(redirect('homepage:slides'))
    return _private(render(request, 'accueil/manage/slides.html', {
        'configuration': current, 'form': form, 'formset': formset, 'slide_rows': rows,
        'saved_slide_count': len(rows), 'upload_retry': bool(request.method == 'POST' and request.FILES),
        'has_slide_errors': formset.total_error_count() > 0,
        'current_page': 'hero', 'page_title': 'Photos du diaporama',
    }, status=status))
