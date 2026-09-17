from collections import Counter
from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.utils.translation import gettext as _
from django.db.models import Q
from connexion.email_verification import email_verified
from core.models import Product, Category
from paiement.models import Order
from .models import Address, CreditNote, Voucher
from .forms import AddressForm, PersonalForm


def private(view):
    @wraps(view)
    def guarded(request, *args, **kwargs):
        if not email_verified(request.user):
            return redirect('connexion:verify_email')
        return view(request, *args, **kwargs)
    return never_cache(login_required(guarded))


def collection(request, kind):
    products = Product.objects.filter(is_published=True).select_related('category')
    titles = {'promotions':_('Promotions'), 'new':_('Nouveaux produits'), 'best':_('Meilleures ventes')}
    intros = {'promotions':_('Les belles occasions du moment.'), 'new':_('Les derniers ajouts à notre catalogue, à découvrir à votre rythme.'), 'best':_('Les articles les plus commandés sur cette boutique en ligne.')}
    if kind == 'promotions':
        products = products.filter(Q(category__slug='offres-speciales') | Q(collections__slug='offres-speciales')).distinct().order_by('price')
    elif kind == 'new':
        products = products.order_by('-created_at','-pk')[:24]
    else:
        counts = Counter()
        for items in Order.objects.filter(status='paid').values_list('items', flat=True):
            for item in items:
                counts[item['product']] += item['quantity']
        available = {p.pk:p for p in products.filter(pk__in=counts)}
        products = [available[pk] for pk,_ in counts.most_common() if pk in available][:24]
    return render(request,'espace/collection.html',{'title':titles[kind],'intro':intros[kind],'products':products})


def info(request, page):
    titles = {'contact':_('Contactez-nous'), 'about':_('À propos'), 'terms':_('Conditions d’utilisation'), 'sitemap':_('Plan du site')}
    return render(request, 'espace/'+page+'.html', {'title':titles[page], 'categories':Category.objects.all()})


@private
def account(request):
    return render(request,'espace/account.html',{'title':_('Mon compte')})


@private
def orders(request):
    return render(request,'espace/orders.html',{'title':_('Mes commandes'),'orders':Order.objects.filter(user=request.user).order_by('-created_at')})


@private
def order_detail(request, pk):
    order=get_object_or_404(Order, pk=pk, user=request.user)
    return render(request,'espace/order.html',{'title':_('Détail de la commande'),'order':order})


@private
def addresses(request):
    return render(request,'espace/addresses.html',{'title':_('Mes adresses'),'addresses':Address.objects.filter(user=request.user)})


@private
def address_edit(request, pk=None):
    address=get_object_or_404(Address, pk=pk, user=request.user) if pk else Address(user=request.user)
    form=AddressForm(request.POST if request.method=='POST' else None, instance=address)
    if request.method=='POST' and form.is_valid():
        form.save(); messages.success(request,_('Adresse enregistrée.')); return redirect('espace:addresses')
    return render(request,'espace/form.html',{'title':_('Mon adresse'),'form':form})


@private
@require_POST
def address_delete(request, pk):
    get_object_or_404(Address,pk=pk,user=request.user).delete()
    messages.success(request,_('Adresse supprimée.'))
    return redirect('espace:addresses')


@private
def personal(request):
    form=PersonalForm(request.POST if request.method=='POST' else None,instance=request.user)
    if request.method=='POST' and form.is_valid():
        form.save()
        if not email_verified(request.user):
            from connexion.email_verification import send_verification
            send_verification(request.user)
            return redirect('connexion:verify_email')
        messages.success(request,_('Vos informations ont été enregistrées.'))
        return redirect('espace:personal')
    return render(request,'espace/form.html',{'title':_('Mes informations personnelles'),'form':form})


@private
def credits(request):
    return render(request,'espace/credits.html',{'title':_('Mes avoirs'),'credits':CreditNote.objects.filter(order__user=request.user)})


@private
def vouchers(request):
    return render(request,'espace/vouchers.html',{'title':_('Mes bons de réduction'),'vouchers':Voucher.objects.filter(user=request.user)})
