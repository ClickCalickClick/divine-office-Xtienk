# Divine Office for the Xteink X4 Pro

A Liturgy of the Hours reader for the Xteink X4 Pro (stock XTOS Licorice firmware, as an InkApp).
It shows the day's eight Hours, the Mass readings and texts, Lectio Divina and the notes on the
day, all taken from your own Universalis e-book export.

**Private use only.** Universalis e-books "are for your private use only and must not be given or
distributed to other people". This repository holds the app and the converter only. The e-books
and the data files made from them are kept out by `.gitignore`. Keep the repository private and
never submit the app to the InkApp store.

![Screens in the emulator](docs/emulator-v2.png)

## What is in it

| path | what it is |
|---|---|
| `app/office/` | the InkApp: `manifest.json`, `index.lua` (the app), `lockscreen.lua` (lock screen) |
| `tools/convert.py` | turns Universalis year EPUBs into the app's data files |
| `tools/emulator/` | scripted runs in the [inkemu](https://pypi.org/project/inkemu/) emulator |
| `docs/` | screenshots |

## Features

- **Today:** date, celebration (with the optional memorial to choose), the eight Hours with the
  current one marked "Now", and buttons for Mass readings, Mass, Lectio Divina and About Today.
  Before 4 am the app still shows the previous day, so Night Prayer stays where you expect it.
- **Reader:** pages turned by tapping, swiping or the side buttons; **Sections** jumps to any part
  (Psalmody, Reading, Gospel, Intercessions); **Aa** cycles the text size.
- **Choices:** days with more than one Mass (Vigil and Mass of the Day, for example) ask which.
- **Reopens where you left off** on the same liturgical day.
- **Calendar:** a month view with solemnities and feasts marked; tap a day to open it.
- **Settings:** text size, ℣ ℟ or V. R., line spacing, margins, lock screen on or off.
- **Lock screen:** today's celebration, the Hour for now and the next one, updated hourly.
- **End-of-data reminder** in December when next year's data is missing.

## Making the data

1. In Universalis (registered copy), export each year as an EPUB e-book for your diocese.
2. Convert (needs Python 3 with `beautifulsoup4` and `lxml`):

   ```
   python3 tools/convert.py uy2026.epub uy2027.epub --out build
   ```

   For 2026 and 2027 this writes about 317 files (40 MB): `cal2026.txt`, `cal2027.txt` and the
   bundles `h…bin` (the Hours), `m…bin` (Mass readings, Lectio Divina, About Today) and `x…bin`
   (Mass texts), each about 120 KB, because the app can only read whole files.
3. Copy everything in `build/` to the SD card folder `XTApps/office/files/` (reader in USB mode).
   The app may store at most 512 files, so keep two years at a time.

Uninstalling the app deletes `XTApps/office/files/`; installing over it keeps the files.

## Installing the app

Through Xteink's InkApp AI Studio (https://xtapp-ai.xteink.cc/developer): apply for developer
access with your XTEINK account, put the three files from `app/office/` in a project, and use
**Install on device**. Do not submit it to the store. The app code holds no Universalis text.

## Testing in the emulator

```
pipx install inkemu
mkdir -p .emu/office/files && cp build/* .emu/office/files/
inkemu window app/office --state .emu/office            # try it by hand
python3 tools/emulator/tour.py .emu/office              # screenshots of every screen in ./tour/
python3 tools/emulator/sweep.py .emu/office             # open everything on sampled days
```

The emulator's fonts are close to, but not the same as, the device's. Still to check on a real
X4 Pro: the ℣ and ℟ glyphs (Settings can switch to V. and R.), and the lock screen, which needs
firmware with app lock screens.

## Data format

See the header of `tools/convert.py`. In short: `cal<YYYY>.txt` has one line per day
(`date|title|rank|bundle files|item codes|memorial|variant labels`); each bundle starts with
`XO1 <header length> <zlib flag>`, then lines `date code offset length`, then the compressed
items in a small tagged text format (`#H` heading, `#P` psalm title, `#A` antiphon, `V`/`v`/`i`
verse lines, `P`/`p` paragraphs and so on).
