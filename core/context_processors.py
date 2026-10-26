def basket(request):
    return {'cart_count': sum(i.get('quantity', 0) for i in request.session.get('cart', {}).values())}


def catalog_access(request):
    from django.conf import settings
    from .catalog_access import can_manage_catalog
    from connexion.email_verification import email_verified
    from avoirs.access import email_confirmation_required
    from retours.access import manager
    return {'catalog_return_to': getattr(request, 'catalog_return_to', ''), 'can_manage_returns':manager(request.user), 'email_test_mode': settings.DEBUG and settings.EMAIL_TEST_MODE, 'can_manage_catalog': can_manage_catalog(request.user), 'email_confirmed': email_verified(request.user), 'benefit_email_required': email_confirmation_required()}


def back_navigation(request):
    from urllib.parse import urlsplit
    from django.urls import reverse
    from django.utils.http import url_has_allowed_host_and_scheme

    match = request.resolver_match
    name = match.view_name if match else ''
    if name in {'accueil:home', 'editorial:page_preview_frame', 'editorial:page_version_frame'}:
        return {'back_url': None, 'back_fallback': None, 'back_use_history': False}
    if name in {'core:catalog_add', 'core:catalog_edit', 'core:catalog_duplicate',
                'core:catalog_preview', 'core:catalog_bulk'}:
        from .catalog_navigation import catalog_return
        return_to = catalog_return(request)
        if return_to:
            return {'back_url': return_to, 'back_fallback': return_to, 'back_use_history': False}
    fallback = reverse('accueil:home')
    if name.startswith('editorial:'):
        if name in {'editorial:page_preview', 'editorial:page_history', 'editorial:page_publish'}:
            fallback = reverse('editorial:page_edit', kwargs={'key': match.kwargs['key']})
        elif name in {'editorial:page_version_preview', 'editorial:page_restore'}:
            fallback = reverse('editorial:page_history', kwargs={'key': match.kwargs['key']})
        elif name in {'editorial:faq_edit', 'editorial:faq_create', 'editorial:faq_reorder',
                      'editorial:faq_toggle', 'editorial:faq_move'}:
            fallback = reverse('editorial:faq_list')
        elif name in {'editorial:announcement_edit', 'editorial:announcement_create',
                      'editorial:announcement_archive', 'editorial:announcement_restore',
                      'editorial:announcement_duplicate', 'editorial:announcement_pause'}:
            fallback = reverse('editorial:announcements')
        elif name != 'editorial:faq':
            fallback = reverse('espace:account') if name == 'editorial:overview' else reverse('editorial:overview')
    elif name in {'core:catalog_history_detail', 'core:catalog_history_undo', 'core:catalog_activity'}:
        fallback = reverse('core:catalog_history_detail', kwargs={'pk': match.kwargs['pk']}) if name == 'core:catalog_history_undo' else reverse('core:catalog_history')
        return {'back_url': fallback, 'back_fallback': fallback, 'back_use_history': False}
    elif name.startswith('core:category_'):
        fallback = reverse('core:catalog_list') if name == 'core:category_list' else reverse('core:category_list')
    elif name == 'core:catalog_promotions' or name.startswith('core:sales_'):
        fallback = reverse('espace:account') if name == 'core:sales_dashboard' else reverse('core:sales_dashboard')
    elif name == 'core:seo_overview':
        fallback = reverse('espace:account')
    elif name == 'core:catalog_homepage':
        fallback = reverse('espace:account')
    elif name == 'core:homepage_collections':
        fallback = reverse('core:catalog_homepage')
    elif name == 'homepage:slides':
        fallback = reverse('homepage:section', kwargs={'section': 'hero'})
    elif name.startswith('homepage:'):
        fallback = reverse('homepage:sections') if name == 'homepage:section' else reverse('core:catalog_homepage')
    elif name.startswith('core:catalog_') and name != 'core:catalog_list':
        fallback = reverse('core:catalog_list')
    elif name == 'core:product_detail':
        fallback = reverse('core:product_list')
    elif name.startswith('paiement:'):
        fallback = reverse('core:cart')
    elif name in {'facturation:accounting_history', 'facturation:accounting_export_detail', 'facturation:accounting_export_transmission'}:
        fallback = reverse('facturation:accounting') if name == 'facturation:accounting_history' else reverse('facturation:accounting_history')
        return {'back_url': fallback, 'back_fallback': fallback, 'back_use_history': False}
    elif name in {'facturation:accounting', 'facturation:sales_export', 'facturation:invoice_archive', 'facturation:accounting_bundle'}:
        fallback = reverse('espace:account') if name == 'facturation:accounting' else reverse('facturation:accounting')
        return {'back_url': fallback, 'back_fallback': fallback, 'back_use_history': False}
    elif name.startswith('alertes:'):
        if name == 'alertes:stock_rule':
            fallback = reverse('alertes:stocks')
        elif name == 'alertes:email_detail':
            fallback = reverse('alertes:emails')
        else:
            fallback = reverse('espace:account')
        return {'back_url': fallback, 'back_fallback': fallback, 'back_use_history': False}
    elif name.startswith('clients:'):
        if name == 'clients:directory':
            fallback = reverse('espace:account')
        elif name in {'clients:case_edit', 'clients:attachment_add', 'clients:attachment_download', 'clients:attachment_remove', 'clients:relationships_edit'}:
            fallback = reverse('clients:case_detail', kwargs={'pk': request.resolver_match.kwargs['pk']})
        elif name in {'clients:profile', 'clients:case_list'}:
            fallback = reverse('clients:directory')
        else:
            fallback = reverse('clients:case_list')
        return {'back_url': fallback, 'back_fallback': fallback, 'back_use_history': False}
    elif name.startswith('expeditions:'):
        if name == 'expeditions:contacts' and match and match.kwargs.get('pk'):
            fallback = reverse('expeditions:detail', kwargs={'pk': match.kwargs['pk']})
        else:
            fallback = reverse('espace:account') if name == 'expeditions:overview' else reverse('expeditions:overview')
    elif name.startswith('boutique:shipping_') and name != 'boutique:shipping_information':
        fallback = reverse('espace:account') if name == 'boutique:shipping_overview' else reverse('boutique:shipping_overview')
    elif name.startswith('boutique:settings_'):
        fallback = reverse('boutique:settings')
    elif name.startswith('boutique:'):
        fallback = reverse('espace:account')
    # The href remains a stable parent without JavaScript. A safe internal
    # referrer only enables browser Back; following a referrer link would push
    # another entry and make repeated clicks bounce between the same pages.
    referer = request.META.get('HTTP_REFERER', '')
    if url_has_allowed_host_and_scheme(referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        try:
            previous = urlsplit(referer)
            current = urlsplit(request.build_absolute_uri())
            target = previous.path + (('?' + previous.query) if previous.query else '')
            if (not previous.netloc or (previous.scheme, previous.netloc) == (current.scheme, current.netloc)) and target.startswith('/') and not target.startswith('//') and target != request.get_full_path():
                if not previous.path.startswith(('/accounts/', '/account/', '/admin/')):
                    return {'back_url': target + (('#' + previous.fragment) if previous.fragment else ''),
                            'back_fallback': fallback, 'back_use_history': True}
        except ValueError:
            pass
    return {'back_url': fallback, 'back_fallback': fallback, 'back_use_history': False}
