"""Issued-invoice copies preserve frozen figures and render long/private text safely."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID
import re

from django.test import SimpleTestCase, override_settings

from .pdf import invoice_pdf, validate_snapshot
from .services import DocumentError


def sample_invoice(line_count=2, *, taxed=True, long_name=False):
    """Synthetic, unsaved example for rendering QA. Never creates an accounting record."""
    lines = []
    for index in range(line_count):
        name = f'Polaire de démonstration n°{index + 1} - Édition été'
        if long_name and index == 0:
            name += ' ' + ('Désignation très longue avec accents & <texte littéral> ' * 50)
        lines.append({'name': name, 'size': 'M / L', 'color': 'Vert forêt', 'quantity': 1,
                      'unit': '30.00', 'gross': '30.00', 'discount': '3.00', 'net': '27.00',
                      'ht': '22.50' if taxed else '27.00', 'vat': '4.50' if taxed else '0.00',
                      'unit_ht': '25.0000' if taxed else '30.0000'})
    seller = {'business_name': 'Élodie Exemple', 'legal_form': 'Entrepreneur individuel',
              'address': '10 rue de la Démonstration, 01410 Lélex, France',
              'siren': '000 000 000', 'siret': '000 000 000 00000', 'registration': 'Immatriculation fictive',
              'email': 'boutique@example.test', 'phone': '00 00 00 00 00',
              'vat_number': 'FR00EXEMPLE' if taxed else '',
              'vat_notice': 'Mention fiscale de démonstration : TVA 20 %.' if taxed else 'TVA non applicable, art. 293 B du CGI.'}
    buyer = {'name': 'Client de démonstration', 'email': 'client@example.test',
             'address': {'line1': '1 chemin des Essais', 'line2': 'Appartement 4', 'postal_code': '69001',
                         'city': 'Lyon', 'country': 'FR'}}
    snapshot = {'number': 'WEB-2026-000001', 'is_demo': False, 'amounts_verified': True,
                'final_ready': True, 'shipping_known': True,
                'issued_at': '2026-10-05T13:00:00+00:00', 'ordered_at': '2026-10-01T10:00:00+00:00',
                'paid_at': '2026-10-01T10:01:00+00:00', 'order_reference': '00000000-0000-4000-8000-000000000001',
                'seller': seller, 'buyer': buyer, 'shipping': deepcopy(buyer),
                'vat_mode': 'standard' if taxed else 'franchise', 'vat_rate': '20.00' if taxed else '',
                'lines': lines, 'gross': str(Decimal('30')*line_count),
                'discount': str(Decimal('3')*line_count), 'shipping_amount': '6.00',
                'shipping_ht': '5.00' if taxed else '6.00', 'shipping_vat': '1.00' if taxed else '0.00',
                'total': str(Decimal('27')*line_count+6),
                'tax_base': str(Decimal('22.50' if taxed else '27.00')*line_count+(5 if taxed else 6)),
                'tax_amount': str(Decimal('4.50')*line_count+1) if taxed else '0.00',
                'document_note': 'EXEMPLE FICTIF DE VÉRIFICATION. La facture initiale reste distincte des remboursements et de leurs avoirs.'}
    return SimpleNamespace(snapshot=snapshot, number=snapshot['number'],
                           order_id=UUID(snapshot['order_reference']),
                           issued_at=datetime(2026,10,5,13,tzinfo=timezone.utc))


class InvoicePdfTests(SimpleTestCase):
    def test_issued_snapshot_renders_pdf_without_database_or_current_configuration(self):
        invoice = sample_invoice()
        original = deepcopy(invoice.snapshot)
        with override_settings(SHOP_LEGAL={'business_name': 'NOUVEAU NOM À IGNORER'}, SHOP_INVOICING={}):
            with patch('facturation.services.build_preview', side_effect=AssertionError('No live preview')):
                content = invoice_pdf(invoice)
        self.assertTrue(content.startswith(b'%PDF-'))
        self.assertTrue(content.rstrip().endswith(b'%%EOF'))
        self.assertIn(b'/FontFile2', content)  # French glyphs are embedded in the file.
        self.assertEqual(len(re.findall(rb'/Type\s*/Page\b',content)),1)
        self.assertEqual(invoice.snapshot, original)

    def test_many_lines_and_a_row_longer_than_a_page_render_with_continuation(self):
        content = invoice_pdf(sample_invoice(45, long_name=True))
        self.assertGreaterEqual(len(re.findall(rb'/Type\s*/Page\b',content)),3)
        self.assertTrue(content.rstrip().endswith(b'%%EOF'))

    def test_zero_amount_invoice_is_valid_and_not_relabelled_as_unknown(self):
        invoice=sample_invoice(taxed=False)
        for line in invoice.snapshot['lines']:
            for key in ('unit','gross','discount','net','ht','vat','unit_ht'):
                line[key]='0'
        for key in ('gross','discount','shipping_amount','shipping_ht','shipping_vat','total','tax_base','tax_amount'):
            invoice.snapshot[key]='0'
        self.assertTrue(invoice_pdf(invoice).startswith(b'%PDF-'))

    def test_literal_markup_never_creates_links_or_external_assets(self):
        invoice=sample_invoice()
        invoice.snapshot['lines'][0]['name']='<a href="https://example.test/private">Texte</a> & <img src="file:///etc/passwd"/>'
        content=invoice_pdf(invoice)
        for forbidden in (b'/JavaScript',b'/OpenAction',b'/EmbeddedFile',b'/URI',b'/RichMedia',b'/Annots'):
            self.assertNotIn(forbidden,content)

    def test_unknown_tax_mode_is_rejected_without_rebuilding_a_profile(self):
        invoice=sample_invoice();invoice.snapshot['vat_mode']=''
        with self.assertRaises(DocumentError):invoice_pdf(invoice)

    def test_issue_serialization_preserves_milliseconds_while_model_keeps_microseconds(self):
        import json
        from django.core.serializers.json import DjangoJSONEncoder
        invoice = sample_invoice()
        invoice.issued_at = invoice.issued_at.replace(microsecond=123456)
        invoice.snapshot['issued_at'] = json.loads(json.dumps(invoice.issued_at, cls=DjangoJSONEncoder))
        self.assertTrue(invoice_pdf(invoice).startswith(b'%PDF-'))
        invoice.snapshot['issued_at'] = invoice.snapshot['issued_at'].replace('.123', '.124')
        with self.assertRaises(DocumentError):validate_snapshot(invoice)

    def test_snapshot_must_belong_to_the_recorded_order(self):
        invoice = sample_invoice()
        invoice.order_id = UUID('00000000-0000-4000-8000-000000000002')
        with self.assertRaises(DocumentError):invoice_pdf(invoice)

    def test_changed_number_date_demo_or_unconfirmed_flags_are_rejected(self):
        for key,value in (('number','WEB-2026-999999'),('issued_at','2026-10-05T13:01:00+00:00'),
                          ('is_demo',True),('amounts_verified',False),('final_ready',False),
                          ('shipping_known',False),('issued_at',None),('paid_at','2026-10-01')):
            with self.subTest(key=key,value=value):
                invoice=sample_invoice();invoice.snapshot[key]=value
                with self.assertRaises(DocumentError):invoice_pdf(invoice)

    def test_bad_totals_missing_values_and_non_finite_money_are_rejected(self):
        for key,value in (('total','65.99'),('tax_base','0'),('gross','300'),('discount','100'),
                          ('shipping_vat',None),('shipping_amount','NaN'),('total',True),
                          ('tax_amount','Infinity'),('total','60.001'),('total','-1')):
            with self.subTest(key=key,value=value):
                invoice=sample_invoice();invoice.snapshot[key]=value
                with self.assertRaises(DocumentError):validate_snapshot(invoice)

    def test_inconsistent_line_math_and_invalid_quantity_are_rejected(self):
        for key,value in (('gross','300'),('discount','4'),('ht','20'),('unit','31'),('quantity',True),
                          ('quantity',0),('name',''),('unit_ht',None)):
            with self.subTest(key=key,value=value):
                invoice=sample_invoice();invoice.snapshot['lines'][0][key]=value
                with self.assertRaises(DocumentError):validate_snapshot(invoice)

    def test_incomplete_seller_or_buyer_identity_is_rejected(self):
        for party,key in (('seller','business_name'),('seller','siret'),('seller','vat_notice'),('buyer','name')):
            with self.subTest(party=party,key=key):
                invoice=sample_invoice();invoice.snapshot[party][key]=''
                with self.assertRaises(DocumentError):validate_snapshot(invoice)
        invoice=sample_invoice();invoice.snapshot['buyer']['address']['line1']=''
        with self.assertRaises(DocumentError):validate_snapshot(invoice)

    def test_franchise_requires_zero_vat_and_preserves_stored_notice(self):
        invoice=sample_invoice(taxed=False)
        self.assertEqual(validate_snapshot(invoice)['seller']['vat_notice'],'TVA non applicable, art. 293 B du CGI.')
        self.assertTrue(invoice_pdf(invoice).startswith(b'%PDF-'))
        invoice.snapshot['tax_amount']='1'
        with self.assertRaises(DocumentError):validate_snapshot(invoice)

    def test_standard_tax_requires_recorded_number_and_rate(self):
        for rate in ('', '0', '101', 'NaN'):
            invoice=sample_invoice();invoice.snapshot['vat_rate']=rate
            with self.assertRaises(DocumentError):validate_snapshot(invoice)
        invoice=sample_invoice();invoice.snapshot['seller']['vat_number']=''
        with self.assertRaises(DocumentError):validate_snapshot(invoice)
