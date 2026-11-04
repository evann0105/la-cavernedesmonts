"""A scheduled pause changes purchasing UI, never catalogue facts or inventory."""
from datetime import timedelta
from html.parser import HTMLParser
from unittest.mock import patch

from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Category, Product
from paiement.models import Order
from paiement.tests import CONFIG
from stocks.models import Reservation, Variant
from .models import PauseConfiguration, PauseEvent, PurchaseAdmission, PurchasePause
from .public import public_information, public_payload


class Elements(HTMLParser):
    def __init__(self, response):
        super().__init__()
        self.nodes = []
        self.feed(response.content.decode())

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))

    def with_attr(self, name):
        return [attrs for _, attrs in self.nodes if name in attrs]


@override_settings(**CONFIG)
class PublicPauseTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Polaires', slug='polaires')
        self.product = Product.objects.create(name='Polaire vacances', category=self.category,
            price='60.00', availability='available', main_image='products/polaire.webp',
            description='Une polaire de la collection.', main_color='Vert')
        self.variant = Variant.objects.create(product=self.product, size='M', color='Vert', quantity=8)
        session = self.client.session
        session['cart'] = {f'variant:{self.variant.pk}': {'product': self.product.pk,
            'variant': self.variant.pk, 'size': 'M', 'color': 'Vert', 'quantity': 1}}
        session.save()
        self.now = timezone.now()
        self.pause = PurchasePause.objects.create(name='Motif interne confidentiel',
            message='Nous préparons notre retour. Merci de votre patience.',
            starts_at=self.now - timedelta(hours=1), ends_at=self.now + timedelta(days=3))
        self.status_url = reverse('suspensions_public:status')
        for path in ('stripe.checkout.Session.create', 'stripe.checkout.Session.retrieve', 'stripe.checkout.Session.expire'):
            guard = patch(path, side_effect=AssertionError('Public pause reads must not contact payment providers'))
            guard.start(); self.addCleanup(guard.stop)

    def test_status_is_public_get_only_no_cache_and_contains_no_private_metadata(self):
        response = self.client.get(self.status_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {'paused', 'message', 'starts_at', 'resumes_at',
            'next_change_at', 'checked_at', 'revision', 'resume_label'})
        self.assertTrue(response.json()['paused'])
        self.assertNotContains(response, 'Motif interne confidentiel')
        self.assertIn('heure de Paris', response.json()['resume_label'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('noindex', response['X-Robots-Tag'])
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(self.client.post(self.status_url).status_code, 405)

    def test_status_and_catalogue_gets_do_not_write_or_change_stock(self):
        models = (PurchasePause, PauseConfiguration, PauseEvent, PurchaseAdmission, Order, Reservation)
        before = [model.objects.count() for model in models]
        original = (self.variant.quantity, self.variant.reserved, self.variant.revision)
        for url in (self.status_url, reverse('accueil:home'), reverse('core:product_list'),
                    reverse('core:product_detail', args=[self.product.slug])):
            self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual([model.objects.count() for model in models], before)
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity, self.variant.reserved, self.variant.revision), original)

    def test_home_banner_and_product_purchase_are_paused_but_sizes_and_photos_remain(self):
        home = self.client.get(reverse('accueil:home'))
        self.assertNotIn('hidden', Elements(home).with_attr('data-orders-banner')[0])
        product = self.client.get(reverse('core:product_detail', args=[self.product.slug]))
        self.assertContains(product, 'Nous préparons notre retour.')
        self.assertContains(product, 'data-product-color="vert"')
        self.assertContains(product, 'products/polaire.webp')
        self.assertIn('disabled', Elements(product).with_attr('data-orders-fieldset')[0])
        self.assertNotIn('hidden', Elements(product).with_attr('data-orders-paused')[0])
        self.assertNotContains(product, 'Motif interne confidentiel')

    def test_cart_and_checkout_keep_inputs_and_totals_but_disable_purchase(self):
        for url in (reverse('core:cart'), reverse('paiement:checkout')):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'name="benefit_code"')
            self.assertContains(response, '60,00')
            self.assertNotIn('hidden', Elements(response).with_attr('data-orders-paused')[0])
            if url == reverse('core:cart'):
                self.assertIn('hidden', Elements(response).with_attr('data-orders-open')[0])
            else:
                self.assertTrue(response.context['enabled'])
                self.assertIn('disabled', Elements(response).with_attr('data-orders-fieldset')[0])
        self.assertEqual(self.client.session['cart'][f'variant:{self.variant.pk}']['quantity'], 1)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Reservation.objects.exists())

    def test_expired_pause_reopens_controls_without_mutating_the_schedule(self):
        with patch('suspensions.services.timezone.now', return_value=self.pause.ends_at):
            response = self.client.get(reverse('core:product_detail', args=[self.product.slug]))
            state = self.client.get(self.status_url).json()
        self.assertFalse(state['paused'])
        self.assertIsNone(state['resumes_at'])
        self.assertIn('hidden', Elements(response).with_attr('data-orders-banner')[0])
        self.assertNotIn('disabled', Elements(response).with_attr('data-orders-fieldset')[0])
        self.pause.refresh_from_db()
        self.assertIsNone(self.pause.stopped_at)
        self.assertEqual(self.pause.revision, 1)

    def test_future_pause_has_timer_boundary_without_announcing_a_current_pause(self):
        with patch('suspensions.services.timezone.now', return_value=self.pause.starts_at - timedelta(seconds=1)):
            response = self.client.get(reverse('core:product_detail', args=[self.product.slug]))
        self.assertIn('hidden', Elements(response).with_attr('data-orders-banner')[0])
        self.assertEqual(response.context['order_pause']['next_change_at'], self.pause.starts_at.isoformat())
        self.assertContains(response, '/etat-commandes/')
        self.assertContains(response, 'suspensions/public.js')

    def test_resume_label_uses_paris_even_when_the_active_timezone_is_different(self):
        with timezone.override('America/Los_Angeles'):
            state = public_payload(at=self.now)
        from zoneinfo import ZoneInfo
        local = self.pause.ends_at.astimezone(ZoneInfo('Europe/Paris'))
        self.assertIn(f'{local:%d/%m/%Y} à {local:%H h %M}', state['resume_label'])

    def test_management_and_editorial_previews_do_not_get_live_public_controls(self):
        factory = RequestFactory()
        for path in ('/gestion/', '/gestion/historique/1/apercu/', '/admin/'):
            self.assertEqual(public_information(factory.get(path)), {})
        request = factory.get('/')
        request._history_preview = True
        self.assertEqual(public_information(request), {})

    def test_pause_leaves_product_seo_and_physical_availability_identical(self):
        url = reverse('core:product_detail', args=[self.product.slug])
        during = self.client.get(url)
        with patch('suspensions.services.timezone.now', return_value=self.pause.ends_at):
            after = self.client.get(url)
        self.assertEqual(during.context['seo']['title'], after.context['seo']['title'])
        self.assertEqual(during.context['seo']['robots'], after.context['seo']['robots'])
        self.assertEqual(during.context['seo']['json_ld'], after.context['seo']['json_ld'])
        self.assertIn('https://schema.org/InStock', str(during.context['seo']['json_ld']))
        self.assertEqual(during.get('X-Robots-Tag', ''), after.get('X-Robots-Tag', ''))

    def test_direct_add_from_a_stale_page_is_refused_without_releasing_a_checkout(self):
        before = self.client.session['cart']
        with patch('paiement.cart_checkout.release_for_cart_change') as release:
            response = self.client.post(reverse('core:cart_add', args=[self.product.slug]),
                {'variant': self.variant.pk, 'quantity': 1})
        self.assertRedirects(response, reverse('core:product_detail', args=[self.product.slug]), fetch_redirect_response=False)
        release.assert_not_called()
        self.assertEqual(self.client.session['cart'], before)
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity, self.variant.reserved), (8, 0))

    def test_quantities_and_removals_remain_available_during_pause(self):
        key = f'variant:{self.variant.pk}'
        self.client.post(reverse('core:cart_update'), {'key': key, 'quantity': 2})
        self.assertEqual(self.client.session['cart'][key]['quantity'], 2)
        self.client.post(reverse('core:cart_update'), {'key': key, 'quantity': 0})
        self.assertNotIn(key, self.client.session['cart'])

    def test_add_becomes_available_after_scheduled_end(self):
        # Keep the browser session clock unchanged: this test covers the pause,
        # not the independent one-hour session expiration.
        self.pause.ends_at = self.now
        self.pause.save(update_fields=['ends_at'])
        response = self.client.post(reverse('core:cart_add', args=[self.product.slug]),
            {'variant': self.variant.pk, 'quantity': 1})
        self.assertRedirects(response, reverse('core:cart'), fetch_redirect_response=False)
        self.assertEqual(self.client.session['cart'][f'variant:{self.variant.pk}']['quantity'], 2)

    def test_paused_add_still_reconciles_already_paid_items(self):
        session = self.client.session
        paid = Order.objects.create(status='paid', session_key=session.session_key, subtotal=6000,
            items=[{'product': self.product.pk, 'variant': self.variant.pk, 'size': 'M',
                    'color': 'Vert', 'quantity': 1, 'unit_amount': 6000}])
        session['checkout_order'] = str(paid.pk); session.save()
        self.client.post(reverse('core:cart_add', args=[self.product.slug]),
            {'variant': self.variant.pk, 'quantity': 1})
        self.assertNotIn(f'variant:{self.variant.pk}', self.client.session['cart'])
        self.assertNotIn('checkout_order', self.client.session)
        paid.refresh_from_db()
        self.assertEqual(paid.status, 'paid')

    @override_settings(DEBUG=False, SITE_URL='https://la-cavernedesmonts.fr',
                       SEO_INDEXING_ENABLED=True, SECURE_SSL_REDIRECT=False)
    def test_production_catalogue_stays_indexable_during_pause(self):
        for url in (reverse('accueil:home'), reverse('core:product_detail', args=[self.product.slug])):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['seo']['indexable'])
            self.assertNotIn('noindex', response.get('X-Robots-Tag', ''))
            self.assertTrue(response.context['order_pause']['paused'])
