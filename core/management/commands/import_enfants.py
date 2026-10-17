from .import_femmes import Command as CollectionImportCommand


class Command(CollectionImportCommand):
    help = 'Importe les références Enfant ; les articles bébé et chaussettes restent dans Petits bonheurs.'
    seed_file = 'enfants_seed.json'
    category_slug = 'enfants'
    category_name = 'Enfants'
    include_baby_originals = True
    # Baby products are enriched from the source but never added to Enfant.
    share_collection = False
