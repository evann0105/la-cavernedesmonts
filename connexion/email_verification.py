import hashlib
import secrets
import time
from datetime import timedelta
from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.translation import gettext as _
from .models import EmailVerification, SignupQuota

SALT = 'boutique-email-verification-v1'


def email_verified(user):
    if not user.is_authenticated or not user.is_active or not user.email:
        return False
    return EmailVerification.objects.filter(user=user, verified_email=user.email.strip().lower(), verified_at__isnull=False).exists()


def email_ready():
    return bool(settings.EMAIL_DELIVERY_ENABLED and settings.DEFAULT_FROM_EMAIL and (settings.DEBUG or settings.SITE_URL.startswith('https://')))


def send_verification(user):
    if not email_ready() or not user.email:
        return 'unavailable'
    if email_verified(user):
        return 'verified'
    window = int(time.time()) // 3600
    key = salted_hmac('verification-mail-quota', f'{user.pk}:{window}').hexdigest()
    quota, quota_created = SignupQuota.objects.get_or_create(key=key, defaults={'expires_at':timezone.now()+timedelta(hours=2)})
    if not SignupQuota.objects.filter(pk=quota.pk, attempts__lt=5).update(attempts=F('attempts')+1):
        return 'limited'
    state, state_created = EmailVerification.objects.get_or_create(user=user)
    token = signing.dumps({'user':user.pk, 'email':user.email.strip().lower(), 'password':hashlib.sha256(user.password.encode()).hexdigest(), 'nonce':secrets.token_urlsafe(32)}, salt=SALT)
    digest = hashlib.sha256(token.encode()).hexdigest()
    claimed = EmailVerification.objects.filter(pk=state.pk).filter(Q(sent_at__isnull=True)|Q(sent_at__lt=timezone.now()-timedelta(seconds=60))).update(token_digest=digest, sent_at=timezone.now())
    if not claimed:
        return 'limited'
    url = settings.SITE_URL + reverse('connexion:confirm_email', args=[token])
    try:
        sent=send_mail(_('Confirmez votre adresse e-mail — La Caverne des Monts'), _('Pour confirmer votre adresse e-mail, ouvrez ce lien puis confirmez sur la page. Il expire dans une heure et ne peut être utilisé qu’une fois.')+'\n\n'+url+'\n\n'+_('Si vous n’êtes pas à l’origine de cette demande, ignorez ce message.'),settings.DEFAULT_FROM_EMAIL,[user.email],fail_silently=False)
        if sent != 1:
            raise RuntimeError('Email not accepted')
    except Exception:
        # Never expose mail credentials, addresses or verification tokens in errors/logs.
        EmailVerification.objects.filter(pk=state.pk,token_digest=digest).update(token_digest='')
        return 'unavailable'
    return 'sent'


def token_matches(user, token):
    try:
        payload=signing.loads(token,salt=SALT,max_age=3600)
    except (signing.BadSignature, ValueError):
        return False
    if payload.get('user') != user.pk or payload.get('email') != user.email.strip().lower() or not constant_time_compare(payload.get('password',''),hashlib.sha256(user.password.encode()).hexdigest()):
        return False
    digest=hashlib.sha256(token.encode()).hexdigest()
    return EmailVerification.objects.filter(user=user,token_digest=digest).exists()


def confirm_token(user,token):
    if not token_matches(user,token):
        return False
    return bool(EmailVerification.objects.filter(user=user,token_digest=hashlib.sha256(token.encode()).hexdigest()).update(verified_email=user.email.strip().lower(),verified_at=timezone.now(),token_digest=''))
