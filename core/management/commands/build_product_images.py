"""Build display derivatives; original product photographs stay untouched."""
import hashlib
import json
from pathlib import Path
from PIL import Image, ImageOps
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Prépare les WebP du catalogue et les petites vignettes sans modifier les originaux.'

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR) / 'core/static'
        destination = root / 'core/img/optimized'
        destination.mkdir(parents=True, exist_ok=True)
        sources = []
        for folder in ('products', 'best_product', 'femmes', 'hommes', 'enfants', 'bb'):
            sources.extend(p for p in (root/'core/img'/folder).rglob('*') if p.suffix.lower() in ('.png','.jpg','.jpeg') and p.is_file())
        manifest = {}
        before = after = 0
        for path in sorted(sources):
            fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()[:20]
            with Image.open(path) as original:
                original = ImageOps.exif_transpose(original)
                original = original.convert('RGBA' if 'A' in original.getbands() else 'RGB')
                variants = []
                for target in sorted({160,320,640,min(original.width,960)}):
                    width = min(target, original.width)
                    if any(v['width']==width for v in variants):
                        continue
                    output = destination/f'{fingerprint}-{width}.webp'
                    if not output.exists():
                        size = (width, max(1,round(original.height*width/original.width)))
                        original.resize(size,Image.Resampling.LANCZOS).save(output,'WEBP',quality=86,method=6)
                    variants.append({'width':width,'path':output.relative_to(root).as_posix()})
                manifest[path.relative_to(root).as_posix()] = variants
                before += path.stat().st_size
                after += (root/variants[-1]['path']).stat().st_size
        (root/'core/product-images.json').write_text(json.dumps(manifest,indent=2)+'\n')
        self.stdout.write(f'{len(manifest)} photos : originaux {before} octets ; plus grandes versions WebP {after} octets.')

        # Match the existing mobile object-fit crop without downloading the wide 4K photo.
        with Image.open(root/'core/img/hero/montagne-3840.webp') as hero:
            crop_width = round(hero.height * 374 / 740)
            left = round((hero.width - crop_width) * .6)
            portrait = hero.crop((left, 0, left + crop_width, hero.height))
            for width in (600,960):
                portrait.resize((width,round(portrait.height*width/portrait.width)),Image.Resampling.LANCZOS).save(root/f'core/img/hero/montagne-mobile-{width}.webp','WEBP',quality=88,method=6)
