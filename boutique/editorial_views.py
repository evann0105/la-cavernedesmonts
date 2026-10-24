"""Separate, merchant-facing screens for public pages, FAQ and announcements."""
import json
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from core.catalog_access import catalog_required
from .editorial_configuration import (active_announcement, announcement_overlaps, announcement_rows,
    page_content, page_draft_content, page_version_content, public_faq)
from .editorial_forms import AnnouncementForm, FAQForm, PageContentForm, RevisionForm, ParisDateTimeField, FAQReorderForm, FAQVisibilityForm, FAQMoveForm
from .editorial_schema import PAGE_SPECS
from .editorial_services import (EditorialConflict, EditorialError, save_announcement, save_faq, save_page,
    publish_page, restore_page_version, faq_order_snapshot, reorder_faq, set_faq_visibility,
    archive_announcement, restore_announcement, duplicate_announcement, pause_announcement, move_faq)
from .models import ContentPage, ContentPageVersion, FAQEntry, SiteAnnouncement


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
            'announcements': SiteAnnouncement.objects.filter(archived_at__isnull=True).count(),
            'announcements_archived': SiteAnnouncement.objects.filter(archived_at__isnull=False).count(),
            'announcements_displayed': int(active_announcement(at=at) is not None),
            'announcements_active': SiteAnnouncement.objects.filter(enabled=True, archived_at__isnull=True, starts_at__lte=at, ends_at__gt=at).count(),
            'announcements_upcoming': SiteAnnouncement.objects.filter(enabled=True, archived_at__isnull=True, starts_at__gt=at).count()},
        'page_title': 'Pages et annonces', 'current_page': 'overview',
    }))


def _page_state(key):
    if key not in PAGE_SPECS:
        raise Http404
    instance = ContentPage.objects.filter(key=key).first()
    published = page_content(key, instance=instance)
    current = page_draft_content(key, instance=instance)
    return instance, published, current


@catalog_required
@require_http_methods(['GET', 'POST'])
def page_edit(request, key):
    instance, published, current = _page_state(key)
    form = PageContentForm(key, request.POST if request.method == 'POST' else None,
        request.FILES if request.method == 'POST' else None, current=current)
    status = 200
    if request.method == 'POST' and form.is_valid():
        action = request.POST.get('action', 'save_draft')
        if action not in ('save_draft', 'preview', 'publish'):
            form.add_error(None, 'Choisissez d’enregistrer un brouillon, de le prévisualiser ou de publier.')
        else:
            try:
                save_page(key, form.cleaned_data, request.user, publish=action == 'publish')
            except EditorialConflict as exc:
                _error(form, exc); status = 409
            except (EditorialError, ValidationError) as exc:
                _error(form, exc)
            else:
                if action == 'publish':
                    messages.success(request, 'La page a été publiée. Les informations sont à jour sur le site.')
                else:
                    messages.success(request, 'Le brouillon a été enregistré. La page visible par les clients reste inchangée.')
                return _private(redirect('editorial:page_preview' if action == 'preview' else 'editorial:page_edit', key=key))
    return _private(render(request, 'editorial/manage/page_form.html', {
        'page_key': key, 'page_spec': _page_spec(key), 'current': current, 'published': published,
        'has_draft': bool(instance and instance.has_draft), 'revision': current['revision'], 'form': form,
        'original_image': {'url': static('core/img/boutique/lelex-2400.jpg'), 'width': 2400, 'height': 1594},
        'panels': [{'title': title, 'fields': [form[name] for name in names]} for title, names in PAGE_SPECS[key]['panels']],
        'image_reset_checked': bool(form.is_bound and request.POST.get('image-clear')),
        'upload_retry': bool(request.method == 'POST' and request.FILES),
        'page_title': PAGE_SPECS[key]['title'], 'current_page': key,
    }, status=status))


def _preview_state(key, pk=None):
    instance, published, current = _page_state(key)
    version = None
    source = 'draft' if instance and instance.has_draft else 'published'
    if pk is not None:
        version = get_object_or_404(ContentPageVersion.objects.select_related('page', 'created_by'),
                                    pk=pk, page__key=key)
        current = page_version_content(version)
        source = 'version'
    return instance, current, source, version


def _preview(request, key, pk=None):
    instance, current, source, version = _preview_state(key, pk)
    route = 'editorial:page_version_frame' if version else 'editorial:page_preview_frame'
    kwargs = {'key': key, **({'pk': version.pk} if version else {})}
    return _private(render(request, 'editorial/manage/page_preview.html', {
        'page_spec': _page_spec(key), 'page_key': key, 'current': current, 'revision': current['revision'],
        'has_draft': bool(instance and instance.has_draft), 'preview_frame_url': reverse(route, kwargs=kwargs),
        'preview_source': source, 'selected_version': version, 'version': version,
        'page_title': 'Aperçu · ' + PAGE_SPECS[key]['title'], 'current_page': key,
    }))


@catalog_required
@require_GET
def page_preview(request, key):
    return _preview(request, key)


@catalog_required
@require_GET
def page_version_preview(request, key, pk):
    return _preview(request, key, pk)


def _preview_frame(request, key, pk=None):
    instance, current, source, version = _preview_state(key, pk)
    # These are the actual public layouts, protected like every management view.
    # The parent preview disables navigation and forms; it is not a public link.
    request._editorial_page = {'key': key, **current}
    return _private(render(request, 'espace/about.html' if key == 'about' else 'core/location.html', {
        'editorial_page': current, 'editorial_preview': True, 'preview_source': source,
        'title': current['values']['page_title'], 'page_title': current['values']['page_title'],
    }))


@catalog_required
@require_GET
@xframe_options_sameorigin
def page_preview_frame(request, key):
    return _preview_frame(request, key)


@catalog_required
@require_GET
@xframe_options_sameorigin
def page_version_frame(request, key, pk):
    return _preview_frame(request, key, pk)


def _history(request, key, *, form=None, status=200):
    instance, published, current = _page_state(key)
    return _private(render(request, 'editorial/manage/page_history.html', {
        'page_spec': _page_spec(key), 'page_key': key, 'current': current, 'published': published,
        'versions': instance.versions.select_related('created_by').all() if instance else [],
        'has_draft': bool(instance and instance.has_draft), 'revision': current['revision'],
        'published_revision': published['published_revision'], 'form': form,
        'page_title': 'Historique · ' + PAGE_SPECS[key]['title'], 'current_page': key,
    }, status=status))


@catalog_required
@require_GET
def page_history(request, key):
    return _history(request, key)


@catalog_required
@require_POST
def page_restore(request, key, pk):
    if key not in PAGE_SPECS:
        raise Http404
    get_object_or_404(ContentPageVersion, pk=pk, page__key=key)
    form = RevisionForm(request.POST)
    status = 400
    if form.is_valid():
        try:
            restore_page_version(key, pk, form.cleaned_data['revision'], request.user)
        except EditorialConflict as exc:
            _error(form, exc); status = 409
        except (EditorialError, ValidationError) as exc:
            _error(form, exc)
        else:
            messages.success(request, 'Cette version a été restaurée dans le brouillon. Vérifiez l’aperçu, puis publiez pour la rendre visible.')
            return _private(redirect('editorial:page_preview', key=key))
    return _history(request, key, form=form, status=status)


@catalog_required
@require_POST
def page_publish(request, key):
    if key not in PAGE_SPECS:
        raise Http404
    form = RevisionForm(request.POST)
    status = 400
    if form.is_valid():
        try:
            publish_page(key, form.cleaned_data['revision'], request.user)
        except EditorialConflict as exc:
            _error(form, exc); status = 409
        except (EditorialError, ValidationError) as exc:
            _error(form, exc)
        else:
            messages.success(request, 'Le brouillon a été publié. La page est à jour sur le site.')
            return _private(redirect('editorial:page_edit', key=key))
    return _history(request, key, form=form, status=status)


def _faq_list(request, *, action_form=None, status=200):
    entries = list(FAQEntry.objects.order_by('position', 'pk'))
    return _private(render(request, 'editorial/manage/faq_list.html', {
        'entries': entries, 'action_form': action_form,
        'move_snapshot': faq_order_snapshot(entries, request.user),
        'page_title': 'Questions fréquentes', 'current_page': 'faq',
    }, status=status))


@catalog_required
@require_GET
def faq_list(request):
    return _faq_list(request)


@catalog_required
@require_http_methods(['GET', 'POST'])
def faq_reorder(request):
    entries = list(FAQEntry.objects.order_by('position', 'pk'))
    form = FAQReorderForm(request.POST if request.method == 'POST' else None, initial={
        'order': json.dumps([entry.pk for entry in entries]),
        'snapshot': faq_order_snapshot(entries, request.user),
    })
    status = 200
    if request.method == 'POST':
        status = 400
        if form.is_valid():
            try:
                reorder_faq(form.cleaned_data['order'], form.cleaned_data['snapshot'], request.user)
            except EditorialConflict as exc:
                _error(form, exc); status = 409
            except (EditorialError, ValidationError) as exc:
                _error(form, exc)
            else:
                messages.success(request, 'L’ordre des questions a été enregistré.')
                return _private(redirect('editorial:faq_list'))
    return _private(render(request, 'editorial/manage/faq_reorder.html', {
        'entries': entries, 'form': form, 'page_title': 'Réorganiser les questions', 'current_page': 'faq',
    }, status=status))


@catalog_required
@require_POST
def faq_toggle(request, pk):
    get_object_or_404(FAQEntry, pk=pk)
    form = FAQVisibilityForm(request.POST)
    status = 400
    if form.is_valid():
        try:
            entry = set_faq_visibility(pk, form.cleaned_data['revision'], form.cleaned_data['enabled'], request.user)
        except EditorialConflict as exc:
            _error(form, exc); status = 409
        except (EditorialError, ValidationError) as exc:
            _error(form, exc)
        else:
            messages.success(request, 'La question est maintenant affichée sur le site.' if entry.enabled else 'La question est maintenant masquée sur le site.')
            return _private(redirect('editorial:faq_list'))
    return _faq_list(request, action_form=form, status=status)


@catalog_required
@require_POST
def faq_move(request):
    form = FAQMoveForm(request.POST)
    status = 400
    if form.is_valid():
        pk, direction = form.cleaned_data['move']
        try:
            move_faq(pk, direction, form.cleaned_data['snapshot'], request.user)
        except EditorialConflict as exc:
            _error(form, exc); status = 409
        except (EditorialError, ValidationError) as exc:
            _error(form, exc)
        else:
            messages.success(request, 'L’ordre des questions a été enregistré.')
            return _private(redirect(reverse('editorial:faq_list') + f'#faq-{pk}'))
    return _faq_list(request, action_form=form, status=status)


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


def _announcement_list(request, *, showing_archives=None, action_form=None, status=200):
    at = timezone.now()
    if showing_archives is None:
        showing_archives = request.GET.get('view') == 'archives'
    current_entries = list(SiteAnnouncement.objects.filter(archived_at__isnull=True).order_by('-starts_at', '-pk'))
    states = {state: [] for state in ('active', 'scheduled', 'draft', 'ended')}
    for entry in current_entries:
        states[entry.status_at(at)].append(entry)
    selected_state = request.GET.get('state', '') if not showing_archives else ''
    if selected_state not in states:
        selected_state = ''
    archives = SiteAnnouncement.objects.filter(archived_at__isnull=False)
    entries = list(archives.order_by('-archived_at', '-pk')) if showing_archives else states.get(selected_state, current_entries)
    displayed = active_announcement(at=at)
    request._site_announcement = displayed
    counts = {'current': len(current_entries),
              'archives': len(entries) if showing_archives else archives.count(),
              **{state: len(rows) for state, rows in states.items()}}
    # Count conflicts against the entire current list, including periods hidden
    # by the selected status filter. Reuse that same snapshot for its rows.
    all_rows = announcement_rows(current_entries if not showing_archives else entries, displayed, at=at)
    row_by_id = {row['announcement'].pk: row for row in all_rows}
    return _private(render(request, 'editorial/manage/announcements.html', {
        'announcements': entries, 'announcement_rows': [row_by_id[entry.pk] for entry in entries],
        'active_announcement': displayed, 'showing_archives': showing_archives,
        'list_view': 'archives' if showing_archives else 'current', 'counts': counts, 'selected_state': selected_state,
        'action_form': action_form, 'page_title': 'Archives des annonces' if showing_archives else 'Annonces du site',
        'current_page': 'announcements',
    }, status=status))


@catalog_required
@require_GET
def announcements(request):
    return _announcement_list(request)


def _announcement_action(request, pk, action):
    entry = get_object_or_404(SiteAnnouncement, pk=pk)
    form = RevisionForm(request.POST)
    status = 400
    if form.is_valid():
        try:
            service = {'duplicate': duplicate_announcement, 'archive': archive_announcement, 'restore': restore_announcement, 'pause': pause_announcement}[action]
            saved = service(pk, form.cleaned_data['revision'], request.user)
        except EditorialConflict as exc:
            _error(form, exc); status = 409
        except (EditorialError, ValidationError) as exc:
            _error(form, exc)
        else:
            if action == 'duplicate':
                messages.success(request, 'La copie a été créée en brouillon. Vérifiez ses dates avant de l’activer.')
                return _private(redirect('editorial:announcement_edit', pk=saved.pk))
            if action == 'pause':
                messages.success(request, 'L’annonce a été mise en pause. Ses dates et son message sont conservés.')
                return _private(redirect('editorial:announcements'))
            if action == 'archive':
                messages.success(request, 'L’annonce a été archivée et retirée du site. Vous pourrez la restaurer depuis les archives.')
                return _private(redirect(reverse('editorial:announcements') + '?view=archives'))
            messages.success(request, 'L’annonce a été restaurée en brouillon. Ses dates sont conservées : vérifiez-les avant de l’activer.')
            return _private(redirect('editorial:announcements'))
    return _announcement_list(request, showing_archives=entry.archived_at is not None, action_form=form, status=status)


@catalog_required
@require_POST
def announcement_duplicate(request, pk):
    return _announcement_action(request, pk, 'duplicate')


@catalog_required
@require_POST
def announcement_archive(request, pk):
    return _announcement_action(request, pk, 'archive')


@catalog_required
@require_POST
def announcement_restore(request, pk):
    return _announcement_action(request, pk, 'restore')


@catalog_required
@require_POST
def announcement_pause(request, pk):
    return _announcement_action(request, pk, 'pause')


@catalog_required
@require_GET
def announcement_overlap_check(request):
    date_field = ParisDateTimeField(input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S'])
    try:
        starts_at = date_field.clean(request.GET.get('starts_at'))
        ends_at = date_field.clean(request.GET.get('ends_at'))
        exclude_pk = forms.IntegerField(min_value=1, required=False).clean(request.GET.get('exclude_pk'))
        if ends_at <= starts_at:
            raise ValidationError('La fin doit être postérieure au début.')
    except ValidationError as exc:
        return _private(JsonResponse({'overlaps': [], 'error': ' '.join(exc.messages)}, status=400))
    entries = announcement_overlaps(starts_at, ends_at, exclude_pk=exclude_pk)
    return _private(JsonResponse({'overlaps': [{
        'name': entry.name, 'starts_at': entry.starts_at.isoformat(), 'ends_at': entry.ends_at.isoformat(),
        'priority_label': entry.get_priority_display(),
    } for entry in entries], 'error': ''}))


@catalog_required
@require_http_methods(['GET', 'POST'])
def announcement_edit(request, pk=None):
    announcement = get_object_or_404(SiteAnnouncement, pk=pk) if pk is not None else None
    if announcement is not None and announcement.archived_at is not None:
        if request.method == 'POST':
            action_form = RevisionForm(request.POST)
            action_form.is_valid()
            action_form.add_error(None, 'Cette annonce a été archivée. Restaurez-la avant de la modifier.')
            return _announcement_list(request, showing_archives=True, action_form=action_form, status=409)
        messages.info(request, 'Cette annonce est archivée. Restaurez-la pour la modifier.')
        return _private(redirect(reverse('editorial:announcements') + '?view=archives'))
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
    candidate = form.cleaned_data if form.is_bound else initial
    overlaps = announcement_overlaps(candidate.get('starts_at'), candidate.get('ends_at'), exclude_pk=pk) if candidate.get('enabled') else []
    return _private(render(request, 'editorial/manage/announcement_form.html', {
        'announcement': announcement, 'form': form, 'overlaps': overlaps,
        'page_title': 'Modifier une annonce' if announcement else 'Créer une annonce',
        'current_page': 'announcements',
    }, status=status))


@require_GET
def faq(request):
    return render(request, 'editorial/faq.html', {'entries': public_faq(),
        'title': 'Questions fréquentes', 'page_title': 'Questions fréquentes'})
