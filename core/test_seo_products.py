from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from core.seo_products import ProductSEO, build_product_seo, clean_text


class AutomaticProductSEOTests(SimpleTestCase):
    def product(self, **changes):
        values = dict(
            name='Polaire femme', brand='Peak Mountain', model_reference='AF200',
            category=SimpleNamespace(name='Femmes', slug='femmes'),
            article_type='polaires', description='Col montant et fermeture zipp\u00e9e.',
            composition='100 % polyester', main_color='Rouge', sizes='M, L, XL',
            main_image='products/polaire.webp', static_image='',
        )
        values.update(changes)
        return SimpleNamespace(**values)

    def variant(self, size, color, **changes):
        return SimpleNamespace(size=size, color=color, **changes)

    def test_automatic_copy_uses_exact_product_facts(self):
        seo = build_product_seo(self.product())
        self.assertIsInstance(seo, ProductSEO)
        self.assertEqual(seo.warnings, [])
        self.assertIn('Polaire femme Peak Mountain', seo.title)
        self.assertIn('AF200', seo.description)
        self.assertIn('polaire femme Peak Mountain', seo.keywords)
        self.assertIn('Peak Mountain AF200', seo.keywords)
        self.assertIn('polaire femme Peak Mountain Rouge', seo.keywords)
        self.assertIn('polaire femme Peak Mountain taille XL', seo.keywords)
        self.assertIn('100 % polyester', seo.intro)
        self.assertNotIn('en stock', seo.description.casefold())

    def test_brand_already_in_name_is_not_repeated(self):
        seo = build_product_seo(self.product(name='Veste polaire femme PEAK   MOUNTAIN', brand='Peak Mountain'))
        self.assertEqual(seo.title.casefold().count('mountain'), 1)
        self.assertEqual(seo.intro.casefold().count('mountain'), 1)

    def test_brand_token_is_not_confused_with_a_part_of_another_word(self):
        seo = build_product_seo(self.product(name='Pochette', brand='POC', model_reference='', article_type='accessoires'))
        self.assertIn('Pochette POC', seo.title)

    def test_name_and_body_are_plain_readable_html_entity_safe_text(self):
        product = self.product(
            name='<b>Polaire</b> &amp; gilet \u00c9t\u00e9',
            description='<script>bad()</script><p>Fermeture &amp; col montant.</p>',
            model_reference='&lt;/script&gt;<script>bad()</script> A200',
        )
        seo = build_product_seo(product)
        self.assertIn('Polaire & gilet \u00c9t\u00e9', seo.title)
        for value in (seo.title, seo.description, seo.intro, *seo.keywords):
            self.assertNotIn('<', value)
            self.assertNotIn('bad()', value)
            self.assertNotIn('&amp;', value)

    def test_control_characters_and_invisible_unicode_do_not_break_text(self):
        self.assertEqual(clean_text('Polaire\x00\nrouge\u200b\u2028M'), 'Polaire rouge M')

    def test_long_unicode_title_and_description_are_bounded(self):
        seo = build_product_seo(self.product(
            name='Veste polaire femme tr\u00e8s longue ' * 15 + 'Peak Mountain',
            description='Mati\u00e8re d\u00e9crite avec pr\u00e9cision. ' * 100,
        ))
        self.assertLessEqual(len(seo.title), 95)
        self.assertLessEqual(len(seo.description), 165)
        self.assertTrue(seo.title.endswith(' | La Caverne des Monts'))
        self.assertIn('Peak Mountain', seo.title)
        self.assertNotIn('\ufffd', seo.title)

    def test_long_real_names_keep_distinguishing_terminal_colours(self):
        titles, descriptions = [], []
        for color in ('noir', 'blanc', 'bleu marine'):
            seo = build_product_seo(self.product(
                name=f'Veste sans manche matelass\u00e9e femme bi-mati\u00e8re , avec capuche coloris {color}',
                brand='Peak Mountain', model_reference='ANTILOPE',
            ))
            titles.append(seo.title)
            descriptions.append(seo.description)
            self.assertIn(color, seo.title)
            self.assertIn('Peak Mountain', seo.title)
            self.assertTrue(seo.title.startswith('Veste sans manche'))
            self.assertLessEqual(len(seo.title), 95)
        self.assertEqual(len(set(titles)), 3)
        self.assertEqual(len(set(descriptions)), 3)

    def test_long_alias_brand_names_keep_colour_and_exact_stored_brand(self):
        titles = []
        for color in ('noire', 'gris clair'):
            seo = build_product_seo(self.product(
                name=f'Veste polaire bi-mati\u00e8re chin\u00e9e /polarshell Homme {color} GEONORWAY',
                brand='Geographical Norway', model_reference='',
            ))
            titles.append(seo.title)
            self.assertIn(color, seo.title)
            self.assertIn('Geographical Norway', seo.title)
            self.assertTrue(seo.title.startswith('Veste polaire'))
            self.assertLessEqual(len(seo.title), 95)
        self.assertNotEqual(*titles)

    def test_identical_true_names_stay_identical_without_invented_identifiers(self):
        first = self.product(pk=56, name='Veste polaire sans manche blanche LHOTSE', brand='', model_reference='')
        second = self.product(pk=60, name=first.name, brand='', model_reference='')
        self.assertEqual(build_product_seo(first).title, build_product_seo(second).title)

    def test_middle_shortening_also_works_when_brand_is_missing(self):
        first = build_product_seo(self.product(
            name='Polaire avec une tr\u00e8s longue description du col des manches et de la coupe couleur Noir', brand='',
        ))
        second = build_product_seo(self.product(
            name='Polaire avec une tr\u00e8s longue description du col des manches et de la coupe couleur Rouge', brand='',
        ))
        self.assertIn('Noir', first.title)
        self.assertIn('Rouge', second.title)
        self.assertNotEqual(first.title, second.title)

    def test_empty_product_still_has_metadata_and_specific_warnings(self):
        seo = build_product_seo(SimpleNamespace())
        self.assertTrue(seo.title)
        self.assertTrue(seo.description)
        self.assertEqual(len(seo.warnings), 4)
        self.assertIn('Article de la boutique', seo.title)
        self.assertNotIn('pr\u00eat', ' '.join(seo.warnings))

    def test_punctuation_only_fields_are_treated_as_missing(self):
        seo = build_product_seo(self.product(name=' --- ', brand='!', description='...', model_reference=';'))
        self.assertIn('Article de la boutique', seo.title)
        self.assertEqual(len(seo.warnings), 3)
        self.assertNotIn('!', seo.keywords)

    def test_name_brand_is_never_inferred_into_the_brand_field(self):
        product = self.product(name='Polaire Peak Mountain', brand='')
        seo = build_product_seo(product)
        self.assertEqual(product.brand, '')
        self.assertTrue(any('Marque non renseign\u00e9e' in warning for warning in seo.warnings))
        self.assertIn('Polaire Peak Mountain', seo.title)

    def test_explicit_variants_are_the_source_for_sizes_and_colors(self):
        seo = build_product_seo(self.product(sizes='XS', main_color='Bleu'), variants=[
            self.variant('M', 'Rouge', quantity=0), self.variant('L', 'Vert', quantity=2),
        ])
        text = ' '.join(seo.keywords)
        self.assertIn('Rouge', text)
        self.assertIn('Vert', text)
        self.assertIn('taille M', text)
        self.assertIn('taille L', text)
        self.assertNotIn('XS', text)
        self.assertNotIn('Bleu', text)
        # Being described is not a promise that a zero-stock variant is buyable.
        self.assertNotIn('disponible', seo.description.casefold())

    def test_prefetched_variants_do_not_access_the_manager(self):
        manager = Mock()
        manager.all.side_effect = AssertionError('No hidden variant queries')
        product = self.product(stock_variants=manager, _prefetched_objects_cache={
            'stock_variants': [self.variant('S', 'Noir')],
        })
        seo = build_product_seo(product)
        self.assertIn('taille S', ' '.join(seo.keywords))
        manager.all.assert_not_called()

    def test_no_prefetch_does_not_start_a_query_per_product(self):
        manager = Mock()
        manager.all.side_effect = AssertionError('No hidden variant queries')
        seo = build_product_seo(self.product(stock_variants=manager))
        self.assertIn('taille M', ' '.join(seo.keywords))
        manager.all.assert_not_called()

    def test_variants_deduplicate_case_spacing_and_placeholder_labels(self):
        seo = build_product_seo(self.product(), variants=[
            self.variant('M', 'Rouge'), self.variant(' m ', ' rouge '),
            self.variant('Taille unique', 'Coloris unique'),
        ])
        self.assertEqual(sum(phrase.endswith('taille M') for phrase in seo.keywords), 1)
        self.assertEqual(sum(phrase.endswith(' Rouge') for phrase in seo.keywords), 1)
        self.assertNotIn('unique', ' '.join(seo.keywords))

    def test_phrase_count_remains_bounded_for_large_variant_catalogue(self):
        seo = build_product_seo(self.product(), variants=[
            self.variant(str(number), f'Coloris {number}') for number in range(100)
        ])
        self.assertLessEqual(len(seo.keywords), 12)
        self.assertLessEqual(len(seo.intro), 400)

    def test_category_provides_audience_when_name_does_not(self):
        seo = build_product_seo(self.product(name='Polaire AF200'))
        self.assertIn('polaire femme Peak Mountain', seo.keywords)

    def test_unknown_article_type_keeps_identity_instead_of_generic_audience_phrases(self):
        seo = build_product_seo(self.product(name='Mod\u00e8le A200', article_type='', brand=''))
        self.assertNotIn('femme', seo.keywords)
        self.assertIn('Mod\u00e8le A200 AF200', seo.keywords)
        self.assertTrue(all('Mod\u00e8le A200' in phrase or 'AF200' in phrase for phrase in seo.keywords))

    def test_baby_category_is_not_relabelled_as_adult_or_child(self):
        seo = build_product_seo(self.product(
            name='Body coton', category=SimpleNamespace(slug='accessoires-bebe'),
            article_type='accessoires', brand='Marque Exemple',
        ))
        self.assertIn('body b\u00e9b\u00e9 Marque Exemple', seo.keywords)
        self.assertNotIn('body femme Marque Exemple', seo.keywords)

    def test_conflicting_category_warns_without_inventing_gender(self):
        seo = build_product_seo(self.product(name='Polaire homme'))
        self.assertIn('polaire Peak Mountain', seo.keywords)
        self.assertNotIn('polaire femme Peak Mountain', seo.keywords)
        self.assertTrue(any('publics diff\u00e9rents' in warning for warning in seo.warnings))

    def test_new_and_existing_products_get_fresh_copy_after_every_edit(self):
        product = self.product()
        first = build_product_seo(product)
        product.name = 'Polaire femme A300'
        product.brand = 'Autre Marque'
        product.model_reference = 'A300'
        product.main_color = 'Noir'
        second = build_product_seo(product)
        self.assertNotEqual(first.title, second.title)
        self.assertNotEqual(first.description, second.description)
        self.assertNotIn('Peak Mountain', ' '.join(second.keywords))
        self.assertIn('Noir', ' '.join(second.keywords))

    def test_generation_does_not_modify_the_merchant_description(self):
        product = self.product(description='<p>Texte exact de la boutique.</p>')
        original = product.description
        build_product_seo(product)
        self.assertEqual(product.description, original)

    def test_static_product_photo_counts_as_image(self):
        seo = build_product_seo(self.product(main_image='', static_image='legacy/coat.webp'))
        self.assertFalse(any('photo' in warning.casefold() for warning in seo.warnings))

    def test_no_unsupported_quality_location_material_or_ranking_claims(self):
        seo = build_product_seo(self.product(
            name='Polaire femme', composition='', description='',
        ))
        text = ' '.join([seo.title, seo.description, seo.intro, *seo.keywords]).casefold()
        for invented in ('imperm\u00e9able', 'respirant', 'meilleur', 'fabriqu\u00e9 en france', 'coton', 'premier sur google', 'livraison offerte'):
            self.assertNotIn(invented, text)
