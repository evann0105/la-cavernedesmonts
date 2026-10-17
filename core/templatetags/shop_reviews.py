"""Selected shop reviews transcribed by the owner of this project on 2026-09-23.

Visit months are supplied visit dates, not publication dates. No live Google
rating, purchase verification or product-specific endorsement is inferred.
The Google link opens the shop's reviews search, not an individual review.
"""
from datetime import date
from random import sample
from django import template

register = template.Library()
REVIEWS = (
    {'author': 'Nathalie Largillet', 'rating': 5, 'visited': date(2021, 10, 1),
     'quote': 'Accueil très chaleureux et magasin super. Je trouve toujours mon bonheur. Je conseille fortement.'},
    {'author': 'Annette Morel', 'rating': 5, 'visited': date(2023, 2, 1),
     'quote': 'Prix - qualité : parfait.\nVendeuse: très sympathique.'},
    {'author': 'Clemence Guillaume', 'rating': 4, 'visited': date(2021, 2, 1),
     'quote': 'Très bon accueil ds ce magasin et de bons conseils sont donnés aux clients concernant leurs achats.'},
    {'author': 'sophie Giradet', 'rating': 5, 'visited': None,
     'quote': "Vêtements à prix franchement raisonnable. j'ai découvert des marques que je ne connaissais pas... qualité top! Je conseille!"},
    {'author': 'Corinne Garcon', 'rating': 5, 'visited': date(2022, 2, 1),
     'quote': 'Très bien .chaque fois que je vais a lelex je m habille la bas petite boutique et une super patronne Très commerçante'},
    {'author': 'Mélanie Hépert', 'rating': 5, 'visited': None,
     'quote': 'polaire impecc et super douillet.je vais en recommandé une autre'},
)


def select_reviews(compact=False):
    # Editorial selection of supplied reviews, not a live or aggregate Google rating.
    eligible = [review for review in REVIEWS if 4 <= review['rating'] <= 5 and review.get('quote', '').strip()]
    if compact:
        # Keep the advice-focused quote on product pages, where context matters.
        return [review for review in eligible if review['author'] == 'Clemence Guillaume'][:1]
    return sample(eligible, min(3, len(eligible)))


@register.inclusion_tag('core/partials/shop_reviews.html')
def shop_reviews(compact=False):
    return {
        'compact': compact,
        'reviews': select_reviews(compact),
        'reviews_url': 'https://www.google.com/search?q=LA+CAVERNE+DES+MONTS+L%C3%A9lex+avis',
    }
