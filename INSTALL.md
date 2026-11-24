# Installing Divine Office on the X4 Pro

Here is the step-by-step process to install Divine Office (and its data) on your X4 Pro.

## 1. Install `office.xtapp`

1. Connect the X4 Pro to your computer and put the reader in USB mode.
2. Copy `dist/office.xtapp` to the **root of the SD card** (not inside a subfolder).
3. Safely eject the SD card and exit USB mode on the reader.
4. On the X4 Pro, open **All Files**, tap `office.xtapp`, and confirm **Install** (you should see
   an **Installed** toast).

## 2. Open it from Ink App

1. Open the side drawer (≡) → **Ink App** → **Divine Office**.
2. (First time only) If **Ink App** does not appear in the drawer yet, unlock it by going to
   **Settings → About** and tapping **Model** 10 times quickly (under 2 seconds between taps).

## 3. Add your Universalis data files

The `.xtapp` contains only the app code and icon, not the liturgical texts. To load the Hours and
Mass texts:

1. Export your year e-book(s) as EPUB from your registered copy of Universalis and convert them
   with [`tools/convert.py`](tools/convert.py):

   ```bash
   python3 tools/convert.py uy2026.epub uy2027.epub --out build
   ```

2. Put the X4 Pro back in USB mode and copy all files inside `build/` (`calYYYY.txt` and `*.bin`)
   into:

   ```text
   XTApps/office/files/
   ```

   (Create the `files/` folder inside `XTApps/office/` if it isn't there yet. Keep at most two
   years at a time to stay within the 512-file app quota.)
3. Eject and launch **Divine Office**.

## Helpful notes

- **Updating later:** Installing a new `office.xtapp` over an existing installation keeps
  everything in `XTApps/office/files/`. Uninstalling the app deletes that folder.
- **Lock screen (optional):** Turn it on inside **Divine Office → Settings → Lock screen**, and
  ensure the reader's **Settings → Display → Off wallpaper** is set to **Default**.
- **If you see "Install failed":** Your firmware build may have rotated the device key. Dump the
  flash with `esptool` and re-pack with `uv run tools/xtpack.py --image x4pro-flash.bin` (see
  [README.md](README.md)).
