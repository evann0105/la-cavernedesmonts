from django.db.models import Q
from .models import Product, HomepageSelection

WORLD_CARDS = (
    {'default_slug':'veste-tri-matieres-femme-blanche-capuche', 'slot':'femmes', 'label':'Femme', 'style':'women', 'title':'Bien dans sa nature.'},
    {'default_slug':'blouson-softshell-homme-impermeable', 'slot':'hommes', 'label':'Homme', 'style':'men', 'title':'L’appel du dehors.'},
    {'default_slug':'enfant-74-polaire-garcon-ceven', 'slot':'enfants', 'label':'Enfant', 'style':'kids', 'title':'Grandir au grand air.'},
)


def eligible_products(slot):
    return Product.objects.filter(is_published=True).filter(Q(category__slug=slot) | Q(collections__slug=slot)).exclude(Q(main_image='') | Q(main_image__isnull=True), static_image='').distinct().order_by('name', 'pk')


def world_cards():
    chosen = dict(HomepageSelection.objects.values_list('slot', 'product_id'))
    cards = []
    for spec in WORLD_CARDS:
        eligible = eligible_products(spec['slot'])
        product = eligible.filter(pk=chosen.get(spec['slot'])).first()
        if spec['slot'] not in chosen:
            product = eligible.filter(slug=spec['default_slug']).first()
        product = product or eligible.first()
        cards.append({**spec, 'product':product})
    return cards
