from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse

from espace.legal import context, contract_snapshot


class ConsumerTermsFinalisationTests(TestCase):
    def terms(self):
        return render_to_string('espace/legal/terms_copy.html', context())

    @override_settings(SHOP_LEGAL={})
    def test_required_guarantee_rights_are_available_without_a_custom_notice(self):
        html = self.terms()
        for required in (
            'Encadré réglementaire relatif aux garanties légales',
            'extension de six mois',
            'dans un délai de trente jours',
            'le vendeur impose le remplacement',
            '4° La non-conformité du bien persiste',
            'Toute période d\'immobilisation du bien',
            'articles L. 217-1 à L. 217-32',
            '300 000 euros',
            'articles 1641 à 1649 du code civil',
            'sans frais dans les conditions légales',
        ):
            with self.subTest(required=required):
                self.assertIn(required, html)
        self.assertNotIn('Ce résumé ne remplace pas cet encadré', html)

    @override_settings(SHOP_LEGAL={'guarantees_notice': 'Précision commerciale sans restriction des droits'})
    def test_additional_merchant_notice_cannot_replace_the_regulatory_box(self):
        html = self.terms()
        self.assertIn('Précision commerciale sans restriction des droits', html)
        self.assertIn('articles L. 217-1 à L. 217-32', html)
        self.assertIn('articles 1641 à 1649 du code civil', html)

    def test_customer_contract_copy_contains_the_full_guarantees_and_return_information(self):
        text = contract_snapshot()['text']
        self.assertIn('Encadré réglementaire relatif aux garanties légales', text)
        self.assertIn('extension de six mois', text)
        self.assertIn('4° La non-conformité du bien persiste', text)
        self.assertIn('sa date et son heure', text)
        self.assertIn('puis pendant dix ans à compter de cette livraison', text)
        self.assertNotIn('{% comment', text)

    def test_withdrawal_page_gives_direct_access_and_complete_timing(self):
        html = render_to_string('espace/legal/withdrawal_copy.html', context())
        self.assertIn('href="'+reverse('retours:entry')+'"', html)
        for phrase in (
            'Le compte client est facultatif',
            'réception du dernier bien',
            'premier jour ouvrable suivant',
            'sa date et son heure d’envoi',
            'conservez une preuve de l’envoi',
            'La clôture administrative du dossier n’allonge pas ce délai',
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, html)

    @override_settings(SHOP_LEGAL={})
    def test_no_mediator_membership_or_contact_is_invented(self):
        html = render_to_string('espace/legal/mediator.html', context())
        self.assertIn('restent à confirmer', html)
        self.assertNotIn('<a href=', html)

    @override_settings(SHOP_LEGAL={
        'mediator_name': 'Médiateur de test',
        'mediator_address': 'Adresse de test',
        'mediator_url': 'https://example.org/mediation',
    })
    def test_mediation_states_written_complaint_and_one_year_deadline(self):
        html = render_to_string('espace/legal/mediator.html', context())
        self.assertIn('réclamation écrite préalable', html)
        self.assertIn('dans l’année suivant cette réclamation', html)
        self.assertIn('href="https://example.org/mediation"', html)
        self.assertNotIn('restent à confirmer', html)
