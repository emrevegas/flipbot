--[[
  FlipBot — Donation listener (ayrı Luci script)
  deposit_watch.json aktifken BOT_HOME_WORLD'de donation box dinler;
  WL/DL/BGL yatırımlarını deposit_inbox/*.json olarak yazar.
]]

local QUEUE_BASE = "C:/Users/Administrator/Desktop/flipbot/data/luci"

local BOT_HOME_WORLD = "BOTHOUSE"
local BOT_HOME_DOOR = ""

local POLL_MS = 2500

local DEPOSIT_INBOX_DIR = ""
local DEPOSIT_WATCH_FILE = ""
local hook_active = false
local hook_registered = false

local bot = getBot()
bot.auto_reconnect = true
bot.auto_accept = false
bot.auto_ban = false

function DL_log(msg)
  local line = "[FlipBot Donation] " .. tostring(msg)
  print(line)
  pcall(function() getBot():getLog():append(line) end)
end

function DL_init_paths()
  local base = QUEUE_BASE
  local ok, path = pcall(function()
    return read(base .. "/QUEUE_PATH.txt")
  end)
  if ok and path and path ~= "" then
    path = path:gsub("%s+", ""):gsub("\\", "/")
    if path:sub(-1) == "/" then path = path:sub(1, -2) end
    base = path
  end
  DEPOSIT_INBOX_DIR = base .. "/deposit_inbox"
  DEPOSIT_WATCH_FILE = base .. "/deposit_watch.json"
  DL_log("Queue path: " .. base)
end

function DL_json_escape(s)
  s = tostring(s or "")
  s = s:gsub("\\", "\\\\")
  s = s:gsub('"', '\\"')
  return s
end

function DL_go_home()
  local home = tostring(BOT_HOME_WORLD or ""):gsub("%s+", "")
  if home == "" then return end
  local dest = string.upper(home)
  if BOT_HOME_DOOR and BOT_HOME_DOOR ~= "" then
    dest = dest .. "|" .. tostring(BOT_HOME_DOOR)
  end
  if bot:isInWorld() and bot:getWorld() then
    local cur = string.upper(tostring(bot:getWorld().name or ""))
    if cur == string.upper(home) then return end
  end
  bot:enter(dest)
end

function DL_watch_active()
  local raw = read(DEPOSIT_WATCH_FILE)
  if not raw or raw == "" then return false end
  local until_ts = tonumber(raw:match('"until":(%d+)'))
  if not until_ts then return false end
  return os.time() < until_ts
end

function DL_write_inbox(growid, amount_units)
  if DEPOSIT_INBOX_DIR == "" then return end
  local fname = DEPOSIT_INBOX_DIR .. "/" .. os.time() .. "_" .. growid .. ".json"
  local json = string.format(
    '{"growid":"%s","amount_units":%d,"at":%d}',
    DL_json_escape(growid), amount_units, os.time()
  )
  write(fname, json)
  DL_log("Inbox " .. growid .. " +" .. amount_units .. " WL-units")
end

function DL_on_donation(var, netid)
  if not hook_active then return end
  local ok, v0 = pcall(function() return var:get(0):getString() end)
  if not ok then return end
  if not tostring(v0):find("OnConsoleMessage", 1, true) then return end
  local ok2, v1 = pcall(function() return var:get(1):getString() end)
  if not ok2 or not v1 then return end
  local msg = tostring(v1)
  if not msg:find("Donation Box", 1, true) then return end
  if not msg:find("places", 1, true) then return end
  local username = msg:match("`w(%S+) places")
  local item = msg:match("`2(.-)``")
  local amount = tonumber(msg:match("places `5(%d+)"))
  if not username or not amount or not item then return end
  if item == "World Lock" then
    -- WL units as-is
  elseif item == "Diamond Lock" then
    amount = amount * 100
  elseif item == "Blue Gem Lock" then
    amount = amount * 10000
  else
    return
  end
  if msg:find("CP:0_PL:4_OID:_CT:", 1, true) then return end
  if item == "World Lock" or item == "Diamond Lock" or item == "Blue Gem Lock" then
    DL_write_inbox(username, amount)
  end
end

function DL_register_hook()
  if hook_registered then return end
  pcall(function() addEvent(Event.variantlist, DL_on_donation) end)
  hook_registered = true
  DL_log("Donation hook registered")
end

function DL_tick()
  if not DL_watch_active() then
    hook_active = false
    return
  end
  DL_register_hook()
  hook_active = true
  local home = tostring(BOT_HOME_WORLD or ""):gsub("%s+", "")
  if home == "" then return end
  if bot:isInWorld() and bot:getWorld() then
    local cur = string.upper(tostring(bot:getWorld().name or ""))
    if cur == string.upper(home) then return end
  end
  DL_log("Deposit watch — going home")
  DL_go_home()
end

DL_init_paths()
DL_log("Donation listener started")

while true do
  DL_tick()
  listenEvents(2)
  sleep(POLL_MS)
end
