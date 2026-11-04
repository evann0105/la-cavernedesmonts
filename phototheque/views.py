from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.catalog_access import catalog_required
from . import services
from .forms import FolderForm, MovePhotosForm, PhotoActionForm, PhotoFilterForm, PhotoForm, UploadPhotosForm
from .models import Folder, Photo


def _private(response):
    patch_cache_control(response, private=True, no_store=True, max_age=0)
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Referrer-Policy'] = 'same-origin'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _object(model, pk):
    if not 0 < pk <= 9223372036854775807:
        raise Http404
    return get_object_or_404(model, pk=pk)


def _params(data):
    """Only known local filters may survive a return link, never a target URL."""
    form = PhotoFilterForm(data)
    if not form.is_valid():
        return {}
    values = {key: value for key, value in form.cleaned_data.items() if value}
    raw_page = data.get('page', '')
    if raw_page.isdecimal() and len(raw_page) <= 6 and int(raw_page) > 1:
        values['page'] = str(int(raw_page))
    return values


def _back_query(request):
    raw = (request.POST.get('back') if request.method == 'POST' else request.GET.get('back')) or ''
    return urlencode(_params(QueryDict(raw[:2000])))


def _library_url(query=''):
    return reverse('phototheque:overview') + ('?' + query if query else '')


def _detail_url(photo, back=''):
    return reverse('phototheque:detail', args=[photo.pk]) + ('?' + urlencode({'back': back}) if back else '')


def _query(form, *, active_only=False):
    photos = Photo.objects.select_related('folder')
    if not form.is_valid():
        return photos.none()
    data = form.cleaned_data
    photos = photos.filter(archived_at__isnull=True if active_only else data['state'] != 'archived')
    if data['q']:
        photos = photos.filter(Q(title__icontains=data['q']) | Q(original_name__icontains=data['q'])
                               | Q(folder__name__icontains=data['q']))
    if data['folder'] == 'unfiled':
        photos = photos.filter(folder__isnull=True)
    elif data['folder']:
        photos = photos.filter(folder_id=int(data['folder']))
    return photos


def _card(photo):
    urls = services.photo_urls(photo)
    return {'photo': photo, 'url': urls['url'], 'thumb': urls['thumb'] or urls['url']}


def _overview_response(request, *, data=None, move_form=None, status=200):
    data = request.GET if data is None else data
    form = PhotoFilterForm(data)
    photos = _query(form)
    if not form.is_valid():
        status = 400
    page = Paginator(photos, 24).get_page(data.get('page'))
    params = _params(data)
    back = urlencode(params)
    rows = [_card(photo) for photo in page]
    selection = set()
    if move_form and move_form.is_bound:
        try:
            selection = {int(value) for value in move_form.data.getlist('photo_ids') if len(value) < 20 and value.isdecimal()}
        except (ValueError, TypeError):
            selection = set()
    for row in rows:
        row['detail_url'] = _detail_url(row['photo'], back)
        row['selected'] = row['photo'].pk in selection
    counts = Photo.objects.aggregate(active=Count('pk', filter=Q(archived_at__isnull=True)),
        archived=Count('pk', filter=Q(archived_at__isnull=False)))
    previous_url = _library_url(urlencode({**params, 'page': page.previous_page_number()})) if page.has_previous() else ''
    next_url = _library_url(urlencode({**params, 'page': page.next_page_number()})) if page.has_next() else ''
    return _private(render(request, 'phototheque/management/overview.html', {
        'page_title': 'Bibliothèque de photos', 'form': form, 'page_obj': page,
        'rows': rows, 'counts': counts, 'back': back,
        'move_form': move_form or MovePhotosForm(auto_id='id_move_%s'), 'previous_url': previous_url, 'next_url': next_url,
        'is_archived': form.is_valid() and form.cleaned_data['state'] == 'archived',
    }, status=status))


@catalog_required
@require_GET
def overview(request):
    return _overview_response(request)


@catalog_required
@require_GET
def photos_json(request):
    data = request.GET.copy(); data['state'] = 'active'
    form = PhotoFilterForm(data)
    if not form.is_valid():
        return _private(JsonResponse({'error': 'Vérifiez le texte recherché et le dossier choisi.'}, status=400))
    page = Paginator(_query(form, active_only=True), 24).get_page(data.get('page'))
    results = []
    for photo in page:
        card = _card(photo)
        # A missing local file is never offered as a usable library choice.
        if not card['url']:
            continue
        results.append({'id': photo.pk, 'title': photo.title, 'alt': '', 'url': card['url'], 'thumb': card['thumb'],
            'width': photo.width, 'height': photo.height,
            'folder': {'id': photo.folder_id, 'name': photo.folder.name} if photo.folder_id else None})
    return _private(JsonResponse({'results': results, 'folders': list(Folder.objects.values('id', 'name')),
        'pagination': {'page': page.number, 'pages': page.paginator.num_pages, 'total': page.paginator.count,
                       'has_next': page.has_next(), 'has_previous': page.has_previous()}}))


@catalog_required
@require_http_methods(['GET', 'POST'])
def upload(request):
    back = _back_query(request)
    previous_folder = QueryDict(back).get('folder')
    form = UploadPhotosForm(request.POST if request.method == 'POST' else None,
                            request.FILES if request.method == 'POST' else None,
                            initial={'folder': previous_folder if previous_folder not in (None, 'unfiled') else None})
    status = 200
    if request.method == 'POST':
        if form.is_valid():
            try:
                result = services.upload_photos(form.cleaned_data['photos'], actor=request.user,
                    folder_id=form.cleaned_data['folder'].pk if form.cleaned_data['folder'] else None)
            except (services.PhotoError, ValidationError) as error:
                form.add_error(None, error); status = 400
            else:
                created = sum(1 for _, added in result if added)
                known = len(result) - created
                message = f'{created} photo(s) ajoutée(s) à la bibliothèque.' if created else 'Ces photos sont déjà dans la bibliothèque.'
                if known and created:
                    message += f' {known} photo(s) déjà présente(s), sans doublon.'
                if known:
                    message += ' Les photos déjà présentes conservent leur dossier.'
                if any(photo.archived_at for photo, _ in result):
                    message += ' Certaines sont archivées : réactivez-les dans les photos archivées pour les réutiliser.'
                messages.success(request, message)
                actual_folders = {photo.folder_id for photo, _ in result}
                result_params = {}
                if len(actual_folders) == 1:
                    actual_folder = actual_folders.pop()
                    result_params['folder'] = actual_folder if actual_folder is not None else 'unfiled'
                if all(photo.archived_at for photo, _ in result):
                    result_params['state'] = 'archived'
                result_query = urlencode(result_params)
                return _private(redirect(_detail_url(result[0][0], back) if len(result) == 1 else _library_url(result_query)))
        else:
            status = 400
    return _private(render(request, 'phototheque/management/upload.html', {
        'page_title': 'Ajouter des photos', 'form': form, 'back': back, 'back_url': _library_url(back),
    }, status=status))


@catalog_required
@require_http_methods(['GET', 'POST'])
def detail(request, pk):
    photo = _object(Photo, pk)
    back = _back_query(request)
    form = PhotoForm(request.POST if request.method == 'POST' else None,
                     initial={'title': photo.title, 'folder': photo.folder_id})
    status = 200
    if request.method == 'POST':
        if form.is_valid():
            try:
                services.update_photo(photo.pk, actor=request.user, title=form.cleaned_data['title'],
                    folder_id=form.cleaned_data['folder'].pk if form.cleaned_data['folder'] else None)
            except (services.PhotoError, ValidationError) as error:
                form.add_error(None, error); status = 400
            else:
                messages.success(request, 'Le nom et le rangement de la photo ont été enregistrés.')
                return _private(redirect(_detail_url(photo, back)))
        else:
            status = 400
    return _private(render(request, 'phototheque/management/detail.html', {
        'page_title': 'Consulter une photo', 'form': form, **_card(photo),
        'back': back, 'back_url': _library_url(back),
    }, status=status))


@catalog_required
@require_POST
def move(request):
    back = _back_query(request)
    form = MovePhotosForm(request.POST, auto_id='id_move_%s')
    if form.is_valid():
        try:
            with transaction.atomic():
                photos = list(Photo.objects.select_for_update().filter(pk__in=form.cleaned_data['photo_ids']).order_by('pk'))
                if len(photos) != len(form.cleaned_data['photo_ids']):
                    raise services.PhotoError('Une photo n’est plus disponible. Vérifiez votre sélection.')
                for photo in photos:
                    services.update_photo(photo.pk, actor=request.user, title=photo.title,
                        folder_id=form.cleaned_data['folder'].pk if form.cleaned_data['folder'] else None)
        except (services.PhotoError, ValidationError) as error:
            form.add_error(None, error)
        else:
            messages.success(request, f'{len(photos)} photo(s) rangée(s).')
            return _private(redirect(_library_url(back)))
    return _overview_response(request, data=QueryDict(back), move_form=form, status=400)


@catalog_required
@require_http_methods(['GET', 'POST'])
def folders(request):
    form = FolderForm(request.POST if request.method == 'POST' else None)
    status = 200
    if request.method == 'POST':
        if form.is_valid():
            try:
                folder = services.create_folder(form.cleaned_data['name'], actor=request.user)
            except (services.PhotoError, ValidationError) as error:
                form.add_error('name', error); status = 400
            else:
                messages.success(request, 'Le dossier a été créé. Vous pouvez y ranger vos photos.')
                return _private(redirect(_library_url(urlencode({'folder': folder.pk}))))
        else:
            status = 400
    return _private(render(request, 'phototheque/management/folders.html', {
        'page_title': 'Dossiers de photos', 'form': form,
        'folders': Folder.objects.annotate(photo_count=Count('photos', filter=Q(photos__archived_at__isnull=True)),
                                           archived_count=Count('photos', filter=Q(photos__archived_at__isnull=False))),
    }, status=status))


@catalog_required
@require_http_methods(['GET', 'POST'])
def folder_edit(request, pk):
    folder = _object(Folder, pk)
    form = FolderForm(request.POST if request.method == 'POST' else None, initial={'name': folder.name})
    status = 200
    if request.method == 'POST':
        if form.is_valid():
            try:
                services.rename_folder(folder.pk, form.cleaned_data['name'], actor=request.user)
            except (services.PhotoError, ValidationError) as error:
                form.add_error('name', error); status = 400
            else:
                messages.success(request, 'Le dossier a été renommé. Les photos restent rangées au même endroit.')
                return _private(redirect('phototheque:folders'))
        else:
            status = 400
    return _private(render(request, 'phototheque/management/folder_edit.html', {
        'page_title': 'Renommer un dossier', 'form': form, 'folder': folder,
    }, status=status))


@catalog_required
@require_http_methods(['GET', 'POST'])
def photo_action(request, pk, action):
    if action not in ('archive', 'restore'):
        raise Http404
    photo = _object(Photo, pk)
    back = _back_query(request)
    form = PhotoActionForm(request.POST if request.method == 'POST' else None)
    status = 200
    if request.method == 'POST':
        if form.is_valid():
            try:
                service = services.archive_photo if action == 'archive' else services.restore_photo
                service(photo.pk, actor=request.user)
            except (services.PhotoError, ValidationError) as error:
                form.add_error(None, error); status = 400
            else:
                messages.success(request, 'La photo est archivée. Les pages qui l’utilisent restent inchangées.' if action == 'archive'
                    else 'La photo est à nouveau disponible dans la bibliothèque.')
                return _private(redirect(_detail_url(photo, back)))
        else:
            status = 400
    return _private(render(request, 'phototheque/management/action.html', {
        'page_title': 'Archiver la photo' if action == 'archive' else 'Réactiver la photo',
        'form': form, **_card(photo), 'action': action, 'back': back, 'back_url': _detail_url(photo, back),
    }, status=status))
