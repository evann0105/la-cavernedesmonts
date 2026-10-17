"""Open the development site in an isolated, local-only HTTP browser profile."""
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit
import webbrowser


def find_browser():
    if sys.platform == 'darwin':
        for folder in (Path('/Applications'), Path.home() / 'Applications'):
            for app, executable in (
                ('Google Chrome', 'Google Chrome'),
                ('Microsoft Edge', 'Microsoft Edge'),
                ('Chromium', 'Chromium'),
            ):
                binary = folder / f'{app}.app/Contents/MacOS/{executable}'
                if binary.is_file():
                    return str(binary)
    for command in ('google-chrome', 'chromium', 'chromium-browser', 'msedge'):
        binary = shutil.which(command)
        if binary:
            return binary
    if sys.platform == 'win32':
        for variable in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
            folder = os.environ.get(variable)
            if folder:
                for relative in ('Google/Chrome/Application/chrome.exe', 'Microsoft/Edge/Application/msedge.exe'):
                    binary = Path(folder) / relative
                    if binary.is_file():
                        return str(binary)
    return None


def local_origin(url):
    parsed = urlsplit(url)
    host = parsed.hostname
    if parsed.scheme != 'http' or not host or not parsed.port or parsed.username or parsed.password:
        raise ValueError('Le navigateur de démonstration accepte uniquement une adresse HTTP locale avec un port.')
    if host != 'localhost':
        address = ipaddress.ip_address(host)
        if not (address.is_loopback or address.is_private) or address.is_unspecified or address.is_multicast:
            raise ValueError('Le navigateur de démonstration est réservé au réseau local.')
    return host, parsed.port


def open_local_browser(root, url):
    host, port = local_origin(url)
    binary = find_browser()
    if not binary:
        print('Chrome, Chromium ou Edge introuvable : ouverture dans le navigateur habituel. '
              'Son cache ou son mode HTTPS peut nécessiter un réglage manuel.', flush=True)
        return webbrowser.open(url)

    # Never read or modify the user's normal browser profile. One separate profile
    # per origin also avoids changing Preferences while another demo is running.
    profile = Path(root) / '.local-browser' / f'http-{host.replace(":", "_")}-{port}'
    default = profile / 'Default'
    for folder in (profile.parent, profile, default):
        folder.mkdir(mode=0o700, exist_ok=True)
    preferences = default / 'Preferences'
    if not preferences.exists():
        # Chromium's host allowlist exempts only this local host from HTTPS
        # upgrades. HTTPS, certificate validation and Safe Browsing stay enabled
        # for other sites. No --ignore-certificate-errors or --disable-web-security.
        # See chromium/src/chrome/common/pref_names.h: kHttpAllowlist.
        settings = {
            'https_upgrades': {'policy': {'http_allowlist': [host]}},
            'profile': {'name': 'La Caverne — démonstration locale'},
            'browser': {'check_default_browser': False},
        }
        descriptor = os.open(preferences, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(settings, stream)
    arguments = [f'--user-data-dir={profile}', '--no-first-run',
                 '--no-default-browser-check', '--new-window', url]
    if sys.platform == 'darwin':
        # LaunchServices creates an actual independent macOS application
        # instance instead of forwarding the URL to the personal profile.
        application = str(Path(binary).parents[2])
        result = subprocess.run(['open', '-n', '-a', application, '--args', *arguments],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if result.returncode:
            raise OSError(result.stderr.strip() or 'macOS a refusé l’ouverture du navigateur local.')
    else:
        process = subprocess.Popen(
            [binary, *arguments], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        else:
            if code:
                raise OSError(f'Le navigateur local s’est arrêté (code {code}).')
    print('Ouverture dans la fenêtre de démonstration locale (profil séparé).', flush=True)
    return True
