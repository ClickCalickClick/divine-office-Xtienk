#!/usr/bin/env python3
"""Make an InkApp AI Studio project backup of the app, to import into Studio and install from there.

  python3 tools/studio_backup.py            ->  dist/office-studio-backup.zip

In Studio (https://xtapp-ai.xteink.cc, developer access needed): More (...) -> Import backup,
pick the zip; it becomes a new project. Then "Install on device". Do not submit it to the store.

The zip holds only the app's code and icon, never Universalis text: the readings and prayers are
copied to the SD card separately (see README).

The archive is Studio's own backup format ("xtapp-studio-project-archive" v1), as documented by
the touch-licorice-apps project (tools/store_export.py, MIT licence, Omer Faruk Inel):
    xtapp-studio/project.json   name, device, manifest, asset list, source list
    project-files/<path>        manifest.json and the .lua files
    assets/xic/icon.xic         the app icon, 88x88, 1 bpp (XIC image)
    assets/raw/icon.png         the same as PNG
    presentation/store_icon.png the icon at 512x512 (Studio's project card)
"""
import datetime, io, json, os, struct, zipfile
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(HERE, 'app', 'office')
OUT = os.path.join(HERE, 'dist', 'office-studio-backup.zip')

# the API the app is written against: 2.1 zlib, 2.2 ctx.state, 2.8 lockscreen, 2.9 ctx.storage
API = '2.9'
# official 2.x manifest fields; anything else is dropped
FIELDS = ('api', 'app_id', 'display_name', 'display_names', 'version', 'author', 'description',
          'entry', 'display', 'requires', 'permissions', 'preload_assets', 'data_files', 'lockscreen')
ICON_PX, STORE_PX = 88, 512


def draw_icon(px):
    """A cross over an open book, black on white."""
    s = px / 100.0
    im = Image.new('L', (px, px), 255)
    d = ImageDraw.Draw(im)
    w = max(2, int(5 * s))
    # open book
    d.polygon([(10 * s, 58 * s), (50 * s, 66 * s), (50 * s, 92 * s), (10 * s, 84 * s)], outline=0, width=w)
    d.polygon([(90 * s, 58 * s), (50 * s, 66 * s), (50 * s, 92 * s), (90 * s, 84 * s)], outline=0, width=w)
    # cross
    d.rectangle([44 * s, 6 * s, 56 * s, 56 * s], fill=0)
    d.rectangle([30 * s, 18 * s, 70 * s, 29 * s], fill=0)
    return im


def xic_1bpp(img):
    """The firmware's 1-bpp image: 24-byte header (magic, u16 width, u16 height, version 1,
    format 1, planes 1, u32 data size) and rows of ceil(w/8) bytes, MSB first, dark pixels = 1."""
    img = img.convert('L')
    w, h = img.size
    stride = (w + 7) // 8
    px = img.load()
    rows = bytearray(stride * h)
    for y in range(h):
        for x in range(w):
            if px[x, y] < 128:
                rows[y * stride + x // 8] |= 0x80 >> (x % 8)
    head = bytearray(24)
    head[0:4] = b'XIC\0'
    struct.pack_into('<HHBBB', head, 4, w, h, 1, 1, 1)
    struct.pack_into('<I', head, 16, len(rows))
    return bytes(head) + bytes(rows)


def png(img):
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


def main():
    src = json.load(open(os.path.join(APP, 'manifest.json'), encoding='utf-8'))
    m = {k: src[k] for k in FIELDS if k in src}
    m['api'] = API
    m['app_icon_s'] = 'icon'
    if not m.get('permissions'):
        m.pop('permissions', None)
    if m.get('lockscreen'):
        m['lockscreen'] = {k: m['lockscreen'][k] for k in ('entry', 'interval_sec') if k in m['lockscreen']}
    sources = {'manifest.json': json.dumps(m, indent=2, ensure_ascii=False) + '\n'}
    for fn in sorted(os.listdir(APP)):
        if fn.endswith('.lua'):
            sources[fn] = open(os.path.join(APP, fn), encoding='utf-8').read()

    icon = draw_icon(ICON_PX * 4).resize((ICON_PX, ICON_PX), Image.LANCZOS).point(lambda v: 0 if v < 128 else 255)
    store = Image.new('L', (STORE_PX, STORE_PX), 255)
    inner = int(STORE_PX * 0.82)
    store.paste(draw_icon(inner), ((STORE_PX - inner) // 2,) * 2)

    meta = {
        'format': 'xtapp-studio-project-archive', 'version': 1,
        'project': {
            'sourceProjectId': '', 'name': m['display_name'], 'device': 'x4_pro', 'manifest': m,
            'assetRefs': {'icon': {}}, 'folders': [],
            'presentation': {'storeCover': None,
                             'storeIcon': {'key': 'store_icon', 'mime': 'image/png', 'width': STORE_PX,
                                           'height': STORE_PX, 'path': 'presentation/store_icon.png'}},
        },
        'sourceFiles': sorted(sources),
        'assets': [{'key': 'icon', 'width': ICON_PX, 'height': ICON_PX, 'bpp': 1, 'contentHash': '',
                    'process': {}, 'xicPath': 'assets/xic/icon.xic', 'rawPath': 'assets/raw/icon.png',
                    'rawType': 'image/png', 'xicType': 'application/x-xic'}],
        'exportedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    files = {
        'xtapp-studio/project.json': (json.dumps(meta, indent=2, ensure_ascii=False) + '\n').encode('utf-8'),
        'presentation/store_icon.png': png(store),
        'assets/xic/icon.xic': xic_1bpp(icon),
        'assets/raw/icon.png': png(icon.convert('1')),
    }
    files.update({'project-files/' + p: s.encode('utf-8') for p, s in sources.items()})
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            z.writestr(name, files[name])
    print(os.path.relpath(OUT, HERE) + ': ' + ', '.join(f'{n} {len(b)} B' for n, b in sorted(files.items())))


if __name__ == '__main__':
    main()
