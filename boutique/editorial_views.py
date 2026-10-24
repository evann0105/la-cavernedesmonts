"""Separate, merchant-facing screens for public pages, FAQ and announcements."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from core.catalog_access import catalog_required
from .editorial_configuration import active_announcement, page_content, public_faq
from .editorial_forms import AnnouncementForm, FAQForm, PageContentForm
from .editorial_schema import PAGE_SPECS
from .editorial_services import EditorialConflict, EditorialError, save_announcement, save_faq, save_page
from .models import ContentPage, FAQEntry, SiteAnnouncement


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


def _page_spec(key):
    return {**PAGE_SPECS[key], 'key': key,
        'url': reverse('editorial:page_edit', kwargs={'key': key}),
        'public_url': reverse(PAGE_SPECS[key]['public_route'])}


@catalog_required
@require_GET
def overview(request):
    at = timezone.now()
    return _private(render(request, 'editorial/manage/overview.html', {
        'pages': [_page_spec(key) for key in PAGE_SPECS],
        'counts': {'faq': FAQEntry.objects.count(), 'faq_published': public_faq().count(),
            'announcements': SiteAnnouncement.objects.count(),
            'announcements_active': SiteAnnouncement.objects.filter(enabled=True, starts_at__lte=at, ends_at__gt=at).count(),
            'announcements_upcoming': SiteAnnouncement.objects.filter(enabled=True, starts_at__gt=at).count()},
        'page_title': 'Pages et annonces', 'current_page': 'overview',
    }))


@catalog_required
@require_http_methods(['GET', 'POST'])
def page_edit(request, key):
    if key not in PAGE_SPECS:
        raise Http404
    current = page_content(key)
    form = PageContentForm(key, request.POST if request.method == 'POST' else None,
        request.FILES if request.method == 'POST' else None, current=current)
    status = 200
    if request.method == 'POST' and form.is_valid():
        try:
            save_page(key, form.cleaned_data, request.user)
        except EditorialConflict as exc:
            _error(form, exc); status = 409
        except (EditorialError, ValidationError) as exc:
            _error(form, exc)
        else:
            messages.success(request, 'La page a été enregistrée. Les informations sont à jour sur le site.')
            return _private(redirect('editorial:page_edit', key=key))
    return _private(render(request, 'editorial/manage/page_form.html', {
        'page_key': key, 'page_spec': _page_spec(key), 'current': current, 'form': form,
        'panels': [{'title': title, 'fields': [form[name] for name in names]} for title, names in PAGE_SPECS[key]['panels']],
        'image_reset_checked': bool(form.is_bound and request.POST.get('image-clear')),
        'upload_retry': bool(request.method == 'POST' and request.FILES),
        'page_title': PAGE_SPECS[key]['title'], 'current_page': key,
    }, status=status))


@catalog_required
@require_GET
def faq_list(request):
    return _private(render(request, 'editorial/manage/faq_list.html', {
        'entries': FAQEntry.objects.order_by('position', 'pk'),
        'page_title': 'Questions fréquentes', 'current_page': 'faq',
    }))


@catalog_required
@require_http_methods(['GET', 'POST'])
def faq_edit(request, pk=None):
    entry = get_object_or_404(FAQEntry, pk=pk) if pk is not None else None
    names = ('question', 'answer', 'target', 'link_label', 'position', 'enabled', 'revision')
    initial = {name: getattr(entry, name) for name in names} if entry else {
        'position': 1, 'enabled': True, 'revision': 0}
    form = FAQForm(request.POST if request.method == 'POST' else None, initial=initial)
    status = 200
    if request.method == 'POST' and form.is_valid():
        try:
            saved = save_faq(form.cleaned_data, request.user, pk=pk)
        except EditorialConflict as exc:
            _error(form, exc); status = 409
        except (EditorialError, ValidationError) as exc:
            _error(form, exc)
        else:
            messages.success(request, 'La question a été enregistrée.' if saved.enabled else 'La question a été enregistrée et reste masquée sur le site.')
            return _private(redirect('editorial:faq_list'))
    return _private(render(request, 'editorial/manage/faq_form.html', {
        'entry': entry, 'form': form, 'page_title': 'Modifier une question' if entry else 'Ajouter une question',
        'current_page': 'faq',
    }, status=status))


@catalog_required
@require_GET
def announcements(request):
    return _private(render(request, 'editorial/manage/announcements.html', {
        'announcements': SiteAnnouncement.objects.order_by('-starts_at', '-pk'),
        'active_announcement': active_announcement(),
        'page_title': 'Annonces du site', 'current_page': 'announcements',
    }))


@catalog_required
@require_http_methods(['GET', 'POST'])
def announcement_edit(request, pk=None):
    announcement = get_object_or_404(SiteAnnouncement, pk=pk) if pk is not None else None
    names = ('name', 'kind', 'message', 'starts_at', 'ends_at', 'enabled', 'priority', 'target', 'link_label', 'revision')
    initial = {name: getattr(announcement, name) for name in names} if announcement else {
        'kind': 'information', 'priority': 0, 'enabled': False, 'revision': 0}
    form = AnnouncementForm(request.POST if request.method == 'POST' else None, initial=initial)
    status = 200
    if request.method == 'POST' and form.is_valid():
        try:
            saved = save_announcement(form.cleaned_data, request.user, pk=pk)
        except EditorialConflict as exc:
            _error(form, exc); status = 409
        except (EditorialError, ValidationError) as exc:
            _error(form, exc)
        else:
            messages.success(request, 'L’annonce a été enregistrée. Son affichage suivra les dates choisies.' if saved.enabled else 'Le brouillon a été enregistré. Il n’est pas affiché sur le site.')
            return _private(redirect('editorial:announcements'))
    return _private(render(request, 'editorial/manage/announcement_form.html', {
        'announcement': announcement, 'form': form,
        'page_title': 'Modifier une annonce' if announcement else 'Créer une annonce',
        'current_page': 'announcements',
    }, status=status))


@require_GET
def faq(request):
    return render(request, 'editorial/faq.html', {'entries': public_faq(),
        'title': 'Questions fréquentes', 'page_title': 'Questions fréquentes'})
