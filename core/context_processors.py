PUBLIC_PREVIEW_VIEWS = frozenset({
    'historique:frame',
    'editorial:page_preview_frame',
    'editorial:page_version_frame',
    'core:catalog_preview',
})


def basket(request):
    return {'cart_count': sum(i.get('quantity', 0) for i in request.session.get('cart', {}).values())}


def catalog_access(request):
    from django.conf import settings
    from .catalog_access import can_manage_catalog
    from connexion.email_verification import email_access_allowed, email_verified
    from avoirs.access import email_confirmation_required
    from retours.access import manager
    catalog = can_manage_catalog(request.user)
    returns = manager(request.user)
    seller = bool((catalog or returns) and email_access_allowed(request.user))
    match = getattr(request, 'resolver_match', None)
    view_name = match.view_name if match else ''
    return {
        'catalog_return_to': getattr(request, 'catalog_return_to', ''),
        'can_manage_returns': returns,
        'can_manage_catalog': catalog,
        'can_use_seller_space': seller,
        # Preview content keeps the public shop layout; only its outer editor
        # uses seller navigation, even when both URLs live below /gestion/.
        'is_management_page': seller and request.path.startswith('/gestion/') and view_name not in PUBLIC_PREVIEW_VIEWS,
        'email_test_mode': settings.DEBUG and settings.EMAIL_TEST_MODE,
        'email_confirmed': email_verified(request.user),
        'benefit_email_required': email_confirmation_required(),
    }


def back_navigation(request):
    from urllib.parse import urlsplit
    from django.urls import reverse
    from django.utils.http import url_has_allowed_host_and_scheme

    match = request.resolver_match
    name = match.view_name if match else ''
    if name in {'accueil:home', 'tableau:home', 'editorial:page_preview_frame', 'editorial:page_version_frame', 'historique:frame'}:
        return {'back_url': None, 'back_fallback': None, 'back_use_history': False}
    if name in {'core:catalog_add', 'core:catalog_edit', 'core:catalog_duplicate',
                'core:catalog_preview', 'core:catalog_bulk'}:
        from .catalog_navigation import catalog_return
        return_to = catalog_return(request)
        if return_to:
            return {'back_url': return_to, 'back_fallback': return_to, 'back_use_history': False}
    fallback = reverse('accueil:home')
    seller_home = reverse('tableau:home')
    if name in {'connexion:password_reset', 'connexion:password_reset_done',
                'connexion:password_reset_confirm', 'connexion:password_reset_complete'}:
        fallback = reverse('connexion:login')
        return {'back_url': fallback, 'back_fallback': fallback, 'back_use_history': False}
    if name.startswith('phototheque:'):
        if name == 'phototheque:overview':
            fallback = reverse('tableau:tools')
        elif name == 'phototheque:folder_edit':
            fallback = reverse('phototheque:folders')
        elif name == 'phototheque:photo_action':
            fallback = reverse('phototheque:detail', kwargs={'pk': match.kwargs['pk']})
        else:
            fallback = reverse('phototheque:overview')
        return {'back_url': fallback, 'back_fallback': fallback, 'back_use_history': False}
    if name.startswith('suspensions:'):
        if name == 'suspensions:overview':
            fallback = reverse('boutique:settings')
        elif name == 'suspensions:action':
            fallback = reverse('suspensions:edit', kwargs={'pk': match.kwargs['pk']})
        else:
            fallback = reverse('suspensions:overview')
        return {'back_url': fallback, 'back_fallback': fallback, 'back_use_history': False}
    if name.startswith('historique:'):
        if name in {'historique:preview', 'historique:restore'}:
            fallback = reverse('historique:detail', kwargs={'pk': match.kwargs['pk']})
        elif name in {'historique:detail', 'historique:product', 'historique:homepage'}:
            fallback = reverse('historique:index')
        else:
            fallback = reverse('tableau:tools')
        return {'back_url': fallback, 'back_fallback': fallback, 'back_use_history': False}
    if name.startswith('aide:'):
        fallback = reverse('aide:index') if name == 'aide:detail' else reverse('tableau:tools')
        return {'back_url': fallback, 'back_fallback': fallback, 'back_use_history': False}
    if name == 'annulations:detail':
        fallback = getattr(request, 'cancellation_back_url', '') or reverse('expeditions:detail', kwargs={'pk': match.kwargs['pk']})
        return {'back_url': fallback, 'back_fallback': fallback, 'back_use_history': False}
    if name == 'annulations:overview':
        return {'back_url': seller_home, 'back_fallback': seller_home, 'back_use_history': False}
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
            fallback = seller_home if name == 'editorial:overview' else reverse('editorial:overview')
    elif name in {'core:catalog_history_detail', 'core:catalog_history_undo', 'core:catalog_activity'}:
        fallback = reverse('core:catalog_history_detail', kwargs={'pk': match.kwargs['pk']}) if name == 'core:catalog_history_undo' else reverse('core:catalog_history')
        return {'back_url': fallback, 'back_fallback': fallback, 'back_use_history': False}
    elif name.startswith('core:category_'):
        fallback = reverse('core:catalog_list') if name == 'core:category_list' else reverse('core:category_list')
    elif name == 'core:catalog_promotions' or name.startswith('core:sales_'):
        fallback = seller_home if name == 'core:sales_dashboard' else reverse('core:sales_dashboard')
    elif name == 'core:seo_overview':
        fallback = seller_home
    elif name == 'core:catalog_homepage':
        fallback = seller_home
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
        fallback = seller_home if name == 'facturation:accounting' else reverse('facturation:accounting')
        return {'back_url': fallback, 'back_fallback': fallback, 'back_use_history': False}
    elif name.startswith('alertes:'):
        if name == 'alertes:stock_rule':
            fallback = reverse('alertes:stocks')
        elif name == 'alertes:email_detail':
            fallback = reverse('alertes:emails')
        else:
            fallback = seller_home
        return {'back_url': fallback, 'back_fallback': fallback, 'back_use_history': False}
    elif name.startswith('clients:'):
        if name == 'clients:directory':
            fallback = seller_home
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
            fallback = seller_home if name == 'expeditions:overview' else reverse('expeditions:overview')
    elif name.startswith('boutique:shipping_') and name != 'boutique:shipping_information':
        fallback = seller_home if name == 'boutique:shipping_overview' else reverse('boutique:shipping_overview')
    elif name.startswith('boutique:settings_'):
        fallback = reverse('boutique:settings')
    elif name.startswith('boutique:') and request.path.startswith('/gestion/'):
        fallback = seller_home
    if request.path.startswith('/gestion/') and fallback == reverse('accueil:home'):
        fallback = seller_home
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
