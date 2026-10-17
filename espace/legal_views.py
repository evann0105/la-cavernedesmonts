from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET
from core.catalog_access import catalog_required
from .legal import context, blockers

TITLES = {'reviews':'Présentation des avis', 'notice':'Mentions légales', 'terms':'Conditions générales de vente',
          'privacy':'Confidentialité et données personnelles', 'cookies':'Cookies',
          'withdrawal':'Rétractation et garanties', 'accessibility':'Accessibilité'}

@require_GET
def document(request, page):
    return render(request,'espace/legal/'+page+'.html',{'title':TITLES[page], **context()})

@require_GET
def withdrawal_form(request):
    from django.template.loader import render_to_string
    response=HttpResponse(render_to_string('espace/legal/withdrawal_form.txt',context()),content_type='text/plain; charset=utf-8')
    response['Content-Disposition']='attachment; filename="formulaire-retractation.txt"'
    return response

@catalog_required
@require_GET
def readiness(request):
    return render(request,'espace/legal/readiness.html',{'title':'Préparer l’ouverture des ventes', **context(), 'blockers':blockers()})
