from .legal import DEFAULTS, REQUIRED

APPROVED_LEGAL_SETTINGS = {
    'SHOP_LEGAL':{**DEFAULTS, **{key:'Renseignement de test uniquement' for key in REQUIRED}, 'mediator_url':'https://example.test/mediation', 'vat_notice':'TVA non applicable, article 293 B du CGI (test uniquement).'},
    'SHOP_INVOICING':{'ready':True,'numbering_confirmed':True,'issue_policy_confirmed':True,'vat_mode':'franchise'},
    'LEGAL_DOCUMENTS_APPROVED':True, 'LEGAL_PRIVACY_OPERATIONS_READY':True,
    'LEGAL_PRODUCT_INFORMATION_READY':True, 'LEGAL_INVOICING_READY':True,
    'LEGAL_ACCESSIBILITY_REVIEWED':True,
    'EMAIL_DELIVERY_ENABLED':True, 'EMAIL_TEST_MODE':False,
}
