"""Human-readable product differences; never print raw snapshots or file paths."""
from decimal import Decimal, InvalidOperation

from .media import image_url


def _money(value):
    if value is None or value == '':
        return 'Prix général du produit'
    try:
        return f'{Decimal(str(value)):.2f} €'.replace('.', ',')
    except (InvalidOperation, ValueError):
        return 'Montant non disponible'


def _text(value):
    return str(value) if value is not None and value != '' else 'Non renseigné'


def _image(name, kind, version, alt='Photo du produit'):
    url = image_url(name, kind, version=version) if name else ''
    return {'url': url, 'alt': alt, 'missing': bool(name and not url), 'empty': not bool(name)}


def _category(value, snapshot):
    if not value:
        return 'Sans rubrique'
    return snapshot.get('category_labels', {}).get(str(value), f'Rubrique n° {value}')


def _sources(value):
    lines = []
    labels = {'brand': 'Marque', 'model_reference': 'Référence', 'composition': 'Composition', 'brand_contact': 'Responsable de la marque'}
    for key, item in (value or {}).items():
        if key not in labels or not isinstance(item, dict):
            continue
        text = labels[key] + ' : ' + _text(item.get('value'))
        if item.get('checked_on'):
            text += ' · vérifié le ' + str(item['checked_on'])
        lines.append(text)
        for source in item.get('sources', []):
            if isinstance(source, dict):
                lines.append(str(source.get('title') or source.get('url') or 'Source conservée'))
    return lines or ['Aucune référence de vérification renseignée']


def product_changes(before, after, *, before_version=None, after_version=None):
    before, after = before or {}, after or {}
    old, new = before.get('fields', {}), after.get('fields', {})
    groups = []

    def section(title, rows):
        if rows:
            groups.append({'title': title, 'rows': rows})

    def row(label, a, b, kind='text'):
        return {'label': label, 'before': a, 'after': b, 'kind': kind}

    labels = {
        'name': 'Nom du produit', 'description': 'Description', 'brand': 'Marque',
        'model_reference': 'Référence du modèle', 'composition': 'Composition',
        'brand_contact': 'Responsable de la marque', 'benefits': 'Pourquoi le choisir ?',
        'information_review_notes': 'Notes de vérification — privées', 'sizes': 'Tailles annoncées',
        'main_color': 'Couleur principale',
    }
    def displayed(snapshot, fields, key):
        if key == 'benefits' and fields.get(key) is None:
            return '\n'.join(snapshot.get('resolved_benefits', []))
        return fields.get(key)
    texts = [row(label, _text(displayed(before, old, key)), _text(displayed(after, new, key)))
             for key, label in labels.items() if displayed(before, old, key) != displayed(after, new, key)]
    if old.get('information_sources') != new.get('information_sources'):
        texts.append(row('Informations vérifiées et leurs sources', _sources(old.get('information_sources')),
                         _sources(new.get('information_sources')), 'list'))
    for language in sorted(set(old.get('translations', {})) | set(new.get('translations', {}))):
        old_language = old.get('translations', {}).get(language, {})
        new_language = new.get('translations', {}).get(language, {})
        for key, label in labels.items():
            if old_language.get(key) != new_language.get(key):
                texts.append(row(f'{label} · {language.upper()}', _text(old_language.get(key)), _text(new_language.get(key))))
    section('Textes et informations du produit', texts)

    from core.models import Product
    classification = []
    if old.get('category_id') != new.get('category_id'):
        classification.append(row('Rubrique principale', _category(old.get('category_id'), before), _category(new.get('category_id'), after)))
    if before.get('collections', []) != after.get('collections', []):
        classification.append(row('Rubriques complémentaires',
            [_category(pk, before) for pk in before.get('collections', [])] or ['Aucune'],
            [_category(pk, after) for pk in after.get('collections', [])] or ['Aucune'], 'list'))
    for key, label in (('article_type', 'Type d’article'), ('availability', 'Disponibilité annoncée')):
        if old.get(key) != new.get(key):
            choices = dict(Product._meta.get_field(key).choices)
            classification.append(row(label, str(choices.get(old.get(key), 'Classement automatique')),
                                      str(choices.get(new.get(key), 'Classement automatique'))))
    for key, label, yes, no in (
        ('is_published', 'Visibilité', 'Publié', 'Brouillon / retiré'),
        ('is_featured', 'Mise en avant', 'Mis en avant', 'Sans mise en avant'),
        ('include_imported_gallery', 'Photos d’origine', 'Affichées', 'Masquées'),
    ):
        if old.get(key) != new.get(key):
            classification.append(row(label, yes if old.get(key) else no, yes if new.get(key) else no))
    section('Classement et visibilité', classification)

    photos = []
    old_main = (old.get('main_image') or old.get('static_image') or '', 'media' if old.get('main_image') else 'static')
    new_main = (new.get('main_image') or new.get('static_image') or '', 'media' if new.get('main_image') else 'static')
    if old_main != new_main:
        photos.append(row('Photo principale', _image(*old_main, before_version), _image(*new_main, after_version), 'image'))
    for key, title in (('gallery', 'Photo de galerie'), ('colors', 'Photo de couleur')):
        old_rows = {item['id']: item for item in before.get(key, [])}
        new_rows = {item['id']: item for item in after.get(key, [])}
        for index, pk in enumerate(dict.fromkeys([*old_rows, *new_rows]), 1):
            a, b = old_rows.get(pk, {}), new_rows.get(pk, {})
            label = f'{title} · ' + (str(b.get('name') or a.get('name')) if key == 'colors' else str(index))
            if a.get('image') != b.get('image'):
                photos.append(row(label, _image(a.get('image'), 'media', before_version, a.get('alt') or a.get('name') or label),
                    _image(b.get('image'), 'media', after_version, b.get('alt') or b.get('name') or label), 'image'))
            for field, field_label in (('alt', 'description'), ('order', 'position'), ('name', 'couleur')):
                if a.get(field) != b.get(field):
                    photos.append(row(label + ' · ' + field_label, _text(a.get(field)), _text(b.get(field))))
    if before.get('imported_gallery', []) != after.get('imported_gallery', []):
        old_imported, new_imported = before.get('imported_gallery', []), after.get('imported_gallery', [])
        for position in range(max(len(old_imported), len(new_imported))):
            index = position + 1
            previous = old_imported[position] if position < len(old_imported) else {}
            item = new_imported[position] if position < len(new_imported) else {}
            if previous != item:
                photos.append(row(f'Photo d’origine · {index}',
                    _image(previous.get('name'), previous.get('kind', 'static'), before_version, previous.get('alt', 'Photo d’origine')),
                    _image(item.get('name'), item.get('kind', 'static'), after_version, item.get('alt', 'Photo d’origine')), 'image'))
    section('Photos et galerie', photos)

    prices = []
    if old.get('price') != new.get('price'):
        prices.append(row('Prix général', _money(old.get('price')), _money(new.get('price'))))
    old_variants = {item['id']: item for item in before.get('variants', [])}
    new_variants = {item['id']: item for item in after.get('variants', [])}
    for pk in dict.fromkeys([*old_variants, *new_variants]):
        a, b = old_variants.get(pk, {}), new_variants.get(pk, {})
        label = ' · '.join(str((b or a).get(field) or fallback) for field, fallback in (('size', 'Sans taille'), ('color', 'Sans couleur')))
        if a.get('price') != b.get('price') or bool(a) != bool(b):
            prices.append(row('Prix · ' + label, _money(a.get('price')) if a else 'Déclinaison absente',
                              _money(b.get('price')) if b else 'Déclinaison absente'))
        for field, field_label in (('size', 'Taille'), ('color', 'Couleur'), ('reference', 'Référence')):
            if a.get(field) != b.get(field):
                prices.append(row(field_label + ' · ' + label, _text(a.get(field)), _text(b.get(field))))
    section('Prix et déclinaisons', prices)
    return groups
