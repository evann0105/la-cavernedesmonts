from django.contrib import messages
from django.core import signing
from django.db import IntegrityError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods
from core.catalog_access import catalog_required
from .importing import MAX_BYTES, ImportInvalid, export_inventory, parse_inventory, process_inventory


@catalog_required
@require_GET
def download(request):
    response = HttpResponse(export_inventory(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="inventaire-variantes.csv"'
    response['Cache-Control'] = 'no-store'
    return response


@catalog_required
@require_http_methods(['GET', 'POST'])
def inventory(request):
    context = {}
    if request.method == 'POST':
        try:
            if request.POST.get('confirmation'):
                payload = signing.loads(request.POST['confirmation'], salt='stock-import', max_age=1800)
                if payload['user'] != request.user.pk:
                    raise ImportInvalid('Cet aperçu appartient à un autre compte.')
                process_inventory(payload['rows'], request.user, apply=True)
                messages.success(request, 'Import terminé. Les variantes et stocks ont été enregistrés.')
                return redirect('stocks:overview')
            upload = request.FILES.get('file')
            if not upload:
                raise ImportInvalid('Choisissez un fichier CSV.')
            rows = parse_inventory(upload.read(MAX_BYTES + 1))
            context['preview'] = process_inventory(rows, request.user)
            context['confirmation'] = signing.dumps({'user':request.user.pk, 'rows':rows}, salt='stock-import', compress=True)
        except signing.BadSignature:
            context['error'] = 'Aperçu expiré ou invalide. Rechargez le fichier pour le vérifier.'
        except ImportInvalid as exc:
            context['error'] = str(exc)
        except IntegrityError:
            context['error'] = 'Le catalogue a changé. Aucun changement importé : téléchargez un nouvel export.'
    return render(request, 'stocks/import.html', context)
