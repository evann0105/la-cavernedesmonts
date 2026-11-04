"""Asynchronous, one-hour account recovery, independent of email verification.

The HTTP request queues the same encrypted envelope for any valid address. SMTP
never runs in the requesting browser, so its latency cannot reveal an account.
Only the latest accepted envelope can authorize recovery. Tokens are hashes in
state/session; the worker's retry payload is encrypted and cleared after sending.
"""
from datetime import datetime, timedelta, timezone as datetime_timezone
import hashlib
import ipaddress
import math
from urllib.parse import urlsplit
from uuid import uuid4

from django.conf import settings
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import connection, transaction
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.debug import sensitive_variables

from .email_verification import email_ready
from .models import PasswordResetMail, PasswordResetState, SignupQuota

HOUR = timedelta(hours=1)
LEASE = timedelta(minutes=5)
SESSION_KEY = 'password_reset_grant'
GRANT_SALT = 'boutique-password-reset-session-v1'


class RecoveryTokenGenerator(PasswordResetTokenGenerator):
    def _now(self):
        # A token lasts an elapsed hour, including across French DST changes.
        return datetime.now(datetime_timezone.utc).replace(tzinfo=None)


token_generator = RecoveryTokenGenerator()


class PasswordResetPasswordForm(SetPasswordForm):
    new_password1 = forms.CharField(label='Nouveau mot de passe', max_length=256,
        strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))
    new_password2 = forms.CharField(label='Confirmer le nouveau mot de passe', max_length=256,
        strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth.password_validation import password_validators_help_text_html
        self.fields['new_password1'].help_text = password_validators_help_text_html()


def _hash(kind, value):
    return salted_hmac('password-reset:' + kind, str(value), algorithm='sha256').hexdigest()


def _network(value):
    try:
        address = ipaddress.ip_address(value or '')
        if isinstance(address, ipaddress.IPv6Address):
            return str(address.ipv4_mapped) if address.ipv4_mapped else str(ipaddress.ip_network(f'{address}/64', strict=False))
        return str(address)
    except ValueError:
        return 'unknown'


def site_origin(value=None):
    value = str(settings.SITE_URL if value is None else value).strip().rstrip('/')
    try:
        URLValidator(schemes=['http', 'https'] if settings.DEBUG else ['https'])(value)
        parsed = urlsplit(value)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path or any(character.isspace() for character in value)):
            return ''
        parsed.port
    except (ValidationError, ValueError):
        return ''
    return value


def delivery_available():
    return bool(email_ready() and site_origin())


def _address_lock(recipient_hash):
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s, %s)', [604004, int(recipient_hash[:8], 16) - 2**31])


@transaction.atomic
@sensitive_variables('email')
def queue_password_reset(email, *, remote_addr):
    email = email.strip().lower()
    now = timezone.now()
    window = int(now.timestamp()) // 3600
    expires = timezone.datetime.fromtimestamp((window + 1) * 3600, tz=timezone.get_current_timezone())
    quota, _ = SignupQuota.objects.get_or_create(
        key=_hash('ip-quota', f'{_network(remote_addr)}:{window}'), defaults={'expires_at': expires})
    if not SignupQuota.objects.filter(pk=quota.pk, attempts__lt=20).update(attempts=F('attempts') + 1):
        return 'limited_ip', max(1, math.ceil((expires - now).total_seconds()))
    recipient_hash = _hash('address', email)
    _address_lock(recipient_hash)
    # SQLite owns its writer lock from the quota update. The PostgreSQL lock
    # serializes this address even when it is requested from different networks.
    recent = PasswordResetMail.objects.filter(kind='reset', recipient_hash=recipient_hash, created_at__gt=now - HOUR)
    latest = recent.order_by('-pk').values_list('created_at', flat=True).first()
    if recent.count() >= 5 or (latest and now < latest + timedelta(seconds=60)):
        return 'accepted', 0
    if not delivery_available():
        return 'unavailable', 0
    PasswordResetMail.objects.create(recipient=email, recipient_hash=recipient_hash, origin=site_origin(), created_at=now)
    return 'accepted', 0


def _identity(user):
    last_login = user.last_login.isoformat() if user.last_login else ''
    return _hash('identity', f'{user.pk}:{user.password}:{user.email.strip().lower()}:{last_login}')


def _eligible(user):
    return bool(user and user.is_active and user.email and user.has_usable_password())


def _latest(mail):
    return not PasswordResetMail.objects.filter(kind='reset', recipient_hash=mail.recipient_hash, pk__gt=mail.pk).exists()


def _current_state(user, state):
    now = timezone.now()
    return bool(_eligible(user) and state and state.mail_id and state.token_digest and state.issued_at
                and state.issued_at <= now < state.issued_at + HOUR
                and state.mail.state == 'sent' and _latest(state.mail)
                and constant_time_compare(state.identity_digest, _identity(user)))


def user_from_uid(uidb64):
    if not isinstance(uidb64, str) or len(uidb64) > 64:
        return None
    try:
        identifier = urlsafe_base64_decode(uidb64).decode()
        if not identifier.isascii() or not identifier.isdigit() or len(identifier) > 20:
            return None
        return get_user_model().objects.filter(pk=int(identifier)).first()
    except (ValueError, TypeError, UnicodeDecodeError, OverflowError):
        return None


@sensitive_variables('token')
def token_valid(user, token):
    if not _eligible(user) or not isinstance(token, str) or not 1 <= len(token) <= 128:
        return False
    state = PasswordResetState.objects.select_related('mail').defer('mail__recipient', 'mail__payload').filter(user=user).first()
    return bool(_current_state(user, state)
                and constant_time_compare(state.token_digest, hashlib.sha256(token.encode()).hexdigest())
                and token_generator.check_token(user, token))


@sensitive_variables('token')
def make_session_grant(user, token, session):
    if not token_valid(user, token):
        return False
    session.cycle_key()
    session[SESSION_KEY] = signing.dumps({
        'uid': user.pk, 'digest': hashlib.sha256(token.encode()).hexdigest(),
        'binding': _hash('session', session.session_key),
    }, salt=GRANT_SALT)
    return True


def _grant_payload(value, user, session_key):
    try:
        payload = signing.loads(value or '', salt=GRANT_SALT, max_age=3600)
        if (not isinstance(payload, dict) or payload.get('uid') != user.pk or not isinstance(payload.get('digest'), str)
                or not constant_time_compare(payload.get('binding', ''), _hash('session', session_key))):
            return None
        return payload
    except (signing.BadSignature, ValueError, TypeError):
        return None


def grant_valid(user, grant, session_key):
    if not _eligible(user):
        return False
    payload = _grant_payload(grant, user, session_key)
    state = PasswordResetState.objects.select_related('mail').defer('mail__recipient', 'mail__payload').filter(user=user).first()
    return bool(payload and _current_state(user, state)
                and constant_time_compare(payload['digest'], state.token_digest))


@transaction.atomic
@sensitive_variables('grant', 'data')
def reset_password(user_id, grant, session_key, data):
    # Match the same address lock used by a new request before acquiring user
    # and state locks. No request can supersede this link halfway through its
    # final validation; independent addresses never share a global lock.
    mail_hash = PasswordResetState.objects.filter(user_id=user_id).values_list('mail__recipient_hash', flat=True).first()
    if not mail_hash:
        return None, False
    _address_lock(mail_hash)
    user = get_user_model().objects.select_for_update().filter(pk=user_id).first()
    if not _eligible(user):
        return None, False
    state = PasswordResetState.objects.select_for_update(of=('self',)).select_related('mail').defer('mail__recipient', 'mail__payload').filter(user=user).first()
    payload = _grant_payload(grant, user, session_key)
    if not (payload and _current_state(user, state) and constant_time_compare(payload['digest'], state.token_digest)):
        return None, False
    form = PasswordResetPasswordForm(user, data)
    if not form.is_valid():
        return form, False
    form.save()
    state.token_digest = ''; state.identity_digest = ''
    state.save(update_fields=['token_digest', 'identity_digest'])
    PasswordResetMail.objects.create(kind='changed', recipient=user.email,
        recipient_hash=_hash('address', user.email.strip().lower()), user=user,
        origin=site_origin() or state.mail.origin)
    return form, True


def _claim():
    now = timezone.now()
    with transaction.atomic():
        query = PasswordResetMail.objects.filter(Q(state='pending') | Q(state='sending', claimed_at__lt=now - LEASE))
        query = query.filter(Q(kind='reset', created_at__gte=now - HOUR)
                             | Q(kind='changed', created_at__gte=now - timedelta(days=1)))
        query = query.select_for_update(skip_locked=True) if connection.features.has_select_for_update_skip_locked else query.select_for_update()
        job = query.only('id', 'state', 'claim_id', 'claimed_at', 'attempts').order_by('pk').first()
        if job is None:
            return None
        job.state = 'sending'; job.claim_id = uuid4(); job.claimed_at = now; job.attempts += 1
        job.save(update_fields=['state', 'claim_id', 'claimed_at', 'attempts'])
        return job.pk, job.claim_id


def _finish(job_id, claim_id, state):
    snapshot = PasswordResetMail.objects.filter(pk=job_id).values('user_id').first()
    if snapshot is None:
        return
    with transaction.atomic():
        if snapshot['user_id']:
            get_user_model().objects.select_for_update().filter(pk=snapshot['user_id']).first()
        reset_state = PasswordResetState.objects.select_for_update().filter(mail_id=job_id).first()
        job = PasswordResetMail.objects.select_for_update().only(
            'id', 'state', 'claim_id', 'sent_at', 'user_id').filter(pk=job_id, claim_id=claim_id, state='sending').first()
        if job is None:
            return
        if state != 'sent' and reset_state:
            reset_state.token_digest = ''; reset_state.identity_digest = ''
            reset_state.save(update_fields=['token_digest', 'identity_digest'])
        job.state = state; job.payload = {}; job.claim_id = None
        job.sent_at = timezone.now() if state == 'sent' else None
        job.save(update_fields=['state', 'payload', 'claim_id', 'sent_at'])


@sensitive_variables('token', 'payload')
def _prepare(job_id, claim_id):
    envelope = PasswordResetMail.objects.get(pk=job_id)
    if envelope.attempts > 3 or envelope.created_at < timezone.now() - (HOUR if envelope.kind == 'reset' else timedelta(days=1)):
        return None
    origin = site_origin(envelope.origin)
    if not origin:
        return None
    if envelope.kind == 'changed':
        return {'url': origin + reverse('connexion:login')}
    if not constant_time_compare(envelope.recipient_hash, _hash('address', envelope.recipient.strip().lower())):
        # Rotating the signing key invalidates queued recovery authorizations
        # too. An old HMAC bucket must not bypass a newer accepted request.
        return None
    if not _latest(envelope):
        return None
    user = get_user_model().objects.filter(email__iexact=envelope.recipient).first()
    if not _eligible(user):
        return None
    with transaction.atomic():
        _address_lock(envelope.recipient_hash)
        user = get_user_model().objects.select_for_update().get(pk=user.pk)
        if not _eligible(user) or user.email.strip().lower() != envelope.recipient.strip().lower():
            return None
        state, _ = PasswordResetState.objects.get_or_create(user=user)
        state = PasswordResetState.objects.select_for_update().get(pk=state.pk)
        job = PasswordResetMail.objects.select_for_update().get(pk=job_id)
        if job.claim_id != claim_id or job.state != 'sending' or not _latest(job):
            return None
        if job.payload:
            if (state.mail_id != job.pk or state.issued_at is None or state.issued_at + HOUR <= timezone.now()
                    or not constant_time_compare(state.identity_digest, _identity(user))):
                return None
            return job.payload
        token = token_generator.make_token(user)
        state.mail = job; state.token_digest = hashlib.sha256(token.encode()).hexdigest()
        state.identity_digest = _identity(user); state.issued_at = timezone.now()
        state.save(update_fields=['mail', 'token_digest', 'identity_digest', 'issued_at'])
        payload = {'url': origin + reverse('connexion:password_reset_confirm', kwargs={
            'uidb64': urlsafe_base64_encode(force_bytes(user.pk)), 'token': token})}
        job.user = user; job.payload = payload
        job.save(update_fields=['user', 'payload'])
        return payload


@sensitive_variables('payload', 'message')
def deliver_pending_password_resets(*, limit=10):
    """Called by the existing worker, never from a public HTTP request."""
    if not delivery_available():
        return 0
    processed = 0
    for _ in range(max(1, min(int(limit), 10))):
        claimed = _claim()
        if claimed is None:
            break
        job_id, claim_id = claimed
        try:
            payload = _prepare(job_id, claim_id)
            job = PasswordResetMail.objects.get(pk=job_id)
            if (not payload or job.claim_id != claim_id or job.state != 'sending'
                    or (job.kind == 'reset' and not _latest(job))):
                _finish(job_id, claim_id, 'skipped'); continue
            # Recheck the password/email fingerprint immediately before handing
            # a prepared message to SMTP. A later race still cannot revive an
            # obsolete token because every browser validation repeats the test.
            if job.kind == 'reset':
                user = get_user_model().objects.filter(pk=job.user_id).first()
                current = PasswordResetState.objects.filter(user_id=job.user_id, mail=job).first()
                if not (_eligible(user) and current and current.identity_digest == _identity(user)):
                    _finish(job_id, claim_id, 'skipped'); continue
            from courriels.messages import build_message
            details = ('Ce lien expire après une heure et ne peut être utilisé qu’une fois. '
                       'Seul le lien de votre dernière demande fonctionne. '
                       'Si vous n’avez rien demandé, ignorez ce message.') if job.kind == 'reset' else (
                       'Si vous n’êtes pas à l’origine de cette modification, contactez immédiatement la boutique.')
            message = build_message('password_reset' if job.kind == 'reset' else 'password_changed',
                job.recipient, payload['url'], details=details,
                test=bool(settings.DEBUG and settings.EMAIL_TEST_MODE))
            if message.send(fail_silently=False) != 1:
                raise RuntimeError('Mail not accepted')
            _finish(job_id, claim_id, 'sent'); processed += 1
        except Exception:
            # No exception text, recipient, link, token or provider credential
            # enters application logs or the public response.
            _finish(job_id, claim_id, 'failed')
    return processed


def purge_password_reset_data(*, limit=500):
    limit = max(1, min(int(limit), 500))
    now = timezone.now()
    cutoff = now - timedelta(days=1)
    stale = PasswordResetMail.objects.filter(
        Q(kind='reset', created_at__lt=now - HOUR) | Q(kind='changed', created_at__lt=cutoff)
    ).filter(Q(state='pending') | Q(state='sending', claimed_at__lt=now - LEASE))
    stale_ids = list(stale.order_by('pk').values_list('pk', flat=True)[:limit])
    if stale_ids:
        # The conditional UPDATE rechecks the lease after acquiring each row.
        # A current worker claim is never cleared just because creation is old.
        stale.filter(pk__in=stale_ids).update(state='skipped', payload={}, claim_id=None)
    query = PasswordResetMail.objects.filter(created_at__lt=cutoff, state__in=['sent', 'failed', 'skipped'])
    identifiers = list(query.order_by('pk').values_list('pk', flat=True)[:limit])
    deleted = 0
    if identifiers:
        with transaction.atomic():
            PasswordResetState.objects.filter(mail_id__in=identifiers).update(token_digest='', identity_digest='', issued_at=None)
            deleted = query.filter(pk__in=identifiers).delete()[0]
    quotas = SignupQuota.objects.filter(expires_at__lt=cutoff)
    quota_ids = list(quotas.order_by('pk').values_list('pk', flat=True)[:limit])
    if quota_ids:
        quotas.filter(pk__in=quota_ids).delete()
    return deleted
