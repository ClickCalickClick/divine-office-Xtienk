#!/usr/bin/env python3
"""Convert a Universalis year EPUB into data files for the Divine Office InkApp.

Usage:  python3 convert.py uy2026.epub [uy2027.epub ...] --out OUTDIR

For your private use only: Universalis e-books must not be shared or distributed.

Output (all flat files, as the device's app storage allows no sub-folders):
  cal<YYYY>.txt   one line per day:
                    YYYYMMDD|title|rank|h-file,m-file,x-file|codes|memorial titles|variant labels
                  codes: I R L T S N V C (the Hours), M Mass readings, X Mass texts,
                  D Lectio Divina, A About Today; each followed by its variant number (0 = main).
                  memorial titles: "1=Saint ..." (variant 1 of the Hours is that memorial).
                  variant labels: "X0=Vigil Mass;X1=Mass of the Day;M1=Saint ...".
  <g><YYYY><NNN>.bin  bundles of consecutive days, about 120 KB each, in three groups
                  (h = the Hours, m = Mass readings, Lectio Divina, About Today, x = Mass texts),
                  about 170 files a year, so two years fit the 512-file app quota. Layout:
                    line 1:  "XO1 <header bytes> <z>\n"   (z = 1 when segments are zlib)
                    header:  lines "YYYYMMDD <code><variant> <offset> <length>"
                    body:    the segments, each one item in the tagged text format below
                  The app reads one bundle, cuts out one segment and (if z=1) inflates it,
                  so only one Hour is ever held as text.

Tagged text format, one block per line:
  -            short rule between sections
  #H a|b       section heading (left a, right b, e.g. "Scripture Reading|Baruch 5:3-4")
  #P text      psalm / canticle title     #Q text  its subtitle
  #E text      epigraph (the quoted line under a psalm title)
  #R text      rubric                     #A text  antiphon
  #O           "Or:" between alternative prayers
  V text       verse line, space before   v text   verse line
  i text       indented verse continuation
  P text       paragraph, space before    p text   paragraph, first-line indent
Versicle and response keep the ℣ and ℟ signs; the app can swap them for V. and R.
"""
import argparse, os, re, sys, zipfile, zlib, datetime
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup, NavigableString, Tag

NS = {'n': 'http://www.daisy.org/z3986/2005/ncx/'}
HOURS = {
    'Invitatory Psalm': 'I', 'Office of Readings': 'R', 'Morning Prayer (Lauds)': 'L',
    'Mid-Morning Prayer (Terce)': 'T', 'Midday Prayer (Sext)': 'S',
    'Afternoon Prayer (None)': 'N', 'Vespers (Evening Prayer)': 'V', 'Compline (Night Prayer)': 'C',
}
EXTRA = {'Readings at Mass': 'M', 'Lectio Divina': 'D', 'About Today': 'A'}
ORDER = 'IRLTSNVCMXDA'
HOUR_CODES = 'IRLTSNVC'
GROUPS = 'hmx'          # bundle files: h = the Hours, m = Mass readings + Lectio + About, x = Mass texts
BUNDLE = 120000         # target bytes per bundle file


def group_of(code):
    return 'h' if code in HOUR_CODES else ('x' if code == 'X' else 'm')


def item_code(label):
    """Hour or extra code for a contents label; 'Mass Today (Vigil Mass)' -> ('X', 'Vigil Mass')."""
    if label in HOURS:
        return HOURS[label], ''
    if label in EXTRA:
        return EXTRA[label], ''
    m = re.match(r'^Mass Today(?: \((.*)\))?$', label)
    if m:
        return 'X', m.group(1) or ''
    return None, ''


def short_label(label):
    m = re.match(r'^Mass Today \((.*)\)$', label)
    return m.group(1) if m else label


def clean(s):
    s = s.replace('\xa0', ' ').replace(' ', ' ')
    return re.sub(r'\s+', ' ', s).strip()


def text_of(el):
    return clean(el.get_text(''))


def is_antiphon(div):
    """A verse block whose whole text sits in <i> (Universalis prints antiphons in italics)."""
    kids = [k for k in div.children if not (isinstance(k, NavigableString) and not k.strip())]
    return len(kids) == 1 and isinstance(kids[0], Tag) and kids[0].name == 'i'


def emit_block(el, out):
    if isinstance(el, NavigableString):
        return
    name, cls = el.name, ' '.join(el.get('class') or [])
    if name == 'div' and 'navigation' in cls:
        return
    if name == 'hr':
        if 'shortrule' in cls:
            out.append('-')
        return
    if name == 'table' and 'each' in cls:
        ths = [text_of(t) for t in el.find_all('th')]
        left = ths[0] if ths else ''
        right = ths[1] if len(ths) > 1 else ''
        if left.rstrip(':').lower() == 'or':
            out.append('#O')
        else:
            out.append('#H ' + left + ('|' + right if right else ''))
        return
    if name == 'h4':
        parts = [clean(p) for p in re.split(r'<br\s*/?>', el.decode_contents())]
        parts = [clean(BeautifulSoup(p, 'html.parser').get_text('')) for p in parts if p]
        parts = [p for p in parts if p]
        if parts:
            out.append('#P ' + parts[0])
            for p in parts[1:]:
                out.append('#Q ' + p)
        return
    if name == 'h2':
        return
    if name == 'div' and any(b in cls for b in ('firstblock', 'middleblock', 'lastblock')):
        if 'firstblock' not in cls:
            out.append('#O')
        for k in el.children:
            emit_block(k, out)
        return
    if name == 'p':
        if 'rubric' in cls:
            t = text_of(el)
            if t:
                out.append('#R ' + t)
            return
        links = el.find_all('a')
        t = text_of(el)
        if links and clean(''.join(a.get_text('') for a in links)) == t:
            return  # "1. God our Father..." choice links and "Continue"
        if t:
            out.append('P ' + t)
        return
    if name == 'div':
        if el.find('blockquote') is not None:
            t = text_of(el)
            if t:
                out.append('#E ' + t)
            return
        t = text_of(el)
        if not t:
            return
        if 'rubric' in cls:
            out.append('#R ' + t)
        elif cls in ('vgb', 'v') and is_antiphon(el) and t[0] not in '℣℟':
            out.append('#A ' + t)
        elif cls == 'vgb':
            out.append('V ' + t)
        elif cls == 'v':
            out.append('v ' + t)
        elif cls in ('vi', 'vigb'):
            out.append('i ' + t)
        elif cls == 'pgb':
            out.append('P ' + t)
        elif cls == 'pi':
            out.append('p ' + t)
        else:
            out.append('P ' + t)
        return
    for k in el.children:  # anything else: look inside
        emit_block(k, out)


def convert_hour(html):
    soup = BeautifulSoup(html, 'lxml')
    body = soup.body
    nav = body.find('div', class_='navigation')
    title, rank = '', ''
    if nav is not None:
        p = nav.find('p')
        if p is not None:
            st = p.find('strong')
            title = text_of(st) if st else ''
            em = p.find('em')
            if em is not None and 'see also' not in em.get_text():
                rank = text_of(em)
    out = []
    started = False
    for el in body.children:
        if isinstance(el, Tag) and el.name == 'h2':
            started = True
            continue
        if not started:
            continue
        if isinstance(el, Tag) and el.name == 'hr' and not el.get('class'):
            break  # the closing navigation bar follows
        emit_block(el, out)
    while out and out[0] == '-':
        out.pop(0)
    while out and out[-1] == '-':
        out.pop()
    return title, rank, '\n'.join(out) + '\n'


def parse_day_label(s):
    return datetime.datetime.strptime(s, '%A %d %B %Y').date()


def convert_epub(path, outdir, use_zlib=True):
    z = zipfile.ZipFile(path)
    root = ET.fromstring(z.read('OEBPS/toc.ncx'))
    days = []
    for month in root.find('n:navMap', NS).findall('n:navPoint', NS):
        for d in month.findall('n:navPoint', NS):
            date = parse_day_label(d.find('n:navLabel/n:text', NS).text)
            hours = []
            nextv = {}
            for h in d.findall('n:navPoint', NS):
                code, plabel = item_code(h.find('n:navLabel/n:text', NS).text)
                if not code:
                    continue
                items = [(plabel, h.find('n:content', NS).get('src'))]
                items += [(short_label(a.find('n:navLabel/n:text', NS).text), a.find('n:content', NS).get('src'))
                          for a in h.findall('n:navPoint', NS)]
                for label, src in items:
                    vi = nextv.get(code, 0)
                    nextv[code] = vi + 1
                    hours.append((code, vi, src.split('#')[0], label))
            days.append((date, hours))
    year = days[0][0].year
    # 1. convert and compress every item
    segs = {g: [] for g in GROUPS}        # group -> [(ymd, key, data)]
    cal_rows, total_raw = [], 0
    for date, hours in days:
        ymd = date.strftime('%Y%m%d')
        titles, codes, labels = {}, [], []
        for code, vi, src, label in sorted(hours, key=lambda t: (ORDER.index(t[0]), t[1])):
            title, rank, text = convert_hour(z.read('OEBPS/' + src).decode('utf-8'))
            raw = text.encode('utf-8')
            total_raw += len(raw)
            data = zlib.compress(raw, 9) if use_zlib else raw
            segs[group_of(code)].append((ymd, '%s%d' % (code, vi), data))
            codes.append('%s%d' % (code, vi))
            if label:
                labels.append('%s%d=%s' % (code, vi, label.replace(';', ',').replace('|', '/')))
            if code in HOUR_CODES and vi not in titles and (title or rank):
                titles[vi] = (title, rank)
        t0 = titles.get(0, ('', ''))
        alts = ';'.join('%d=%s' % (k, v[0]) for k, v in sorted(titles.items()) if k > 0)
        cal_rows.append([ymd, t0[0], t0[1], {}, ','.join(codes), alts, ';'.join(labels)])
    # 2. pack consecutive days into bundles of about BUNDLE bytes (the app reads a whole file)
    files_of = {}                           # (group, ymd) -> file name
    total_out, nfiles = 0, 0
    for g, items in segs.items():
        bundles, cur, size, last = [], [], 0, None
        for ymd, key, data in items:
            if cur and ymd != last and size + len(data) > BUNDLE:
                bundles.append(cur); cur, size = [], 0
            cur.append((ymd, key, data)); size += len(data); last = ymd
        if cur:
            bundles.append(cur)
        for n, b in enumerate(bundles):
            fname = '%s%d%03d.bin' % (g, year, n)
            header, off = [], 0
            for ymd, key, data in b:
                header.append('%s %s %d %d' % (ymd, key, off, len(data)))
                off += len(data)
                files_of[(g, ymd)] = fname
            htxt = ('\n'.join(header) + '\n').encode('utf-8')
            first = ('XO1 %d %d\n' % (len(htxt), 1 if use_zlib else 0)).encode()
            with open(os.path.join(outdir, fname), 'wb') as f:
                f.write(first + htxt + b''.join(d for _, _, d in b))
            total_out += len(first) + len(htxt) + off
            nfiles += 1
    lines = []
    for r in cal_rows:
        r[3] = ','.join(files_of.get((g, r[0]), '-') for g in GROUPS)
        lines.append('|'.join(r))
    with open(os.path.join(outdir, 'cal%d.txt' % year), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return year, len(days), total_raw, total_out, nfiles + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('epubs', nargs='+')
    ap.add_argument('--out', required=True)
    ap.add_argument('--no-zlib', action='store_true')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for p in a.epubs:
        y, n, raw, out, nf = convert_epub(p, a.out, not a.no_zlib)
        print('%d: %d days, %.1f MB text, %.1f MB written in %d files' % (y, n, raw / 1e6, out / 1e6, nf))


if __name__ == '__main__':
    main()
