"""Configure Gmail locally without printing or committing credentials."""
import getpass
import os
from pathlib import Path
import re
import smtplib
import ssl
import tempfile
from email.message import EmailMessage


def main():
    root = Path(__file__).resolve().parents[1]
    print('Configuration Gmail — le secret reste dans .env, exclu de Git.')
    email = input('Votre adresse Gmail : ').strip()
    if not re.fullmatch(r'[A-Za-z0-9._%+-]+@gmail\.com', email):
        raise SystemExit('Indiquez une adresse Gmail valide.')
    password = getpass.getpass('Mot de passe d’application Google (saisie masquée) : ').replace(' ', '')
    if not re.fullmatch(r'[a-zA-Z]{16}', password):
        raise SystemExit('Utilisez le mot de passe d’application Google de 16 lettres, pas votre mot de passe habituel.')
    try:
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(email, password)
            message = EmailMessage()
            message['From'] = email
            message['To'] = email
            message['Subject'] = 'Test e-mail — La Caverne des Monts'
            message.set_content('La connexion Gmail du site fonctionne. Vous pouvez maintenant demander votre lien de confirmation après avoir relancé ./start.')
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError:
        raise SystemExit('Google refuse la connexion. Vérifiez le mot de passe d’application et la validation en deux étapes de votre compte Google.')
    except (OSError, smtplib.SMTPException):
        raise SystemExit('Connexion ou envoi refusé. Vérifiez Internet et les paramètres Google. Aucun réglage n’a été modifié.')
    values = {'EMAIL_BACKEND':'django.core.mail.backends.smtp.EmailBackend', 'EMAIL_DELIVERY_ENABLED':'True', 'EMAIL_HOST':'smtp.gmail.com', 'EMAIL_PORT':'587', 'EMAIL_HOST_USER':email, 'EMAIL_HOST_PASSWORD':password, 'EMAIL_USE_TLS':'True', 'EMAIL_USE_SSL':'False', 'DEFAULT_FROM_EMAIL':email}
    target = root / '.env'
    content = target.read_text() if target.exists() else ''
    lines = [line for line in content.splitlines() if not any(re.match(r'^\s*(?:export\s+)?'+key+r'\s*=', line) for key in values)]
    lines.extend(f'{key}={value}' for key,value in values.items())
    fd, temporary = tempfile.mkstemp(dir=root, prefix='.env-gmail-')
    try:
        with os.fdopen(fd, 'w') as output:
            output.write('\n'.join(lines)+'\n')
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print('Gmail a accepté le message de test. Vérifiez votre boîte de réception et les indésirables. Relancez ./start puis demandez le lien de confirmation.')


if __name__ == '__main__':
    main()
