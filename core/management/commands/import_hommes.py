from .import_femmes import Command as CollectionImportCommand


class Command(CollectionImportCommand):
    help = 'Importe les 25 références Homme relevées sur le site d’origine le 17/09/2026.'
    seed_file = 'hommes_seed.json'
    category_slug = 'hommes'
    category_name = 'Hommes'
    # Correct the original seed classification once, on first source association.
    corrected_categories = {'veste-polaire-de-montagne-rouge': 'femmes'}
