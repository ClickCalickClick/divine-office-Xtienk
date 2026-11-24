"""Open every Hour, Mass, Lectio and About on sampled days of both years and page to the end.
Reports app errors, the most draw commands on one screen and the longest item.

  python3 tools/emulator/sweep.py STATE_DIR

STATE_DIR/files/ must hold the converter output (cal<YYYY>.txt and the .bin bundles)."""
import sys
import datetime, os
from inkemu import Emu
APP=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'app', 'office')
STATE=sys.argv[1] if len(sys.argv) > 1 else '.emu/office'
days=[datetime.date(2026,1,1)+datetime.timedelta(d) for d in range(0,730,29)]
days+=[datetime.date(2026,12,25),datetime.date(2027,3,25),datetime.date(2027,3,27),datetime.date(2027,3,28),datetime.date(2026,1,4),datetime.date(2026,3,10)]
mx=(0,None); mp=(0,None); errs=[]; n=0; chooses=0
taps=[(100,222+i*48+24) for i in range(8)]+[(M,640) for M in (60,170,280,390)]
for d in days:
    for f in ('state.json',):
        try: os.remove(STATE + '/'+f)
        except FileNotFoundError: pass
    e=Emu(APP, state_dir=STATE, start_time=f'{d}T10:00:00')
    for k,(x,y) in enumerate(taps):
        e.tap(x,y)
        if e.has_text('This day has more than one choice:'):
            chooses+=1; e.tap(240,170)
        if e.has_text('Calendar'):
            errs.append((str(d),k,[t['text'] for t in e.texts() if 'export' in t['text'] or 'Missing' in t['text'] or 'Not' in t['text']])); continue
        p=0
        while not e.has_text('Calendar') and p<200:
            fc=e.status()['frame_commands']
            if fc>mx[0]: mx=(fc,(str(d),k,p))
            e.key('down'); p+=1
        if p>mp[0]: mp=(p,(str(d),k))
        n+=1
    if e.errors: errs.append((str(d),e.errors[:2]))
print('opened',n,'choices',chooses,'max cmds',mx,'max pages',mp); print('problems',errs[:12])
