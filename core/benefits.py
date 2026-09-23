"""Reviewed catalog copy, valid only while its supporting description is unchanged."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from django.utils.translation import gettext_lazy as _

COPY = {
    'warm': _('Une polaire chaude pour ajouter une couche de confort lors des journées fraîches.'),
    'soft': _('Une texture douce et moelleuse pour les moments de détente au quotidien.'),
    'stretch': _('Un tissu souple et extensible pour accompagner vos mouvements au quotidien.'),
    'pockets': _('Des poches zippées pour garder vos petits essentiels à portée de main.'),
    'hood': _('Une capuche amovible pour adapter la veste à vos envies.'),
    'zip': _('Une ouverture zippée pour enfiler et retirer facilement la veste.'),
    'socks': _('Une maille à bouclettes souple et extensible pour envelopper les petits pieds.'),
    'snaps': _('Des boutons-pression à l’entrejambe pour faciliter le change de bébé.'),
    'wash': _('Lavable en machine pour simplifier le quotidien ; suivez les consignes de l’étiquette.'),
    'cuffs': _('Des finitions élastiques aux manches pour un ajustement confortable aux poignets.'),
    'collar': _('Un col montant pour couvrir le cou lors des sorties au frais.'),
    'layer': _('À porter en veste à la mi-saison ou sous un manteau quand il fait plus frais.'),
}


@lru_cache(maxsize=1)
def reviewed_catalog():
    return json.loads(Path(__file__).with_name('product_benefits.json').read_text())


def catalog_benefits(product):
    entry = reviewed_catalog().get(product.slug)
    if not entry or entry['description_sha256'] != hashlib.sha256(product.description.encode()).hexdigest():
        return []
    return [str(COPY[key]) for key in entry['benefits']]
