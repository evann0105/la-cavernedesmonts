"""Fresh, factual product search copy, derived from the catalogue itself.

The phrases are an editorial aid, never a ``meta keywords`` tag. This module
does not invent technical characteristics, availability, brands or reviews,
and never overwrites the merchant's description.
"""
from dataclasses import dataclass
from html import unescape
import re
import unicodedata

from django.utils.html import strip_tags


SHOP_NAME = 'La Caverne des Monts'
TITLE_MAX_LENGTH = 95
DESCRIPTION_MAX_LENGTH = 165


@dataclass(frozen=True)
class ProductSEO:
    title: str
    description: str
    keywords: list[str]
    intro: str
    warnings: list[str]


def clean_text(value):
    """Return plain readable text; templates must still escape their output."""
    text = unescape(str(value or ''))
    text = re.sub(r'<(script|style)\b[^>]*>.*?</\1\s*>', ' ', text, flags=re.I | re.S)
    text = strip_tags(text)
    text = ''.join(' ' if unicodedata.category(char) in ('Cc', 'Zl', 'Zp') else char
                   for char in text if unicodedata.category(char) != 'Cf')
    return re.sub(r'\s+', ' ', text).strip()


def _key(value):
    text = unicodedata.normalize('NFKD', clean_text(value)).casefold()
    return re.sub(r'[^\w]+', ' ', ''.join(char for char in text if not unicodedata.combining(char))).strip()


def _contains(text, phrase):
    phrase = _key(phrase)
    return bool(phrase) and f' {phrase} ' in f' {_key(text)} '


def _shorten(text, limit):
    text = clean_text(text)
    if len(text) <= limit:
        return text
    candidate = text[:max(1, limit - 1)].rsplit(' ', 1)[0]
    if len(candidate) < limit // 2:
        candidate = text[:max(1, limit - 1)]
    return candidate.rstrip(' ,;:.\u2026-') + '\u2026'


def _middle_shorten(text, limit):
    """Retain the article at the start and its distinguishing colour at the end."""
    text = clean_text(text)
    if len(text) <= limit:
        return text
    if limit < 12:
        return _shorten(text, limit)
    available = limit - len(' \u2026 ')
    head_budget = round(available * .53)
    tail_budget = available - head_budget
    head = text[:head_budget + 1].rsplit(' ', 1)[0]
    tail = text[-tail_budget - 1:].split(' ', 1)[-1]
    # Preserve whole words whenever possible, even for a long model code.
    if not head or len(head) > head_budget:
        head = text[:head_budget]
    if not tail or len(tail) > tail_budget:
        tail = text[-tail_budget:]
    return head.rstrip(' ,;:.\u2026-') + ' \u2026 ' + tail.lstrip(' ,;:.\u2026-')


def _without_phrase(text, phrase):
    """Remove literal brand token runs while retaining all other name facts."""
    wanted = _key(phrase).split()
    if not wanted:
        return text
    while True:
        words = list(re.finditer(r'\w+', text))
        keys = [_key(word.group()) for word in words]
        start = next((index for index in range(len(words) - len(wanted) + 1)
                      if keys[index:index + len(wanted)] == wanted), None)
        if start is None:
            break
        end = start + len(wanted) - 1
        text = clean_text(text[:words[start].start()] + ' ' + text[words[end].end():])
    return text.strip(' ,;:.-')


def _unique(values, *, limit=None):
    result, seen = [], set()
    for value in values:
        value = clean_text(value).strip(' ,;:.')
        key = _key(value)
        if value and key and key not in seen:
            result.append(value)
            seen.add(key)
            if limit and len(result) >= limit:
                break
    return result


def _loaded_variants(product, variants):
    if variants is not None:
        return list(variants)
    cache = getattr(product, '_prefetched_objects_cache', {})
    if 'stock_variants' in cache:
        return list(cache['stock_variants'])
    # Metadata remains useful even in a simple catalogue queryset. Callers
    # needing variant details pass their already-loaded list: no hidden N+1.
    return None


def _audience(product, name):
    category = getattr(product, 'category', None)
    category_slug = _key(getattr(category, 'slug', ''))
    from_category = {
        'femmes': 'femme', 'femme': 'femme', 'hommes': 'homme', 'homme': 'homme',
        'enfants': 'enfant', 'enfant': 'enfant',
        'accessoires bebe': 'b\u00e9b\u00e9', 'polaires bebe': 'b\u00e9b\u00e9',
    }.get(category_slug, '')
    literal = [label for words, label in (
        (('femme', 'femmes'), 'femme'), (('homme', 'hommes'), 'homme'),
        (('b\u00e9b\u00e9', 'b\u00e9b\u00e9s'), 'b\u00e9b\u00e9'),
        (('enfant', 'enfants', 'junior', 'ado', 'adolescent'), 'enfant'),
    ) if any(_contains(name, word) for word in words)]
    if _contains(name, 'mixte') or len(literal) > 1:
        return 'mixte' if _contains(name, 'mixte') else '', False
    if literal and from_category and literal[0] != from_category:
        return '', True
    return literal[0] if literal else from_category, False


def _product_type(product, name):
    # Only words already present in the product name, or its explicit type.
    for phrase, label in (
        ('veste polaire', 'veste polaire'), ('gilet polaire', 'gilet polaire'),
        ('pull polaire', 'pull polaire'), ('polaire', 'polaire'), ('polaires', 'polaire'),
        ('blouson', 'blouson'), ('doudoune', 'doudoune'), ('veste', 'veste'),
        ('parka', 'parka'), ('softshell', 'softshell'), ('t shirt', 't-shirt'),
        ('tee shirt', 't-shirt'), ('sweat', 'sweat'), ('pull', 'pull'),
        ('pantalon', 'pantalon'), ('short', 'short'), ('bermuda', 'bermuda'),
        ('legging', 'legging'), ('chemise', 'chemise'), ('robe', 'robe'),
        ('chaussettes', 'chaussettes'), ('chaussette', 'chaussettes'),
        ('chaussons', 'chaussons'), ('body', 'body'), ('bodies', 'body'),
        ('bonnet', 'bonnet'), ('casque', 'casque'), ('gants', 'gants'),
        ('gant', 'gants'), ('\u00e9charpe', '\u00e9charpe'), ('tour de cou', 'tour de cou'),
        ('casquette', 'casquette'), ('chapeau', 'chapeau'), ('doudou', 'doudou'),
        ('poncho', 'poncho'),
    ):
        if _contains(name, phrase):
            return label
    return {
        'polaires': 'polaire', 'vestes': 'veste', 'hauts': 'haut',
        'pantalons': 'pantalon', 'accessoires': 'accessoire',
    }.get(getattr(product, 'article_type', ''), '')


def _title(name, brand):
    suffix = f' | {SHOP_NAME}'
    budget = TITLE_MAX_LENGTH - len(suffix)
    if not brand:
        text = _middle_shorten(name, budget)
    elif len(brand) < budget // 2:
        # Keep the exact stored brand once, and reserve room for it before
        # shortening. A simple end cut used to erase Noir / Blanc / Bleu marine
        # and produce identical titles for distinct colours of the same item.
        without_brand = _without_phrase(name, brand) if _contains(name, brand) else name
        body = _middle_shorten(without_brand, budget - len(brand) - 1)
        text = f'{body} {brand}'.strip()
    else:
        text = _middle_shorten(f'{name} {brand}', budget)
    return text + suffix


def build_product_seo(product, variants=None):
    """Build metadata for both existing and newly created products.

    ``variants`` is an optional iterable of loaded stock variants. Omitting it
    uses a prefetch cache when present, otherwise the product's saved size and
    main-colour fields. No stock manager query or mutation occurs here.
    """
    name = clean_text(getattr(product, 'name', ''))
    brand = clean_text(getattr(product, 'brand', ''))
    model = clean_text(getattr(product, 'model_reference', ''))
    brand = brand if _key(brand) else ''
    model = model if _key(model) else ''
    merchant_description = clean_text(getattr(product, 'description', ''))
    composition = clean_text(getattr(product, 'composition', ''))
    warnings = []
    if not _key(name):
        name = 'Article de la boutique'
        warnings.append('Renseignez un nom pr\u00e9cis pour distinguer cet article.')
    if not brand:
        warnings.append('Marque non renseign\u00e9e : ajoutez uniquement celle indiqu\u00e9e sur le produit.')
    if not _key(merchant_description):
        warnings.append('Description manquante : pr\u00e9cisez les caract\u00e9ristiques v\u00e9rifi\u00e9es du produit.')
    if not (getattr(product, 'main_image', None) or getattr(product, 'static_image', '')):
        warnings.append('Ajoutez une photo principale pour pr\u00e9senter cet article.')

    loaded = _loaded_variants(product, variants)
    if loaded is None:
        sizes = _unique(str(getattr(product, 'sizes', '') or '').split(','))
        colors = _unique([getattr(product, 'main_color', '')])
    else:
        sizes = _unique(getattr(variant, 'size', '') for variant in loaded)
        colors = _unique(getattr(variant, 'color', '') for variant in loaded)
        if not loaded:
            colors = _unique([getattr(product, 'main_color', '')])
    sizes = [size for size in sizes if _key(size) not in ('taille unique', 'unique', 'a confirmer')]
    colors = [color for color in colors if _key(color) not in ('coloris unique', 'unique', 'a confirmer')]
    audience, conflicting_audience = _audience(product, name)
    if conflicting_audience:
        warnings.append('Le nom et la cat\u00e9gorie indiquent des publics diff\u00e9rents : v\u00e9rifiez le classement.')
    kind = _product_type(product, name)
    subject = ' '.join(part for part in (kind, audience, brand) if part)
    identity = name
    if brand and not _contains(identity, brand):
        identity += f' {brand}'
    if model and not _contains(identity, model):
        identity += f' {model}'
    if not kind:
        subject = identity

    # The most specific queries come first. A small bounded set is useful to
    # the editor; repeating every size/colour combination would be spam.
    phrases = [subject, identity]
    if kind and brand:
        phrases.append(f'{kind} {brand}')
    if kind and audience:
        phrases.append(f'{kind} {audience}')
    if model:
        phrases.append(' '.join(part for part in (brand, model) if part))
    if subject:
        phrases.extend(f'{subject} {color}' for color in colors[:4])
        phrases.extend(f'{subject} taille {size}' for size in sizes[:3])
        phrases.append(f'{subject} L\u00e9lex')
    keywords = _unique(phrases, limit=12)

    facts = []
    if colors:
        facts.append(f"Coloris : {', '.join(colors[:4])}.")
    if sizes:
        facts.append(f"Tailles : {', '.join(sizes[:6])}.")
    if composition:
        facts.append(f'Composition : {_shorten(composition, 110).rstrip(".")}.')
    intro = ' '.join([identity.rstrip('.!?') + '.'] + facts)

    # Keep the exact product identity first and choose useful facts before
    # a local shop attribution. The merchant's copy is not altered in storage.
    pieces = [_middle_shorten(identity, 92).rstrip('.!?') + '.']
    if merchant_description:
        first_sentence = re.split(r'(?<=[.!?])\s+', merchant_description)[0]
        if not _contains(identity, first_sentence):
            pieces.append(first_sentence.rstrip('.!?') + '.')
    pieces.extend(facts)
    description = pieces[0]
    ending = f' Chez {SHOP_NAME}, \u00e0 L\u00e9lex.'
    for piece in pieces[1:]:
        if len(description) + len(piece) + len(ending) + 1 <= DESCRIPTION_MAX_LENGTH:
            description += ' ' + piece
    if len(description) + len(ending) <= DESCRIPTION_MAX_LENGTH:
        description += ending
    # A concise, truthful shop attribution also helps products whose identity
    # is short. Do not pad an incomplete product with invented qualities.
    longer_ending = f' Retrouvez cet article chez {SHOP_NAME}, boutique \u00e0 L\u00e9lex.'
    if len(description) < 145 and description.endswith(ending):
        alternative = description[:-len(ending)] + longer_ending
        if len(alternative) <= DESCRIPTION_MAX_LENGTH:
            description = alternative
    return ProductSEO(
        title=_title(name, brand), description=_shorten(description, DESCRIPTION_MAX_LENGTH),
        keywords=keywords, intro=intro, warnings=warnings,
    )
