"""Scripted tour of every screen; saves screenshots under ./tour/.

  python3 tools/emulator/tour.py STATE_DIR

STATE_DIR/files/ must hold the converter output (cal<YYYY>.txt and the .bin bundles)."""
import sys
import os, shutil
from inkemu import Emu
APP=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'app', 'office')
STATE=sys.argv[1] if len(sys.argv) > 1 else '.emu/office'
os.makedirs('tour', exist_ok=True)
def fresh(t):
    for f in ('state.json','lock.json'):
        try: os.remove(STATE + '/'+f)
        except FileNotFoundError: pass
    try: os.remove(STATE + '/files/prefs.txt')
    except FileNotFoundError: pass
    return Emu(APP, state_dir=STATE, start_time=t)
e=fresh('2026-11-24T07:30:00')
e.save_shot('tour/01-home.png')
e.tap_text('Readings'); e.save_shot('tour/02-mass-readings.png')
e.tap(240, 20); e.tap_text('Mass'); e.key('down'); e.key('down')
e.tap(60, 780); e.save_shot('tour/03-sections.png')
e.tap(240, 84+3*50+25); e.save_shot('tour/04-jumped.png')
e.tap(240, 20); e.tap_text('About'); e.save_shot('tour/05-about.png')
e.tap(240,20); e.tap_text('Lectio'); e.save_shot('tour/06-lectio.png')
e.tap(240,20); e.tap_text('Calendar'); e.save_shot('tour/07-month.png')
e.key('right'); e.key('down'); e.save_shot('tour/08-month-cursor.png')
e.key('back'); e.tap_text('Settings'); e.tap(400, 84+2*92+50); e.tap(400, 84+3*92+50); e.tap(400, 84+1*92+50); e.save_shot('tour/09-settings.png')
e.tap_text('Done'); e.tap_text('Evening Prayer'); e.save_shot('tour/10-relaxed-wide-VR.png')
print('errors A', e.errors)
e=fresh('2026-01-04T09:00:00'); e.tap_text('Mass'); e.save_shot('tour/11-choose.png'); e.tap(240, 140+76+30); e.save_shot('tour/12-mass-of-day.png')
print('errors B', e.errors)
e=fresh('2027-12-05T18:00:00'); e.save_shot('tour/13-dec2027-notice.png'); e.lock(); e.save_shot('tour/14-lock.png')
print('errors C', e.errors)
# resume: open Lauds page 3, restart, expect same page
e=fresh('2026-11-24T07:30:00'); e.tap_text('Morning Prayer'); e.key('down'); e.key('down'); e.wait(1000)
e.leave() if hasattr(e,'leave') else None
e2=Emu(APP, state_dir=STATE, start_time='2026-11-24T08:10:00'); e2.save_shot('tour/15-reopened.png')
print('reopened texts', [t['text'] for t in e2.texts()][-1:], e2.errors)
