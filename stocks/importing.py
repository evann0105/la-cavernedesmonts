"""Versioned stock inventory CSV: validate everything before any write."""
import csv
import io
import re
from django.contrib.admin.models import LogEntry, ADDITION, CHANGE
from django.db import transaction
from core.models import Product
from .forms import VariantForm
from .models import Variant

COLUMNS = ['produit_id', 'produit', 'variante_id', 'revision', 'taille', 'couleur', 'reference', 'quantite']
MAX_BYTES = 2 * 1024 * 1024


class ImportInvalid(ValueError):
    pass


def parse_inventory(data):
    if len(data) > MAX_BYTES:
        raise ImportInvalid('Le fichier dépasse 2 Mo.')
    try:
        reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig')), delimiter=';', strict=True)
        if reader.fieldnames != COLUMNS:
            raise ImportInvalid('Les colonnes doivent correspondre au modèle CSV fourni (séparateur point-virgule).')
        rows = []
        for number, row in enumerate(reader, 2):
            if number > 1001:
                raise ImportInvalid('Import limité à 1 000 lignes par fichier.')
            if None in row or any(value is None for value in row.values()):
                raise ImportInvalid(f'Ligne {number} : nombre de colonnes incorrect.')
            if not any(value.strip() for value in row.values()):
                continue
            for key in ('produit_id', 'variante_id', 'revision', 'quantite'):
                value = row[key].strip()
                if value and not re.fullmatch(r'[0-9]{1,10}', value):
                    raise ImportInvalid(f'Ligne {number} : {key} doit être un entier positif ou nul.')
                row[key] = int(value) if value else None
            if not row['produit_id'] or row['revision'] is None or (row['variante_id'] is None and row['revision'] != 0):
                raise ImportInvalid(f'Ligne {number} : identifiant produit et révision obligatoires ; nouvelle variante = révision 0.')
            row['ligne'] = number
            rows.append(row)
        if not rows:
            raise ImportInvalid('Le fichier ne contient aucune variante.')
        return rows
    except (UnicodeError, csv.Error) as exc:
        raise ImportInvalid('Fichier illisible : utilisez le modèle CSV UTF-8.') from exc


@transaction.atomic
def process_inventory(rows, user, apply=False):
    products = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=[r['produit_id'] for r in rows]).order_by('pk')}
    variants = {v.pk: v for v in Variant.objects.select_for_update().filter(product_id__in=products).order_by('pk')}
    seen_ids, seen_combinations, plan = set(), set(), []
    for row in rows:
        prefix = f"Ligne {row['ligne']} : "
        product = products.get(row['produit_id'])
        if not product:
            raise ImportInvalid(prefix + 'produit introuvable.')
        old = variants.get(row['variante_id'])
        if row['variante_id'] is not None and (not old or old.product_id != product.pk):
            raise ImportInvalid(prefix + 'la variante ne correspond pas au produit.')
        if old and (old.pk in seen_ids or old.revision != row['revision']):
            raise ImportInvalid(prefix + 'variante répétée ou stock modifié depuis l’export. Téléchargez un nouvel export.')
        if old:
            seen_ids.add(old.pk)
        if old:
            row = dict(row)
            for column, attribute in (('taille', 'size'), ('couleur', 'color'), ('reference', 'reference')):
                original = getattr(old, attribute)
                if row[column] == spreadsheet_text(original):
                    row[column] = original
        # Use a separate model instance: ModelForm mutates it during validation.
        instance = Variant(pk=old.pk, product=product) if old else Variant(product=product)
        form = VariantForm({'size':row['taille'], 'color':row['couleur'], 'reference':row['reference'], 'quantity':row['quantite'], 'revision':row['revision']}, instance=instance)
        if not form.is_valid():
            raise ImportInvalid(prefix + ' '.join(str(error) for errors in form.errors.values() for error in errors))
        cleaned = form.cleaned_data
        combo = (product.pk, cleaned['size'].casefold(), cleaned['color'].casefold())
        if combo in seen_combinations:
            raise ImportInvalid(prefix + 'taille et couleur répétées dans le fichier.')
        seen_combinations.add(combo)
        if old and (cleaned['size'], cleaned['color']) != (old.size, old.color):
            raise ImportInvalid(prefix + 'les tailles et couleurs existantes doivent être modifiées depuis la fiche de stock.')
        if old and old.reserved and (cleaned['quantity'] is None or cleaned['quantity'] < old.reserved):
            raise ImportInvalid(prefix + f'{old.reserved} articles sont réservés : la quantité physique ne peut pas être inférieure.')
        saved = form.save(commit=False)
        saved.reserved = old.reserved if old else 0
        saved.revision = old.revision + 1 if old else 1
        plan.append((saved, old, product))
    result = []
    for saved, old, product in plan:
        changed = old is None or (old.quantity, old.reference) != (saved.quantity, saved.reference)
        result.append({'product':product.name, 'size':saved.size, 'color':saved.color, 'before':old.quantity if old else None, 'after':saved.quantity, 'reference':saved.reference, 'action':'Créer' if not old else ('Modifier' if changed else 'Inchangé')})
        if apply and changed:
            saved.save()
            LogEntry.objects.log_actions(user_id=user.pk, queryset=Variant.objects.filter(pk=saved.pk), action_flag=CHANGE if old else ADDITION, change_message=f'Import CSV : stock {old.quantity if old else "nouveau"} → {saved.quantity}, référence {saved.reference}.')
    return result


def spreadsheet_text(value):
    text = str(value)
    # Quoting alone does not prevent spreadsheet formula execution.
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


def export_inventory():
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter=';')
    writer.writerow(COLUMNS)
    for product in Product.objects.prefetch_related('stock_variants').order_by('pk'):
        variants = list(product.stock_variants.all())
        for variant in variants or [None]:
            writer.writerow([product.pk, spreadsheet_text(product.name), variant.pk if variant else '', variant.revision if variant else 0, spreadsheet_text(variant.size) if variant else '', spreadsheet_text(variant.color) if variant else '', spreadsheet_text(variant.reference) if variant else '', variant.quantity if variant and variant.quantity is not None else ''])
    return '\ufeff' + stream.getvalue()
