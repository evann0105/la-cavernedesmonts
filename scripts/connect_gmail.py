"""Interactive local Gmail authorization. Never paste OAuth secrets into chat."""
import base64
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from email.message import EmailMessage
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import AuthorizedSession


def private_write(path, text):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.gmail-')
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    root = Path(__file__).resolve().parents[1]
    filename = input('Chemin du fichier JSON téléchargé depuis Google Cloud : ').strip().strip('\"\'')
    email = input('Adresse Gmail autorisée (et destinataire du test) : ').strip()
    if not re.fullmatch(r'[A-Za-z0-9._%+-]+@gmail\.com', email):
        raise SystemExit('Adresse Gmail invalide.')
    config = json.loads(Path(filename).expanduser().read_text())
    if 'installed' not in config:
        raise SystemExit('Créez un client OAuth de type Application de bureau.')
    # Never send credentials to endpoints supplied by an arbitrary JSON file.
    config['installed']['auth_uri'] = 'https://accounts.google.com/o/oauth2/auth'
    config['installed']['token_uri'] = 'https://oauth2.googleapis.com/token'
    flow = InstalledAppFlow.from_client_config(config, ['https://www.googleapis.com/auth/gmail.send'], autogenerate_code_verifier=True)
    print('Connectez-vous avec cette adresse dans le navigateur. Seul l’envoi de messages est demandé, pas la lecture de votre boîte.')
    credentials = flow.run_local_server(host='127.0.0.1', port=0, timeout_seconds=300, prompt='consent', login_hint=email, authorization_prompt_message='La fenêtre Google va s’ouvrir.', success_message='Connexion terminée. Vous pouvez fermer cet onglet.')
    if not credentials.refresh_token:
        raise SystemExit('Autorisation hors connexion absente. Relancez la commande.')
    message = EmailMessage()
    message['From'] = email
    message['To'] = email
    message['Subject'] = 'Test e-mail — La Caverne des Monts'
    message.set_content('Gmail est connecté au site. Relancez ./start puis demandez votre lien de confirmation depuis Mon compte.')
    with AuthorizedSession(credentials) as session:
        response = session.post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send', json={'raw':base64.urlsafe_b64encode(message.as_bytes()).decode('ascii')}, timeout=15)
        response.raise_for_status()
        if not response.json().get('id'):
            raise RuntimeError('Test refusé')
    directory = root.parent / (root.name + '-data')
    directory.mkdir(mode=0o700, exist_ok=True)
    token = directory / 'gmail-oauth.json'
    private_write(token, credentials.to_json())
    values = {'EMAIL_BACKEND':'connexion.gmail_backend.EmailBackend', 'EMAIL_DELIVERY_ENABLED':'True', 'GMAIL_TOKEN_FILE':str(token), 'DEFAULT_FROM_EMAIL':email}
    target = root / '.env'
    content = target.read_text() if target.exists() else ''
    lines = [line for line in content.splitlines() if not any(re.match(r'^\s*(?:export\s+)?'+key+r'\s*=', line) for key in values)]
    lines.extend(f'{key}={value}' for key,value in values.items())
    private_write(target, '\n'.join(lines)+'\n')
    print('Gmail a accepté le message de test. Vérifiez votre réception, puis relancez ./start. En mode test Google, une nouvelle connexion peut être nécessaire après sept jours.')


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, Exception):
        print('Connexion inachevée. Vérifiez le fichier, l’activation de Gmail API et votre présence dans les utilisateurs de test. Aucun détail secret n’est affiché.', file=sys.stderr)
        sys.exit(1)
