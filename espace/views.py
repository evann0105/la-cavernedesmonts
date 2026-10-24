from collections import Counter
from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.utils.translation import gettext as _
from django.utils import timezone
from django.db.models import Q
from connexion.email_verification import email_access_allowed
from core.models import Product, Category, ProductPromotion
from core.pricing import with_catalog_prices, load_pricing_context, price_quote
from paiement.models import Order
from .models import Address, CreditNote, Voucher
from .forms import AddressForm, PersonalForm
from connexion.security import PasswordCheckLimited, lockout


def private(view):
    @wraps(view)
    def guarded(request, *args, **kwargs):
        if not email_access_allowed(request.user):
            return redirect('connexion:verify_email')
        return view(request, *args, **kwargs)
    return never_cache(login_required(guarded))


def collection(request, kind):
    products = Product.objects.filter(is_published=True).select_related('category')
    pricing_at = timezone.now()
    titles = {'promotions':_('Promotions'), 'new':_('Nouveaux produits'), 'best':_('Meilleures ventes')}
    intros = {'promotions':_('Les belles occasions du moment.'), 'new':_('Les derniers ajouts à notre catalogue, à découvrir à votre rythme.'), 'best':_('Les articles les plus commandés sur cette boutique en ligne.')}
    if kind == 'promotions':
        # Confirm the actual price, not merely the existence of a scheduled row:
        # expired, future, malformed or reference-invalid campaigns are excluded.
        candidate_ids = ProductPromotion.objects.filter(
            product_id__in=products.values('pk'), enabled=True, cancelled_at__isnull=True,
            reference_confirmed=True, starts_at__lte=pricing_at, ends_at__gt=pricing_at,
        ).values_list('product_id', flat=True).distinct()
        candidates = load_pricing_context(products.filter(pk__in=candidate_ids), at=pricing_at)
        discounted_ids = []
        for product in candidates:
            variants = product._pricing_context.variants.get(product.pk, [])
            if any(price_quote(product, variant, at=pricing_at).has_discount for variant in (variants or [None])):
                discounted_ids.append(product.pk)
        products = with_catalog_prices(products.filter(
            Q(category__slug='offres-speciales') | Q(collections__slug='offres-speciales') |
            Q(pk__in=discounted_ids),
        ).distinct(), at=pricing_at).order_by('_catalog_min_price', 'pk')
    elif kind == 'new':
        products = with_catalog_prices(products, at=pricing_at).order_by('-created_at','-pk')[:24]
    else:
        counts = Counter()
        for items in Order.objects.filter(status='paid').values_list('items', flat=True):
            for item in items:
                counts[item['product']] += item['quantity']
        available = {p.pk:p for p in with_catalog_prices(products.filter(pk__in=counts), at=pricing_at)}
        products = [available[pk] for pk,_ in counts.most_common() if pk in available][:24]
    return render(request,'espace/collection.html',{'title':titles[kind],'intro':intros[kind],'products':products})


def info(request, page):
    if page == 'terms':
        from .legal_views import document
        return document(request, 'terms')
    titles = {'contact':_('Contactez-nous'), 'about':_('À propos'), 'terms':_('Conditions d’utilisation'), 'sitemap':_('Plan du site')}
    editorial_page = None
    if page == 'about':
        from boutique.editorial_public import page as public_page
        editorial_page = public_page(request, 'about')
        titles['about'] = editorial_page['values']['page_title']
    return render(request, 'espace/'+page+'.html', {'title':titles[page], 'editorial_page': editorial_page, 'categories':Category.objects.all(), 'sitemap_products':Product.objects.filter(is_published=True).only('name', 'slug').order_by('name') if page == 'sitemap' else []})


@private
def account(request):
    return render(request,'espace/account.html',{'title':_('Mon compte')})


@private
def orders(request):
    return render(request,'espace/orders.html',{'title':_('Mes commandes'),'orders':Order.objects.filter(user=request.user).select_related('fulfillment').order_by('-created_at')})


@private
def order_detail(request, pk):
    order=get_object_or_404(Order.objects.select_related('fulfillment'), pk=pk, user=request.user)
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
    try:
        valid = request.method=='POST' and form.is_valid()
    except PasswordCheckLimited:
        return lockout(request)
    if valid:
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            if not get_user_model().objects.filter(email__iexact=form.cleaned_data['email']).exclude(pk=request.user.pk).exists():
                raise
            form.add_error('email', _('Cette adresse e-mail est déjà utilisée.'))
            return render(request,'espace/form.html',{'title':_('Mes informations personnelles'),'form':form})
        if not email_access_allowed(request.user):
            from connexion.email_verification import send_verification
            send_verification(request.user)
            return redirect('connexion:verify_email')
        messages.success(request,_('Vos informations ont été enregistrées.'))
        return redirect('espace:personal')
    return render(request,'espace/form.html',{'title':_('Mes informations personnelles'),'form':form})


@private
def credits(request):
    from avoirs.access import benefits_access_allowed, benefits_for
    from connexion.email_verification import email_verified
    verified = email_verified(request.user)
    benefits = benefits_for(request.user).prefetch_related('redemptions').order_by('-created_at')
    priority = {'available': 0, 'scheduled': 1, 'reserved': 2, 'used': 3, 'expired': 4, 'inactive': 5}
    benefits = sorted(benefits, key=lambda benefit: (priority[benefit.status], -benefit.pk))
    return render(request, 'espace/credits.html', {
        'title': _('Mes avoirs'), 'credits': CreditNote.objects.filter(order__user=request.user),
        'benefits': benefits, 'benefits_email_verified': verified,
        'benefits_access_allowed': benefits_access_allowed(request.user),
    })


@private
def vouchers(request):
    return render(request,'espace/vouchers.html',{'title':_('Mes bons de réduction'),'vouchers':Voucher.objects.filter(user=request.user)})
