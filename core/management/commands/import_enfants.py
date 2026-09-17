from .import_femmes import Command as CollectionImportCommand


class Command(CollectionImportCommand):
    help = 'Importe les 32 références Enfant relevées sur le site d’origine le 17/09/2026.'
    seed_file = 'enfants_seed.json'
    category_slug = 'enfants'
    category_name = 'Enfants'
    include_baby_originals = True
    # Baby products also appear in Enfant, without losing their original category.
    share_collection = True
