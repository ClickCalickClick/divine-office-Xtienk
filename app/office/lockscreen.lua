-- Lock screen for Divine Office: today's date and celebration, the Hour for now and the next one.
-- Turned on in the app's Settings (Lock screen: Today's Hours). The host wakes it every
-- interval; it asks to be woken at the next full hour, when the Hour may change.

local W, H, M = 480, 800, 28
local WEEKDAYS = { 'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday' }
local MONTHS = { 'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
  'September', 'October', 'November', 'December' }
-- the Hours with their times (24-hour clock; Night Prayer runs past midnight to 4 am)
local HOURS = {
  { name = 'Morning Prayer',     from = 4,  to = 9 },
  { name = 'Mid-Morning Prayer', from = 9,  to = 12 },
  { name = 'Midday Prayer',      from = 12, to = 15 },
  { name = 'Afternoon Prayer',   from = 15, to = 17 },
  { name = 'Evening Prayer',     from = 17, to = 21 },
  { name = 'Night Prayer',       from = 21, to = 28 },
}

local function f(name) return ctx.fonts[name] end
local function lh(fo) local ok, h = pcall(function() return fo.line_height end); return (ok and tonumber(h)) or 24 end
local function width(fo, s)
  local ok, w = pcall(function() return fo:measure(s) end)
  return (ok and type(w) == 'number') and w or #s * 11
end
local function wrap(s, fo, maxw)
  local out, line = {}, ''
  for word in s:gmatch('[^ ]+') do
    local cand = line == '' and word or (line .. ' ' .. word)
    if line ~= '' and width(fo, cand) > maxw then out[#out + 1] = line; line = word else line = cand end
  end
  if line ~= '' then out[#out + 1] = line end
  return out
end

local function days_from_civil(y, m, d)
  if m <= 2 then y = y - 1 end
  local era = (y >= 0 and y or y - 399) // 400
  local yoe = y - era * 400
  local mp = (m + 9) % 12
  local doy = (153 * mp + 2) // 5 + d - 1
  return era * 146097 + yoe * 365 + yoe // 4 - yoe // 100 + doy - 719468
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

local function clock12(h)
  h = h % 24
  local ap = h < 12 and 'am' or 'pm'
  local hh = h % 12
  if hh == 0 then hh = 12 end
  return hh .. ' ' .. ap
end

-- the day's line from cal<year>.txt: title and rank
local function day_line(y, m, d)
  local ok, s = pcall(function() return ctx.storage:read('cal' .. y .. '.txt') end)
  if not ok or type(s) ~= 'string' then return nil end
  local key = string.format('%04d%02d%02d|', y, m, d)
  local a = s:find(key, 1, true)
  if not a then return nil end
  local e = s:find('\n', a, true) or #s + 1
  local line = s:sub(a, e - 1)
  s = nil
  local _, title, rank = line:match('^(%d+)|([^|]*)|([^|]*)|')
  if rank == 'or' then rank = '' end
  return title, rank
end

local function has_year(y)
  local ok, e = pcall(function() return ctx.storage:exists('cal' .. y .. '.txt') end)
  return ok and e == true
end

function on_load(c) end
function on_tick() end
function on_input() return false end

function on_draw(c, g)
  if not g then return end
  local ok, err = pcall(function()
    g:clear(0)
    local t = ctx.clock:now()
    local hour, minute = tonumber(t.hour) or 12, tonumber(t.minute) or 0
    local y, m, d = tonumber(t.year), tonumber(t.month), tonumber(t.day)
    -- the liturgical day: before 4 am it is still last night
    local dn = days_from_civil(y, m, d)
    local lh24 = hour
    if hour < 4 then dn = dn - 1; lh24 = hour + 24 end
    local ly, lm, ld = civil_from_days(dn)
    local wd = WEEKDAYS[(dn + 4) % 7 + 1]

    local fl, fm, fs = f('large'), f('medium'), f('small')
    g:text(M, 60, wd, { font = fl })
    g:text(M, 60 + lh(fl) + 4, ld .. ' ' .. MONTHS[lm] .. ' ' .. ly, { font = fm })
    -- the hour, and the Hour it falls in (redrawn every hour, so no minutes)
    local cur = 1
    for i, h in ipairs(HOURS) do
      if lh24 >= h.from and lh24 < h.to then cur = i end
    end
    local ty = 60 + lh(fl) + 4 + lh(fm) + 4
    g:text(M, ty, clock12(hour) .. ', ' .. HOURS[cur].name, { font = fm })
    local yy = ty + lh(fm) + 22
    g:rect(M, yy, W - 2 * M, 2, { fill = true })
    yy = yy + 18

    local title, rank = day_line(ly, lm, ld)
    if title then
      for k, l in ipairs(wrap(title, fl, W - 2 * M)) do
        if k <= 3 then g:text(M, yy, l, { font = fl }); yy = yy + lh(fl) + 2 end
      end
      if rank and rank ~= '' then g:text(M, yy + 4, rank, { font = fm }); yy = yy + lh(fm) + 4 end
    else
      g:text(M, yy, 'No Universalis data for today', { font = fm }); yy = yy + lh(fm)
    end

    -- now and next
    local nxt = cur % #HOURS + 1
    local by = math.max(yy + 40, 380)
    -- rounded corners where the firmware takes them, else a plain box (round_rect with a bare
    -- radius number fails on some firmware, as it did in the app)
    if not pcall(function() g:round_rect(M, by, W - 2 * M, 130, { radius = 12 }) end) then
      if not pcall(function() g:round_rect(M, by, W - 2 * M, 130, 12) end) then
        g:rect(M, by, W - 2 * M, 130)
      end
    end
    g:text(M + 20, by + 16, 'Now', { font = fs })
    g:text(M + 20, by + 44, HOURS[cur].name, { font = fl })
    g:text(M + 20, by + 44 + lh(fl) + 6,
      clock12(HOURS[cur].from) .. ' to ' .. clock12(HOURS[cur].to), { font = fs })
    local ny = by + 160
    g:text(M + 20, ny, 'Next', { font = fs })
    g:text(M + 20, ny + 26, HOURS[nxt].name .. ', from ' .. clock12(HOURS[nxt].from), { font = fm })

    -- end of data
    if m == 12 and has_year(y) and not has_year(y + 1) then
      g:text(M, H - 70, 'Universalis data ends 31 Dec ' .. y .. '.', { font = fs })
      g:text(M, H - 48, 'Export ' .. (y + 1) .. ' and copy it to the SD card.', { font = fs })
    end

    -- wake at the next full hour
    local secs = (60 - minute) * 60 + 30
    if secs > 3600 then secs = 3600 end
    if secs < 60 then secs = 60 end
    pcall(function() ctx.lock:set_interval(secs) end)
  end)
  if not ok then
    pcall(function()
      local msg, y = 'lock error: ' .. tostring(err), 10
      while #msg > 0 and y < 200 do g:text(10, y, msg:sub(1, 38)); msg = msg:sub(39); y = y + 24 end
    end)
  end
  pcall(function() ctx.lock:done() end)
end
