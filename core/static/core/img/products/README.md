# Product image folders

Place multiple images per product inside `core/static/core/img/products/<slug>/`.

Recommended naming:
- 1.png (main image)
- 2.png
- 3.png

Supported formats: .png, .jpg, .jpeg, .webp

Notes:
- The preview page automatically discovers these images and shows them as a gallery.
- The first image is used as the main image and for the lightbox.
- You can still pass a temporary `img` query parameter in the homepage links while migrating; once folders are ready, it can be removed.
