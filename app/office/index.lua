-- Divine Office: a Liturgy of the Hours reader for the Xteink X4 Pro (InkApp, API 2.x).
-- Data: files made by tools/convert.py from your own Universalis export, copied into the
-- app's storage folder (XTApps/office/files/ on the SD card). Private use only.
--
-- Screens: home (the day: its Hours, Mass and more), read (one item, paged), toc (sections of
-- the item), choose (Vigil or Mass of the Day and similar), month (go to a date), settings.

local W, H = 480, 800
local M = 24                -- margin for the app's own chrome
local HEAD_H = 70           -- reader header
local FOOT_H = 44           -- reader footer

local HOURS = {
  { c = 'I', name = 'Invitatory',         short = 'Invitatory' },
  { c = 'R', name = 'Office of Readings', short = 'Readings' },
  { c = 'L', name = 'Morning Prayer',     short = 'Lauds',    from = 4,  to = 9 },
  { c = 'T', name = 'Mid-Morning Prayer', short = 'Terce',    from = 9,  to = 12 },
  { c = 'S', name = 'Midday Prayer',      short = 'Sext',     from = 12, to = 15 },
  { c = 'N', name = 'Afternoon Prayer',   short = 'None',     from = 15, to = 17 },
  { c = 'V', name = 'Evening Prayer',     short = 'Vespers',  from = 17, to = 21 },
  { c = 'C', name = 'Night Prayer',       short = 'Compline', from = 21, to = 28 },
}
local EXTRAS = {
  { c = 'M', name = 'Mass Readings', btn = 'Readings', main = 'Readings of the day' },
  { c = 'X', name = 'Mass',          btn = 'Mass',     main = 'Mass of the day' },
  { c = 'D', name = 'Lectio Divina', btn = 'Lectio',   main = 'Lectio Divina' },
  { c = 'A', name = 'About Today',   btn = 'About',    main = 'About today' },
}
local ITEM = {}             -- code -> entry, with .i = position on the home screen (1-12)
for i, h in ipairs(HOURS) do ITEM[h.c] = h; h.i = i end
for i, e in ipairs(EXTRAS) do ITEM[e.c] = e; e.i = #HOURS + i end
local NSEL = #HOURS + #EXTRAS

local WEEKDAYS = { 'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday' }
local MONTHS = { 'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
  'September', 'October', 'November', 'December' }

-- settings and their choices
local SIZES = { 20, 24, 28 }               -- text size (px); the host snaps them to its fonts
local SPACING = { 0, 3, 7 }                -- extra px between lines
local MARGINS = { 16, 24, 40 }             -- reader side margin
local SAMPLE = '℣℟–—‘’“”…æœÆŒéèêàâç'

------------------------------------------------------------------ state

local S = {
  screen = 'home',      -- home | read | toc | choose | month | settings
  ymd = nil,            -- day shown, 'YYYYMMDD'
  sel = 3,              -- highlighted entry on home (keys), 1-12
  variant = 0,          -- 1 when the day's optional memorial is chosen
  item = nil, open_v = 0, page = 1, pages = nil, toc = nil, lday = nil,
  size = 2, vr = true, spacing = 2, margin = 2, lock = false,
  err = nil,
  turns = 0,
}

------------------------------------------------------------------ helpers

local function log(s) pcall(function() ctx:log(tostring(s)) end) end
local function redraw() pcall(function() ctx:invalidate() end) end
local function collect() pcall(function() ctx.gc:collect() end) end

local font_cache = {}
local function font(px)
  if font_cache[px] then return font_cache[px] end
  local ok, f = pcall(function() return ctx.fonts:size(px, SAMPLE) end)
  if not ok or not f then ok, f = pcall(function() return ctx.fonts:size(px) end) end
  if not ok or not f then f = ctx.fonts.medium end
  font_cache[px] = f
  return f
end
local function lh(f) local ok, h = pcall(function() return f.line_height end); return (ok and tonumber(h)) or 24 end
local function width(f, s)
  local ok, w = pcall(function() return f:measure(s) end)
  return (ok and type(w) == 'number') and w or #s * 11
end
local function text(g, x, y, s, f) g:text(x, y, s, { font = f }) end
local function ctext(g, cx, y, s, f) text(g, cx - width(f, s) // 2, y, s, f) end
local function rule(g, x, y, w) g:line(x, y, x + w, y) end
local function frame(g, x, y, w, h, thick)
  g:rect(x, y, w, h)
  if thick then g:rect(x + 1, y + 1, w - 2, h - 2); g:rect(x + 2, y + 2, w - 4, h - 4) end
end

local function sread(name)
  local ok, s = pcall(function() return ctx.storage:read(name) end)
  return (ok and type(s) == 'string') and s or nil
end
local function swrite(name, s) pcall(function() ctx.storage:write(name, s) end) end
local function sexists(name)
  local ok, e = pcall(function() return ctx.storage:exists(name) end)
  if ok and type(e) == 'boolean' then return e end
  ok, e = pcall(function() return ctx.storage:size(name) end)
  return ok and tonumber(e) ~= nil
end

-- cut a string to max_w pixels, on a UTF-8 boundary, adding an ellipsis
local function fit(s, f, max_w)
  if width(f, s) <= max_w then return s end
  local ell = '…'
  while s ~= '' and width(f, s .. ell) > max_w do
    s = s:sub(1, -2)
    while s ~= '' and (s:byte(-1) & 0xC0) == 0x80 do s = s:sub(1, -2) end
    if s ~= '' and s:byte(-1) >= 0xC0 then s = s:sub(1, -2) end
  end
  return (s:gsub('%s+$', '')) .. ell
end

local function wrap(s, f, first_w, rest_w)
  local out, line, maxw = {}, '', first_w
  for word in s:gmatch('[^ ]+') do
    local cand = line == '' and word or (line .. ' ' .. word)
    if line ~= '' and width(f, cand) > maxw then
      out[#out + 1] = line
      line, maxw = word, rest_w or first_w
    else
      line = cand
    end
  end
  if line ~= '' then out[#out + 1] = line end
  return out
end

------------------------------------------------------------------ dates

-- days since 1970-01-01 <-> civil date (Howard Hinnant's algorithm, small integers only)
local function days_from_civil(y, m, d)
  if m <= 2 then y = y - 1 end
  local era = (y >= 0 and y or y - 399) // 400
  local yoe = y - era * 400
  local mp = (m + 9) % 12
  local doy = (153 * mp + 2) // 5 + d - 1
  local doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
  return era * 146097 + doe - 719468
end
local function civil_from_days(z)
  z = z + 719468
  local era = (z >= 0 and z or z - 146096) // 146097
  local doe = z - era * 146097
  local yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
  local y = yoe + era * 400
  local doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
  local mp = (5 * doy + 2) // 153
  local d = doy - (153 * mp + 2) // 5 + 1
  local m = mp < 10 and mp + 3 or mp - 9
  if m <= 2 then y = y + 1 end
  return y, m, d
end
local function ymd_of(y, m, d) return string.format('%04d%02d%02d', y, m, d) end
local function split_ymd(s) return tonumber(s:sub(1, 4)), tonumber(s:sub(5, 6)), tonumber(s:sub(7, 8)) end
local function add_days(s, n)
  local y, m, d = split_ymd(s)
  return ymd_of(civil_from_days(days_from_civil(y, m, d) + n))
end
local function weekday(s) -- 1 = Sunday
  local y, m, d = split_ymd(s)
  return (days_from_civil(y, m, d) + 4) % 7 + 1
end
local function month_len(y, m)
  local ny, nm = y, m + 1
  if nm > 12 then ny, nm = y + 1, 1 end
  return days_from_civil(ny, nm, 1) - days_from_civil(y, m, 1)
end
local function day_label(s, long)
  local y, m, d = split_ymd(s)
  local wd = WEEKDAYS[weekday(s)]
  if long then return wd .. ' ' .. d .. ' ' .. MONTHS[m] .. ' ' .. y end
  return wd:sub(1, 3) .. ' ' .. d .. ' ' .. MONTHS[m]:sub(1, 3)
end

local function now()
  local ok, t = pcall(function() return ctx.clock:now() end)
  if not ok or type(t) ~= 'table' then return nil end
  local y, m, d = tonumber(t.year), tonumber(t.month), tonumber(t.day)
  if not (y and m and d) and tonumber(t.local_sec) then
    y, m, d = civil_from_days(tonumber(t.local_sec) // 86400)
  end
  if not y then return nil end
  return { ymd = ymd_of(y, m, d), year = y, month = m, hour = tonumber(t.hour) or 12 }
end

-- the Hour that fits the time of day; before 4 am it is still yesterday's Compline
local function hour_for(h)
  if h < 4 then return 'C', -1 end
  for _, x in ipairs(HOURS) do
    if x.from and h >= x.from and h < x.to then return x.c, 0 end
  end
  return 'L', 0
end

-- the liturgical day and current Hour, or nil without a clock
local function liturgical_now()
  local t = now()
  if not t then return nil end
  local c, shift = hour_for(t.hour)
  return add_days(t.ymd, shift), c, t
end

------------------------------------------------------------------ calendar

-- year -> { ymd -> the day's line }; a line is parsed when needed (parsed tables for a whole
-- year would take too much of the heap)
local cal = {}
local parsed, nparsed = {}, 0
local function load_year(y)
  if cal[y] ~= nil then return cal[y] end
  local s = sread('cal' .. y .. '.txt')
  if not s then cal[y] = false; return false end
  local t = {}
  for line in s:gmatch('[^\n]+') do
    local ymd = line:match('^(%d+)|')
    if ymd then t[ymd] = line end
  end
  cal[y] = t
  return t
end

-- {title, rank, files = {h, m, x}, codes = {set}, alts = {n -> title}, labels = {code -> label}}
local function parse_day(line)
  local f = {}
  for part in (line .. '|'):gmatch('([^|]*)|') do f[#f + 1] = part end
  if #f < 5 then return nil end
  local set = {}
  for c in f[5]:gmatch('[^,]+') do set[c] = true end
  local at = {}
  for k, v in (f[6] or ''):gmatch('(%d+)=([^;]+)') do at[tonumber(k)] = v end
  local labels = {}
  for k, v in (f[7] or ''):gmatch('(%u%d+)=([^;]+)') do labels[k] = v end
  local files = {}
  for fn in f[4]:gmatch('[^,]+') do files[#files + 1] = fn end
  local rank = f[3]
  if rank == 'or' then rank = '' end
  return { title = f[2], rank = rank, files = files, codes = set, alts = at, labels = labels }
end

local function day_info(ymd)
  if parsed[ymd] then return parsed[ymd] end
  local t = load_year(tonumber(ymd:sub(1, 4)))
  local line = t and t[ymd]
  if not line then return nil end
  if nparsed >= 48 then parsed, nparsed = {}, 0 end
  local info = parse_day(line)
  parsed[ymd], nparsed = info, nparsed + 1
  return info
end
-- keep at most two years parsed
local function trim_cal(keep)
  for y in pairs(cal) do if math.abs(y - keep) > 1 then cal[y] = nil end end
end

------------------------------------------------------------------ one item's text

local function file_for(info, c)
  local g = (c == 'X') and 3 or ((c == 'M' or c == 'D' or c == 'A') and 2 or 1)
  local f = info.files[g] or info.files[1]
  return (f and f ~= '-') and f or nil
end

local function read_segment(file, ymd, key)
  local blob = sread(file)
  if not blob then return nil, 'Missing file ' .. file end
  local nl = blob:find('\n', 1, true)
  local hlen, z = blob:sub(1, nl - 1):match('^XO1 (%d+) (%d)')
  if not hlen then return nil, 'Unreadable file ' .. file end
  hlen = tonumber(hlen)
  local header = blob:sub(nl + 1, nl + hlen)
  local base = nl + hlen
  local off, len = header:match(ymd .. ' ' .. key .. ' (%d+) (%d+)')
  if not off then return nil, 'Not in ' .. file end
  local seg = blob:sub(base + tonumber(off) + 1, base + tonumber(off) + tonumber(len))
  blob, header = nil, nil
  if z == '1' then
    local ok, out = pcall(function() return ctx.zlib:decompress(seg) end)
    if not ok or type(out) ~= 'string' then return nil, 'Could not unpack the text' end
    seg = out
  end
  collect()
  return seg
end

------------------------------------------------------------------ layout

-- styles: font px from the body size, indent, space before
local function styles(body)
  return {
    H = { px = body - 4, gap = 14 },
    P_title = { px = body + 2, gap = 18 },
    Q = { px = body - 4, gap = 0 },
    E = { px = body - 4, ind = 24, gap = 4 },
    R = { px = body - 4, gap = 8 },
    A = { px = body, gap = 10, lead = 'Ant. ' },
    O = { px = body - 4, gap = 10 },
    V = { px = body, ind = 0, gap = 10, hang = 28 },
    v = { px = body, ind = 0, gap = 0, hang = 28 },
    i = { px = body, ind = 28, gap = 0, hang = 28 },
    Pp = { px = body, ind = 0, gap = 10 },
    p = { px = body, ind = 0, gap = 6 },
  }
end

-- turn tagged text into pages of draw items {x, y, s, f} or {rule = true, y},
-- and a list of sections {label, page}
local function paginate(src)
  local body_px = SIZES[S.size]
  local LS = SPACING[S.spacing]
  local RM = MARGINS[S.margin]
  local st = styles(body_px)
  local top, bottom = HEAD_H + 14, H - FOOT_H - 8
  local usable = W - 2 * RM
  local pages, cur, y = {}, {}, top
  local toc, last_tag = {}, nil
  -- a page is kept as one string, a line per item ("x y font text", or "- y" for a rule),
  -- as tables per item would not fit the heap for the longest Masses
  local fonts, fidx = {}, {}
  local function add(x, yy, str, f)
    local k = fidx[f]
    if not k then fonts[#fonts + 1] = f; k = #fonts; fidx[f] = k end
    cur[#cur + 1] = x .. ' ' .. yy .. ' ' .. k .. ' ' .. str
  end
  local function new_page() pages[#pages + 1] = table.concat(cur, '\n'); cur = {}; y = top end
  local function mark(label) toc[#toc + 1] = { label = label, page = #pages + 1 } end
  local function place(lines, f, x0, xr, gap, center, keep, label)
    local h = lh(f) + LS
    if y ~= top then y = y + gap end
    local need = (keep and math.min(#lines, 3) or 1) * h
    if y + need > bottom then new_page() end
    if label then mark(label) end
    for k, s in ipairs(lines) do
      if y + h > bottom then new_page() end
      local x = k == 1 and x0 or xr
      if center then x = (W - width(f, s)) // 2 end
      add(x, y, s, f)
      y = y + h
    end
  end
  for raw in (src .. '\n'):gmatch('([^\n]*)\n') do
    local tag, rest = raw:match('^(#?%a)%s?(.*)$')
    if raw == '-' then
      if y ~= top and y + 30 < bottom then
        y = y + 12
        cur[#cur + 1] = '- ' .. y
        y = y + 4
      end
    elseif tag then
      if not S.vr then rest = rest:gsub('\u{2123}%.?', 'V.'):gsub('\u{211F}%.?', 'R.') end
      if tag == '#H' then
        local a, b = rest:match('^([^|]*)|?(.*)$')
        local f = font(st.H.px)
        if y ~= top then y = y + st.H.gap end
        if y + (lh(f) + LS) * 3 > bottom then new_page() end
        mark(a .. ((b and b ~= '') and (': ' .. b) or ''))
        add(RM, y, a:upper(), f)
        if b and b ~= '' then add(W - RM - width(f, b), y, b, f) end
        y = y + lh(f) + LS
      elseif tag == '#P' then
        local f = font(st.P_title.px)
        place(wrap(rest, f, usable), f, RM, RM, st.P_title.gap, true, true, last_tag ~= '#H' and rest or nil)
      elseif tag == '#Q' then
        local f = font(st.Q.px)
        place(wrap(rest, f, usable), f, RM, RM, st.Q.gap, true, true)
      elseif tag == '#E' then
        local s = st.E
        local f = font(s.px)
        place(wrap(rest, f, usable - 2 * s.ind), f, RM + s.ind, RM + s.ind, s.gap)
      elseif tag == '#R' then
        local f = font(st.R.px)
        place(wrap(rest, f, usable), f, RM, RM, st.R.gap)
      elseif tag == '#A' then
        local f = font(st.A.px)
        place(wrap(st.A.lead .. rest, f, usable, usable - 28), f, RM, RM + 28, st.A.gap)
      elseif tag == '#O' then
        place({ 'Or:' }, font(st.O.px), RM, RM, st.O.gap)
      else
        local key = tag == 'P' and 'Pp' or tag
        local s = st[key] or st.Pp
        local f = font(s.px)
        local x0 = RM + (s.ind or 0)
        local xr = RM + (s.hang or 0)
        if key == 'p' then x0 = RM + 20 end
        if key == 'Pp' then xr = RM end
        place(wrap(rest, f, W - RM - x0, W - RM - xr), f, x0, xr, s.gap)
      end
      if tag ~= '#Q' and tag ~= '#E' then last_tag = tag end
    end
  end
  if #cur > 0 then pages[#pages + 1] = table.concat(cur, '\n') end
  S.page_fonts = fonts
  for _, t in ipairs(toc) do if t.page > #pages then t.page = #pages end end
  return pages, toc
end

------------------------------------------------------------------ saving

local function save_prefs()
  swrite('prefs.txt', string.format('size=%d\nvr=%s\nspacing=%d\nmargin=%d\nlock=%s\n',
    S.size, S.vr and '1' or '0', S.spacing, S.margin, S.lock and '1' or '0'))
end

-- where you are, so the app can reopen there
local function note_place(persist)
  pcall(function()
    local reading = (S.screen == 'read' or S.screen == 'toc') and S.item
    ctx.state.screen = reading and 'read' or 'home'
    ctx.state.ymd = S.ymd
    ctx.state.item = S.item or ''
    ctx.state.v = S.open_v or 0
    ctx.state.page = S.page or 1
    ctx.state.npages = S.pages and #S.pages or 1
    ctx.state.memorial = S.variant
    ctx.state.lday = S.lday or ''
    if persist then ctx:save_state() end
  end)
end

------------------------------------------------------------------ actions

-- the variants of an item for the day, sorted
local function variants(info, c)
  local out = {}
  for v = 0, 9 do if info.codes[c .. v] then out[#out + 1] = v end end
  return out
end

local function variant_label(info, c, v)
  local l = info.labels[c .. v]
  if l and l ~= '' then return l end
  if v == 0 then
    local e = ITEM[c]
    return e.main or info.title
  end
  return info.alts[v] or ('Option ' .. (v + 1))
end

local function open_variant(c, v, page)
  local info = day_info(S.ymd)
  if not info then S.err = 'No data for ' .. day_label(S.ymd, true); redraw(); return end
  local file = file_for(info, c)
  if not file then S.err = 'Not in your export'; redraw(); return end
  S.pages, S.toc = nil, nil
  collect()
  local src, err = read_segment(file, S.ymd, c .. v)
  if not src then S.err = err; redraw(); return end
  S.item, S.open_v = c, v
  local srclen = #src
  S.pages, S.toc = paginate(src)
  src = nil
  local bytes = 0
  for _, pg in ipairs(S.pages) do bytes = bytes + #pg end
  log(string.format('open %s%d %s: %d bytes text, %d pages, %d bytes laid out', c, v, S.ymd, srclen, #S.pages, bytes))
  collect()
  S.page = math.max(1, math.min(#S.pages, page or 1))
  S.screen = 'read'
  S.err = nil
  S.lday = liturgical_now() or S.ymd
  S.sel = ITEM[c].i
  note_place(true)
  redraw()
end

-- open an item, choosing the variant: the chosen memorial if it has one, else ask when the
-- day offers more than one (Vigil Mass or Mass of the Day, for example)
local function open_item(c)
  local info = day_info(S.ymd)
  if not info then S.err = 'No data for ' .. day_label(S.ymd, true); redraw(); return end
  local vs = variants(info, c)
  if #vs == 0 then S.err = ITEM[c].name .. ' is not in your export for this day'; redraw(); return end
  local mem = info.alts[1]
  if S.variant == 1 and mem then
    for _, v in ipairs(vs) do
      if info.labels[c .. v] == mem or (v == 1 and c:match('[IRLTSNVC]') and not info.labels[c .. v]) then
        open_variant(c, v); return
      end
    end
  end
  local cands = {}
  for _, v in ipairs(vs) do
    if not (mem and info.labels[c .. v] == mem) then cands[#cands + 1] = v end
  end
  if #cands == 0 then cands = { vs[1] } end
  if #cands == 1 then open_variant(c, cands[1]); return end
  S.choose = { c = c, list = cands, sel = 1 }
  S.screen = 'choose'
  S.err = nil
  redraw()
end

local function go_home()
  S.screen = 'home'
  S.pages, S.toc = nil, nil
  collect()
  note_place(true)
  redraw()
end

local function set_day(ymd)
  S.ymd = ymd
  S.variant = 0
  S.screen = 'home'
  S.err = nil
  trim_cal(tonumber(ymd:sub(1, 4)))
  redraw()
end

local function turn(n)
  if not S.pages then return end
  local p = S.page + n
  if p < 1 then return end
  if p > #S.pages then
    -- past the end: back to the day with the next entry highlighted
    S.sel = math.min(NSEL, ITEM[S.item].i + 1)
    go_home()
    return
  end
  S.page = p
  S.turns = S.turns + 1
  note_place(S.turns % 5 == 0)
  redraw()
end

-- re-lay the open item after a setting changed, keeping the place
local function relayout()
  if not S.item then return end
  local keep = S.page / math.max(1, #S.pages)
  local back = S.screen
  open_variant(S.item, S.open_v, 1)
  if S.pages then S.page = math.max(1, math.min(#S.pages, math.floor(keep * #S.pages + 0.5))) end
  if back ~= 'read' and S.pages then S.screen = back end
  redraw()
end

local function cycle_size()
  S.size = S.size % #SIZES + 1
  save_prefs()
  relayout()
end

local function set_lock(on)
  local L = ctx.lockscreen
  S.lock = on
  if L then
    if on then pcall(function() L:set_default() end) else pcall(function() L:clear_default() end) end
  end
  save_prefs()
end

-- the current section of the open item
local function current_section()
  local k = 1
  for i, t in ipairs(S.toc or {}) do if t.page <= S.page then k = i end end
  return k
end

local function open_toc()
  if not S.toc or #S.toc == 0 then return end
  S.toc_sel = current_section()
  S.toc_top = ((S.toc_sel - 1) // 12) * 12 + 1
  S.screen = 'toc'
  redraw()
end

local function open_month(ymd)
  local y, m = split_ymd(ymd)
  S.mon_y, S.mon_m, S.mon_sel = y, m, ymd
  S.screen = 'month'
  redraw()
end

-- the last day with data (31 December of the latest year present), or nil
local function data_end_notice()
  local _, _, t = liturgical_now()
  if not t then return nil end
  local have_this = sexists('cal' .. t.year .. '.txt')
  local have_next = sexists('cal' .. (t.year + 1) .. '.txt')
  if have_this and not have_next and t.month == 12 then
    return 'Your Universalis data ends 31 Dec ' .. t.year .. '. Add ' .. (t.year + 1) .. ' soon.'
  end
  if not have_this and sexists('cal' .. (t.year - 1) .. '.txt') then
    return 'No data for ' .. t.year .. '. Export it from Universalis.'
  end
  return nil
end

------------------------------------------------------------------ drawing: home

local ROW_TOP, ROW_H = 222, 48
local STRIP_Y, STRIP_H = 616, 46
local NOTE_Y = 676
local BTN_Y, BTN_H = H - 70, 48
local CHOICE_Y = nil

local function bottom_buttons()
  local small = 64
  local big = (W - 2 * M - 2 * small - 30) // 2
  return {
    { x = M, w = small, label = '<' },
    { x = M + small + 10, w = big, label = 'Today' },
    { x = M + small + big + 20, w = big, label = 'Calendar' },
    { x = W - M - small, w = small, label = '>' },
  }
end

local function strip_buttons()
  local bw = (W - 2 * M - 30) // 4
  local out = {}
  for k, e in ipairs(EXTRAS) do out[k] = { x = M + (k - 1) * (bw + 10), w = bw, e = e } end
  return out
end

local function draw_home(g)
  local lday, lcur = liturgical_now()
  local info = day_info(S.ymd)
  local fl, fm, fs = font(28), font(24), font(20)
  -- top line: Today, Settings
  if lday == S.ymd then text(g, M, 10, 'Today', fs)
  elseif lday and S.ymd == add_days(lday, 1) then text(g, M, 10, 'Tomorrow', fs)
  elseif lday and S.ymd == add_days(lday, -1) then text(g, M, 10, 'Yesterday', fs) end
  text(g, W - M - width(fs, 'Settings'), 10, 'Settings', fs)
  text(g, M, 36, day_label(S.ymd, true), fl)
  g:rect(M, 72, W - 2 * M, 2, { fill = true })
  local y = 84
  CHOICE_Y = nil
  if info then
    local alt = info.alts[1]
    if not alt then
      local ls = wrap(info.title, fm, W - 2 * M)
      for k = 1, math.min(#ls, 3) do text(g, M, y, ls[k], fm); y = y + lh(fm) end
      if info.rank ~= '' then text(g, M, y + 4, info.rank, fs) end
    else
      -- a choice of celebration: a filled box marks the one used
      CHOICE_Y = {}
      for v, t in ipairs({ info.title, alt }) do
        CHOICE_Y[v] = y
        g:rect(M, y + 4, 14, 14, (S.variant == v - 1) and { fill = true } or nil)
        local ls = wrap(t, fs, W - 2 * M - 26)
        for k = 1, math.min(#ls, 2) do text(g, M + 26, y, ls[k], fs); y = y + lh(fs) end
        y = y + 8
      end
      CHOICE_Y[3] = y
      text(g, M + 26, y, 'Optional memorial: tap to choose', fs)
    end
  else
    text(g, M, y, 'No data for this day.', fm)
    text(g, M, y + 30, 'Copy cal' .. S.ymd:sub(1, 4) .. '.txt and its .bin files', fs)
    text(g, M, y + 52, 'into XTApps/office/files on the SD card.', fs)
  end
  -- the Hours
  local cur = (lday == S.ymd) and lcur or nil
  for i, h in ipairs(HOURS) do
    local ry = ROW_TOP + (i - 1) * ROW_H
    rule(g, M, ry, W - 2 * M)
    text(g, M + 16, ry + 12, h.name, fm)
    local tag = (h.short ~= h.name) and h.short or ''
    if cur == h.c then tag = 'Now  ' .. tag end
    text(g, W - M - 8 - width(fs, tag), ry + 15, tag, fs)
    if cur == h.c then g:rect(M, ry + 7, 6, ROW_H - 14, { fill = true }) end
    if i == S.sel then g:rect(M + 2, ry + 3, W - 2 * M - 4, ROW_H - 6) end
  end
  rule(g, M, ROW_TOP + #HOURS * ROW_H, W - 2 * M)
  -- Mass and more
  for k, b in ipairs(strip_buttons()) do
    g:round_rect(b.x, STRIP_Y, b.w, STRIP_H, 8)
    ctext(g, b.x + b.w // 2, STRIP_Y + 13, b.e.btn, fs)
    if S.sel == #HOURS + k then frame(g, b.x - 3, STRIP_Y - 3, b.w + 6, STRIP_H + 6) end
  end
  -- notice line
  local note = S.err or data_end_notice()
  if note then
    local ls = wrap(note, fs, W - 2 * M)
    for k = 1, math.min(#ls, 2) do text(g, M, NOTE_Y + (k - 1) * 20, ls[k], fs) end
  end
  -- day buttons
  for _, b in ipairs(bottom_buttons()) do
    g:round_rect(b.x, BTN_Y, b.w, BTN_H, 8)
    ctext(g, b.x + b.w // 2, BTN_Y + 12, b.label, fm)
  end
end

------------------------------------------------------------------ drawing: reader

local function draw_read(g)
  local e = ITEM[S.item]
  local fl, fs = font(28), font(20)
  local info = day_info(S.ymd)
  local date = day_label(S.ymd, false)
  text(g, M, 8, fit(e.name, fl, W - 2 * M - width(fs, date) - 16), fl)
  text(g, W - M - width(fs, date), 14, date, fs)
  local sub = info and info.title or ''
  if info and (S.open_v or 0) > 0 then sub = variant_label(info, S.item, S.open_v)
  elseif info and info.labels[S.item .. '0'] then sub = info.labels[S.item .. '0'] end
  text(g, M, 42, fit(sub, fs, W - 2 * M), fs)
  g:rect(0, HEAD_H, W, 2, { fill = true })
  for line in ((S.pages[S.page] or '') .. '\n'):gmatch('([^\n]*)\n') do
    local ry = line:match('^%- (%d+)$')
    if ry then
      g:line(W // 2 - 40, tonumber(ry), W // 2 + 40, tonumber(ry))
    else
      local x, y, k, str = line:match('^(-?%d+) (%d+) (%d+) (.*)$')
      if x then text(g, tonumber(x), tonumber(y), str, S.page_fonts[tonumber(k)]) end
    end
  end
  local fy = H - FOOT_H
  rule(g, M, fy, W - 2 * M)
  if S.toc and #S.toc > 0 then text(g, M, fy + 12, 'Sections', fs) end
  ctext(g, W // 2, fy + 12, 'Aa', fs)
  local pg = S.page .. ' / ' .. #S.pages
  text(g, W - M - width(fs, pg), fy + 12, pg, fs)
end

------------------------------------------------------------------ drawing: sections

local TOC_TOP, TOC_H, TOC_N = 84, 50, 12

local function draw_toc(g)
  local fl, fm, fs = font(28), font(24), font(20)
  text(g, M, 8, 'Sections', fl)
  text(g, M, 42, fit(ITEM[S.item].name .. ', ' .. day_label(S.ymd, false), fs, W - 2 * M), fs)
  g:rect(0, HEAD_H, W, 2, { fill = true })
  local cur = current_section()
  for k = 0, TOC_N - 1 do
    local i = S.toc_top + k
    local t = S.toc[i]
    if not t then break end
    local ry = TOC_TOP + k * TOC_H
    local pg = tostring(t.page)
    text(g, M + 16, ry + 14, fit(t.label, fs, W - 2 * M - 40 - width(fs, pg)), fs)
    text(g, W - M - 8 - width(fs, pg), ry + 14, pg, fs)
    rule(g, M, ry + TOC_H, W - 2 * M)
    if i == cur then g:rect(M, ry + 8, 6, TOC_H - 16, { fill = true }) end
    if i == S.toc_sel then g:rect(M + 2, ry + 3, W - 2 * M - 4, TOC_H - 6) end
  end
  local labels = { 'Up', 'Back', 'Down' }
  local bw = (W - 2 * M - 20) // 3
  for k = 1, 3 do
    local bx = M + (k - 1) * (bw + 10)
    if k == 2 or #S.toc > TOC_N then
      g:round_rect(bx, BTN_Y, bw, BTN_H, 8)
      ctext(g, bx + bw // 2, BTN_Y + 12, labels[k], fm)
    end
  end
end

------------------------------------------------------------------ drawing: choose

local CH_TOP, CH_H = 140, 64

local function draw_choose(g)
  local info = day_info(S.ymd)
  local fl, fm, fs = font(28), font(24), font(20)
  local e = ITEM[S.choose.c]
  text(g, M, 8, e.name, fl)
  text(g, M, 42, day_label(S.ymd, true), fs)
  g:rect(0, HEAD_H, W, 2, { fill = true })
  text(g, M, 96, 'This day has more than one choice:', fs)
  for k, v in ipairs(S.choose.list) do
    local ry = CH_TOP + (k - 1) * (CH_H + 12)
    g:round_rect(M, ry, W - 2 * M, CH_H, 8)
    local ls = wrap(variant_label(info, S.choose.c, v), fm, W - 2 * M - 32)
    for j = 1, math.min(2, #ls) do text(g, M + 16, ry + (#ls == 1 and 20 or 8) + (j - 1) * 24, ls[j], fm) end
    if k == S.choose.sel then frame(g, M - 3, ry - 3, W - 2 * M + 6, CH_H + 6) end
  end
  local bw = (W - 2 * M - 20) // 3
  g:round_rect(M + bw + 10, BTN_Y, bw, BTN_H, 8)
  ctext(g, M + bw + 10 + bw // 2, BTN_Y + 12, 'Back', fm)
end

------------------------------------------------------------------ drawing: month

local GRID_X, GRID_Y, CELL_W, CELL_H = 16, 124, 64, 64

local function month_cells()
  local first = ymd_of(S.mon_y, S.mon_m, 1)
  local lead = weekday(first) - 1
  return lead, month_len(S.mon_y, S.mon_m)
end

local function draw_month(g)
  local fl, fm, fs = font(28), font(24), font(20)
  local lday = liturgical_now()
  local title = MONTHS[S.mon_m] .. ' ' .. S.mon_y
  g:round_rect(M, 14, 56, 48, 8); ctext(g, M + 28, 26, '<', fm)
  g:round_rect(W - M - 56, 14, 56, 48, 8); ctext(g, W - M - 28, 26, '>', fm)
  ctext(g, W // 2, 22, title, fl)
  g:rect(M, 72, W - 2 * M, 2, { fill = true })
  for k, d in ipairs({ 'S', 'M', 'T', 'W', 'T', 'F', 'S' }) do
    ctext(g, GRID_X + (k - 1) * CELL_W + CELL_W // 2, 90, d, fs)
  end
  local lead, n = month_cells()
  load_year(S.mon_y)
  for d = 1, n do
    local idx = lead + d - 1
    local cx = GRID_X + (idx % 7) * CELL_W
    local cy = GRID_Y + (idx // 7) * CELL_H
    local ymd = ymd_of(S.mon_y, S.mon_m, d)
    ctext(g, cx + CELL_W // 2, cy + 10, tostring(d), fm)
    local info = day_info(ymd)
    if info then
      if info.rank == 'Solemnity' then g:rect(cx + CELL_W // 2 - 5, cy + 42, 10, 10, { fill = true })
      elseif info.rank == 'Feast' then g:rect(cx + CELL_W // 2 - 5, cy + 42, 10, 10) end
    end
    if ymd == lday then frame(g, cx + 2, cy + 2, CELL_W - 4, CELL_H - 4, true)
    elseif ymd == S.ymd then frame(g, cx + 2, cy + 2, CELL_W - 4, CELL_H - 4) end
    if ymd == S.mon_sel then g:rect(cx + 14, cy + 36, CELL_W - 28, 3, { fill = true }) end
  end
  local ly = GRID_Y + 6 * CELL_H + 8
  g:rect(M, ly + 5, 10, 10, { fill = true }); text(g, M + 16, ly, 'Solemnity', fs)
  g:rect(M + 140, ly + 5, 10, 10); text(g, M + 156, ly, 'Feast', fs)
  frame(g, M + 250, ly - 2, 24, 24, true); text(g, M + 282, ly, 'Today', fs)
  -- the day under the cursor
  local info = day_info(S.mon_sel)
  local y = ly + 40
  rule(g, M, y - 8, W - 2 * M)
  text(g, M, y, day_label(S.mon_sel, true), fs)
  if info then
    local ls = wrap(info.title .. (info.rank ~= '' and (' (' .. info.rank .. ')') or ''), fm, W - 2 * M)
    for k = 1, math.min(2, #ls) do text(g, M, y + 24 + (k - 1) * 26, ls[k], fm) end
  else
    text(g, M, y + 24, 'No data for this day', fm)
  end
  local bw = (W - 2 * M - 20) // 3
  local labels = { 'Back', 'Today', 'Open' }
  for k = 1, 3 do
    local bx = M + (k - 1) * (bw + 10)
    g:round_rect(bx, BTN_Y, bw, BTN_H, 8)
    ctext(g, bx + bw // 2, BTN_Y + 12, labels[k], fm)
  end
end

------------------------------------------------------------------ drawing: settings

local SET_TOP, SET_H = 84, 92
local SETTINGS = {
  { label = 'Text size',    opts = { 'Small', 'Medium', 'Large' },   key = 'size' },
  { label = 'Versicles',    opts = { '℣ ℟', 'V. R.' },               key = 'vr' },
  { label = 'Line spacing', opts = { 'Compact', 'Normal', 'Relaxed' }, key = 'spacing' },
  { label = 'Margins',      opts = { 'Narrow', 'Normal', 'Wide' },   key = 'margin' },
  { label = 'Lock screen',  opts = { 'Off', "Today's Hours" },        key = 'lock' },
}
local function setting_value(s)
  if s.key == 'vr' then return S.vr and 1 or 2 end
  if s.key == 'lock' then return S.lock and 2 or 1 end
  return S[s.key]
end

local function draw_settings(g)
  local fl, fm, fs = font(28), font(24), font(20)
  text(g, M, 18, 'Settings', fl)
  g:rect(0, HEAD_H, W, 2, { fill = true })
  for i, s in ipairs(SETTINGS) do
    local y = SET_TOP + (i - 1) * SET_H
    text(g, M, y, s.label, fs)
    if s.key == 'lock' and not ctx.lockscreen then
      text(g, M + 140, y, '(needs newer firmware)', fs)
    end
    local n = #s.opts
    local bw = (W - 2 * M - (n - 1) * 8) // n
    local cur = setting_value(s)
    for k, o in ipairs(s.opts) do
      local bx = M + (k - 1) * (bw + 8)
      local by = y + 26
      if k == cur then
        frame(g, bx, by, bw, 48, true)
        g:rect(bx + 10, by + 40, bw - 20, 3, { fill = true })
      else
        g:round_rect(bx, by, bw, 48, 8)
      end
      ctext(g, bx + bw // 2, by + 12, o, fm)
    end
    if i == S.set_sel then g:rect(M - 10, y + 30, 4, 40, { fill = true }) end
  end
  -- preview of the reader text
  local py = SET_TOP + #SETTINGS * SET_H + 4
  rule(g, M, py, W - 2 * M)
  local f = font(SIZES[S.size])
  local RM = MARGINS[S.margin]
  local yy = py + 10
  local sample = S.vr and { '℣. O God, come to my aid.', '℟. O Lord, make haste to help me.' }
    or { 'V. O God, come to my aid.', 'R. O Lord, make haste to help me.' }
  for _, s in ipairs(sample) do
    for k, l in ipairs(wrap(s, f, W - 2 * RM, W - 2 * RM - 28)) do
      if yy + lh(f) < BTN_Y - 6 then text(g, RM + (k > 1 and 28 or 0), yy, l, f) end
      yy = yy + lh(f) + SPACING[S.spacing]
    end
  end
  local bw = (W - 2 * M - 20) // 3
  g:round_rect(M + bw + 10, BTN_Y, bw, BTN_H, 8)
  ctext(g, M + bw + 10 + bw // 2, BTN_Y + 12, 'Done', fm)
end

function on_draw(c, g)
  local ok, err = pcall(function()
    g:clear(0)
    local sc = S.screen
    if sc == 'read' and S.pages then draw_read(g)
    elseif sc == 'toc' and S.toc then draw_toc(g)
    elseif sc == 'choose' and S.choose then draw_choose(g)
    elseif sc == 'month' then draw_month(g)
    elseif sc == 'settings' then draw_settings(g)
    else draw_home(g) end
  end)
  if not ok then log(err); pcall(function() g:text(20, 400, 'Error: ' .. tostring(err):sub(1, 60)) end) end
end

------------------------------------------------------------------ input

local KEY_GAP_MS = 250
local last_key, last_ms = nil, nil
local function key_ok(ev)
  local ph = ev.phase or ev.action or ev.state
  if ph == 'up' or ph == 'release' or ph == 'released' or ev.pressed == false then return false end
  local t = tonumber(ev.time_ms)
  if t and last_key == ev.key and last_ms and t - last_ms >= 0 and t - last_ms < KEY_GAP_MS then return false end
  last_key, last_ms = ev.key, t
  return true
end

local function today_ymd() return liturgical_now() or S.ymd end

local function in_btn(x, y, b, by, bh) return x >= b.x and x < b.x + b.w and y >= by and y < by + bh end
local function third(x) return (x - M) // ((W - 2 * M - 20) // 3 + 10) + 1 end

local function open_settings() S.set_sel = 1; S.back_from_settings = S.screen; S.screen = 'settings'; redraw() end

local function home_open_sel()
  if S.sel <= #HOURS then open_item(HOURS[S.sel].c) else open_item(EXTRAS[S.sel - #HOURS].c) end
end

local function home_tap(x, y)
  if y < 34 and x > W - M - 120 then open_settings(); return end
  if y >= BTN_Y and y < BTN_Y + BTN_H then
    for k, b in ipairs(bottom_buttons()) do
      if in_btn(x, y, b, BTN_Y, BTN_H) then
        if k == 1 then set_day(add_days(S.ymd, -1))
        elseif k == 2 then set_day(today_ymd())
        elseif k == 3 then open_month(S.ymd)
        else set_day(add_days(S.ymd, 1)) end
      end
    end
    return
  end
  if y >= STRIP_Y and y < STRIP_Y + STRIP_H then
    for k, b in ipairs(strip_buttons()) do
      if in_btn(x, y, b, STRIP_Y, STRIP_H) then S.sel = #HOURS + k; open_item(b.e.c) end
    end
    return
  end
  if y >= ROW_TOP and y < ROW_TOP + #HOURS * ROW_H then
    S.sel = (y - ROW_TOP) // ROW_H + 1
    open_item(HOURS[S.sel].c)
    return
  end
  if y >= 80 and y < ROW_TOP and CHOICE_Y then
    S.variant = (y >= CHOICE_Y[2]) and 1 or 0
    redraw()
  end
end

local function read_tap(x, y, gesture)
  if gesture == 'swipe_left' then turn(1); return end
  if gesture == 'swipe_right' then turn(-1); return end
  if y < HEAD_H + 4 then go_home(); return end
  if y > H - FOOT_H then
    if x < M + 110 then open_toc()
    elseif x > W // 2 - 50 and x < W // 2 + 50 then cycle_size() end
    return
  end
  if x < W // 3 then turn(-1) else turn(1) end
end

local function toc_jump(i)
  local t = S.toc[i]
  if t then S.page = t.page; S.screen = 'read'; note_place(true); redraw() end
end

local function toc_scroll(n)
  local top = S.toc_top + n * TOC_N
  if top >= 1 and top <= #S.toc then S.toc_top = top; S.toc_sel = top; redraw() end
end

local function toc_tap(x, y)
  if y < HEAD_H + 4 then S.screen = 'read'; redraw(); return end
  if y >= BTN_Y and y < BTN_Y + BTN_H then
    local k = third(x)
    if k == 1 then toc_scroll(-1) elseif k == 2 then S.screen = 'read'; redraw() elseif k == 3 then toc_scroll(1) end
    return
  end
  if y >= TOC_TOP and y < TOC_TOP + TOC_N * TOC_H then toc_jump(S.toc_top + (y - TOC_TOP) // TOC_H) end
end

local function choose_tap(x, y)
  if y >= BTN_Y and y < BTN_Y + BTN_H then S.screen = 'home'; redraw(); return end
  for k, v in ipairs(S.choose.list) do
    local ry = CH_TOP + (k - 1) * (CH_H + 12)
    if y >= ry and y < ry + CH_H then open_variant(S.choose.c, v); return end
  end
end

local function month_shift(n)
  local m, y = S.mon_m + n, S.mon_y
  if m < 1 then m, y = 12, y - 1 elseif m > 12 then m, y = 1, y + 1 end
  S.mon_y, S.mon_m = y, m
  S.mon_sel = ymd_of(y, m, 1)
  trim_cal(y)
  redraw()
end

local function month_tap(x, y)
  if y < 70 then
    if x < M + 70 then month_shift(-1) elseif x > W - M - 70 then month_shift(1) end
    return
  end
  if y >= BTN_Y and y < BTN_Y + BTN_H then
    local k = third(x)
    if k == 1 then S.screen = 'home'; redraw()
    elseif k == 2 then set_day(today_ymd())
    elseif k == 3 then set_day(S.mon_sel) end
    return
  end
  if y >= GRID_Y and y < GRID_Y + 6 * CELL_H and x >= GRID_X and x < GRID_X + 7 * CELL_W then
    local idx = ((y - GRID_Y) // CELL_H) * 7 + (x - GRID_X) // CELL_W
    local lead, n = month_cells()
    local d = idx - lead + 1
    if d >= 1 and d <= n then set_day(ymd_of(S.mon_y, S.mon_m, d)) end
  end
end

local function change_setting(i, k)
  local s = SETTINGS[i]
  local layout_before = S.size .. S.spacing .. S.margin .. tostring(S.vr)
  if s.key == 'vr' then S.vr = (k == 1)
  elseif s.key == 'lock' then set_lock(k == 2)
  else S[s.key] = k end
  save_prefs()
  if S.size .. S.spacing .. S.margin .. tostring(S.vr) ~= layout_before then S.relayout = true end
  redraw()
end

local function close_settings()
  S.screen = S.back_from_settings or 'home'
  if S.relayout and S.item and S.screen ~= 'home' then S.relayout = false; relayout(); return end
  S.relayout = false
  if S.screen ~= 'home' and not S.pages then S.screen = 'home' end
  redraw()
end

local function settings_tap(x, y)
  if y >= BTN_Y and y < BTN_Y + BTN_H then close_settings(); return end
  for i, s in ipairs(SETTINGS) do
    local by = SET_TOP + (i - 1) * SET_H + 26
    if y >= by and y < by + 48 then
      local n = #s.opts
      local bw = (W - 2 * M - (n - 1) * 8) // n
      local k = math.max(1, math.min(n, (x - M) // (bw + 8) + 1))
      S.set_sel = i
      change_setting(i, k)
      return
    end
  end
end

local function on_key(k)
  local sc = S.screen
  if sc == 'read' then
    if k == 'down' or k == 'right' or k == 'ok' then turn(1)
    elseif k == 'up' or k == 'left' then turn(-1)
    elseif k == 'back' then go_home() end
  elseif sc == 'toc' then
    if k == 'down' then S.toc_sel = math.min(#S.toc, S.toc_sel + 1)
    elseif k == 'up' then S.toc_sel = math.max(1, S.toc_sel - 1)
    elseif k == 'ok' then toc_jump(S.toc_sel); return
    elseif k == 'back' or k == 'left' then S.screen = 'read'; redraw(); return end
    S.toc_top = ((S.toc_sel - 1) // TOC_N) * TOC_N + 1
    redraw()
  elseif sc == 'choose' then
    local n = #S.choose.list
    if k == 'down' then S.choose.sel = math.min(n, S.choose.sel + 1); redraw()
    elseif k == 'up' then S.choose.sel = math.max(1, S.choose.sel - 1); redraw()
    elseif k == 'ok' then open_variant(S.choose.c, S.choose.list[S.choose.sel])
    elseif k == 'back' then S.screen = 'home'; redraw() end
  elseif sc == 'month' then
    local step = ({ left = -1, right = 1, up = -7, down = 7 })[k]
    if step then
      local nsel = add_days(S.mon_sel, step)
      local y, m = split_ymd(nsel)
      if y ~= S.mon_y or m ~= S.mon_m then S.mon_y, S.mon_m = y, m; trim_cal(y) end
      S.mon_sel = nsel
      redraw()
    elseif k == 'ok' then set_day(S.mon_sel)
    elseif k == 'back' then S.screen = 'home'; redraw() end
  elseif sc == 'settings' then
    local s = SETTINGS[S.set_sel]
    if k == 'down' then S.set_sel = math.min(#SETTINGS, S.set_sel + 1); redraw()
    elseif k == 'up' then S.set_sel = math.max(1, S.set_sel - 1); redraw()
    elseif k == 'left' or k == 'right' then
      local v = setting_value(s) + (k == 'right' and 1 or -1)
      if v >= 1 and v <= #s.opts then change_setting(S.set_sel, v) end
    elseif k == 'ok' or k == 'back' then close_settings() end
  else
    if k == 'down' then S.sel = math.min(NSEL, S.sel + 1); redraw()
    elseif k == 'up' and S.sel == 1 then
      local info = day_info(S.ymd)
      if info and info.alts[1] then S.variant = 1 - S.variant; redraw() end
    elseif k == 'up' then S.sel = math.max(1, S.sel - 1); redraw()
    elseif k == 'left' then set_day(add_days(S.ymd, -1))
    elseif k == 'right' then set_day(add_days(S.ymd, 1))
    elseif k == 'ok' then home_open_sel() end
  end
end

function on_input(c, ev)
  -- the back key: the app keeps it except on the home screen, where it leaves the app
  local handled = true
  if ev.type == 'key' and ev.key == 'back' then
    local ph = ev.phase or ev.action or ev.state
    if ph == 'up' or ph == 'release' or ph == 'released' or ev.pressed == false then
      handled = S.back_kept == true          -- the release goes with its press
    else
      handled = S.screen ~= 'home'
      S.back_kept = handled
    end
  end
  local ok, err = pcall(function()
    if ev.type == 'key' then
      if key_ok(ev) then on_key(ev.key) end
      return
    end
    if ev.type ~= 'pointer' then return end
    if ev.phase and ev.phase ~= 'up' then return end
    local x, y, gs = ev.x or 0, ev.y or 0, ev.gesture
    local sc = S.screen
    if sc == 'read' then read_tap(x, y, gs)
    elseif sc == 'toc' then
      if gs == 'swipe_left' then toc_scroll(1) elseif gs == 'swipe_right' then toc_scroll(-1) else toc_tap(x, y) end
    elseif sc == 'choose' then choose_tap(x, y)
    elseif sc == 'month' then
      if gs == 'swipe_left' then month_shift(1) elseif gs == 'swipe_right' then month_shift(-1) else month_tap(x, y) end
    elseif sc == 'settings' then settings_tap(x, y)
    else
      if gs == 'swipe_left' then set_day(add_days(S.ymd, 1))
      elseif gs == 'swipe_right' then set_day(add_days(S.ymd, -1))
      else home_tap(x, y) end
    end
  end)
  if not ok then log(err) end
  return handled
end

------------------------------------------------------------------ lifecycle

local function load_prefs()
  local p = sread('prefs.txt') or ''
  local function num(k, n, d) local v = tonumber(p:match(k .. '=(%d+)')); return (v and v >= 1 and v <= n) and v or d end
  S.size = num('size', #SIZES, S.size)
  S.spacing = num('spacing', #SPACING, S.spacing)
  S.margin = num('margin', #MARGINS, S.margin)
  S.vr = not p:match('vr=0')
  S.lock = p:match('lock=1') ~= nil
end

local function show_today()
  local lday, c = liturgical_now()
  S.ymd = lday or S.ymd or '20261201'
  S.variant = 0
  if c then S.sel = ITEM[c].i end
  S.screen = 'home'
  S.pages, S.toc = nil, nil
  S.lday = lday
end

-- fresh start: reopen the item you were reading if it is still the same liturgical day
local function start()
  load_prefs()
  local lday = liturgical_now()
  local st = type(ctx.state) == 'table' and ctx.state or {}
  if st.screen == 'read' and st.lday and st.lday ~= '' and st.lday == lday and st.ymd and st.item and ITEM[st.item] then
    S.ymd = st.ymd
    S.variant = tonumber(st.memorial) or 0
    local page = tonumber(st.page) or 1
    open_variant(st.item, tonumber(st.v) or 0, page)
    if S.screen == 'read' then return end
  end
  show_today()
  redraw()
end

-- back from sleep or another app: keep the screen if it is still the same liturgical day
local function resume()
  local lday = liturgical_now()
  local ok_screen = S.screen == 'home' or S.screen == 'month' or S.screen == 'settings'
    or S.screen == 'choose' or S.pages ~= nil
  if lday and S.lday == lday and ok_screen then redraw(); return end
  show_today()
  redraw()
end

function on_load(c) local ok, err = pcall(start); if not ok then log(err) end end
function on_resume(c) local ok, err = pcall(resume); if not ok then log(err) end end
function on_leave() pcall(function() note_place(true) end) end
