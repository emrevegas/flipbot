--[[
  FlipBot — Luci withdraw worker (ayrı script)
  Sadece pending/*.json kuyruğunu işler — display box drop.
  Deposit için flipbot_donation_listener.lua kullanın.
]]

local QUEUE_BASE = "C:/Users/Administrator/Desktop/flipbot/data/luci"

-- Sipariş bitince bot bu dünyaya döner (kapı varsa WORLD|DOOR)
local BOT_HOME_WORLD = "BOTHOUSE"
local BOT_HOME_DOOR = ""

local POLL_MS = 2500
local ORDER_TIMEOUT_MS = 120000
local WARP_RETRY_MS = 5000
local MAX_STACK = 200

local ITEM_WL = 242
local ITEM_DL = 1796
local ITEM_BGL = 7188

local DISPLAY_BOX_FG = 1422

local PENDING_DIR = ""
local PROCESSING_DIR = ""
local RESULTS_DIR = ""
local INDEX_FILE = ""
local ACTIVE_FILE = ""
local BALANCE_FILE = ""

local bot = getBot()
bot.auto_reconnect = true
bot.auto_accept = false
bot.auto_ban = false

local state = {
  busy = false,
  order = nil,
  order_started_ms = 0,
}

function GT_log(msg)
  local line = "[FlipBot] " .. tostring(msg)
  print(line)
  pcall(function() getBot():getLog():append(line) end)
end

function GT_init_paths()
  local base = QUEUE_BASE
  local ok, path = pcall(function()
    return read(base .. "/QUEUE_PATH.txt")
  end)
  if ok and path and path ~= "" then
    path = path:gsub("%s+", ""):gsub("\\", "/")
    if path:sub(-1) == "/" then path = path:sub(1, -2) end
    base = path
  end
  PENDING_DIR = base .. "/pending"
  PROCESSING_DIR = base .. "/processing"
  RESULTS_DIR = base .. "/results"
  INDEX_FILE = base .. "/pending_index.txt"
  ACTIVE_FILE = base .. "/processing/active.txt"
  BALANCE_FILE = base .. "/bot_balance.json"
  GT_log("Queue path: " .. base)
end

function GT_json_escape(s)
  s = tostring(s or "")
  s = s:gsub("\\", "\\\\")
  s = s:gsub('"', '\\"')
  return s
end

function GT_write_bot_balance()
  if BALANCE_FILE == "" then return end
  local wl = GT_inventory_count(ITEM_WL)
  local dl = GT_inventory_count(ITEM_DL)
  local bgl = GT_inventory_count(ITEM_BGL)
  local world = ""
  if bot:isInWorld() and bot:getWorld() then
    world = tostring(bot:getWorld().name or "")
  end
  local json = string.format(
    '{"wl":%d,"dl":%d,"bgl":%d,"at":%d,"bot":"%s","world":"%s"}',
    wl, dl, bgl, os.time(),
    GT_json_escape(bot.name),
    GT_json_escape(world)
  )
  write(BALANCE_FILE, json)
end

function GT_go_home()
  local home = tostring(BOT_HOME_WORLD or ""):gsub("%s+", "")
  if home == "" then return end
  local dest = string.upper(home)
  local door = tostring(BOT_HOME_DOOR or ""):gsub("%s+", "")
  if door ~= "" then
    dest = dest .. "|" .. door
  end
  GT_log("Going home -> " .. dest)
  GT_warp_to_world(dest)
  sleep(1500)
  GT_write_bot_balance()
end

function GT_now_ms()
  return os.time() * 1000
end

function GT_split_world(world_field)
  local w = string.upper(tostring(world_field or ""))
  local name, door = w, ""
  local pipe = w:find("|", 1, true)
  if pipe then
    name = w:sub(1, pipe - 1)
    door = w:sub(pipe + 1)
  end
  return name, door
end

function GT_order_expired()
  if state.order_started_ms <= 0 then return false end
  return (GT_now_ms() - state.order_started_ms) >= ORDER_TIMEOUT_MS
end

function GT_parse_deliveries(body)
  local deliveries = {}
  local section = body:match('"deliveries":%[(.-)%]')
  if not section or section == "" then return deliveries end
  for typ, qty, iid in section:gmatch('"item_type":"([^"]+)","quantity":(%d+),"item_id":(%d+)') do
    deliveries[#deliveries + 1] = {
      item_type = typ,
      quantity = tonumber(qty) or 1,
      item_id = tonumber(iid) or GT_item_id_for_type(typ),
    }
  end
  return deliveries
end

function GT_parse_order_json(body)
  if not body or body == "" then return nil end
  local compact = body:gsub("%s+", "")
  local id = compact:match('"id":(%d+)')
  local growid = compact:match('"growid":"([^"]+)"')
  local world = compact:match('"world_name":"([^"]+)"')
  local item_type = compact:match('"item_type":"([^"]+)"')
  local qty = compact:match('"quantity":(%d+)')
  local item_id = compact:match('"item_id":(%d+)')
  if id and growid and world then
    local order = {
      id = tonumber(id),
      growid = growid,
      world_name = world,
      item_type = item_type or "wl",
      quantity = tonumber(qty) or 1,
      item_id = tonumber(item_id) or ITEM_WL,
    }
    local deliveries = GT_parse_deliveries(compact)
    if #deliveries > 0 then
      order.deliveries = deliveries
    end
    return order
  end
  return nil
end

function GT_item_id_for_type(t)
  if t == "dl" then return ITEM_DL end
  if t == "bgl" then return ITEM_BGL end
  return ITEM_WL
end

function GT_write_result(order_id, status, reason)
  local safe = (reason or ""):gsub('"', "'")
  local json = string.format(
    '{"id":%d,"status":"%s","reason":"%s","at":%d}',
    order_id, status, safe, os.time()
  )
  write(RESULTS_DIR .. "/" .. order_id .. ".json", json)
  GT_log("Result #" .. order_id .. " " .. status)
end

function GT_read_index_ids()
  local raw = read(INDEX_FILE)
  if not raw or raw == "" then return {} end
  local ids = {}
  for line in raw:gmatch("[^\r\n]+") do
    local n = tonumber(line:match("^(%d+)"))
    if n then ids[#ids + 1] = n end
  end
  return ids
end

function GT_write_index_ids(ids)
  if #ids == 0 then
    write(INDEX_FILE, "")
    return
  end
  local lines = {}
  for i = 1, #ids do lines[i] = tostring(ids[i]) end
  write(INDEX_FILE, table.concat(lines, "\n") .. "\n")
end

function GT_load_order_file(dir, id)
  local body = read(dir .. "/" .. id .. ".json")
  if not body or body == "" then return nil end
  return GT_parse_order_json(body)
end

function GT_claim_from_processing()
  local id_str = read(ACTIVE_FILE)
  if not id_str or id_str == "" then return nil end
  local id = tonumber(id_str:match("(%d+)"))
  if not id then return nil end
  local order = GT_load_order_file(PROCESSING_DIR, id)
  if order then
    GT_log("Resume processing #" .. id .. " world=" .. order.world_name)
    return order
  end
  return nil
end

function GT_claim_next_order()
  local ids = GT_read_index_ids()
  if #ids > 0 then
    local id = ids[1]
    local pending_path = PENDING_DIR .. "/" .. id .. ".json"
    local body = read(pending_path)
    if body and body ~= "" then
      local order = GT_parse_order_json(body)
      if order then
        write(PROCESSING_DIR .. "/" .. id .. ".json", body)
        write(ACTIVE_FILE, tostring(id))
        write(pending_path, "")
        local rest = {}
        for i = 2, #ids do rest[#rest + 1] = ids[i] end
        GT_write_index_ids(rest)
        GT_log("Claimed pending #" .. id .. " world=" .. order.world_name)
        return order
      end
    end
    GT_log("Pending file missing for #" .. id)
    local rest = {}
    for i = 2, #ids do rest[#rest + 1] = ids[i] end
    GT_write_index_ids(rest)
  end
  return GT_claim_from_processing()
end

function GT_inventory_count(item_id)
  local inv = bot:getInventory()
  if not inv then return 0 end
  return inv:findItem(item_id) or 0
end

function GT_ensure_connected()
  if bot.status == BotStatus.online then return true end
  GT_log("Connecting... status=" .. tostring(bot.status))
  bot:connect()
  for _ = 1, 45 do
    sleep(1000)
    if bot.status == BotStatus.online then return true end
  end
  return false
end

function GT_warp_to_world(world_field)
  local world_name, door_id = GT_split_world(world_field)
  if world_name == "" then
    GT_log("Empty world name in order")
    return false
  end
  for attempt = 1, 10 do
    if GT_order_expired() then return false end
    GT_log("Warp " .. attempt .. "/10 -> " .. world_name .. (door_id ~= "" and ("|" .. door_id) or ""))
    if door_id ~= "" then
      bot:warp(world_name, door_id)
    else
      bot:warp(world_name)
    end
    sleep(WARP_RETRY_MS)
    if bot:isInWorld(world_name) then
      GT_log("In world " .. world_name)
      return true
    end
    local cur = ""
    if bot:isInWorld() and bot:getWorld() then
      cur = tostring(bot:getWorld().name or "?")
    end
    GT_log("Not in " .. world_name .. " yet (now: " .. cur .. ")")
  end
  return false
end

function GT_tile_has_access(x, y)
  local ok, result = pcall(function()
    return hasAccess(x, y)
  end)
  return ok and result == true
end

function GT_is_display_box(fg)
  if not fg or fg == 0 then return false end
  if fg == DISPLAY_BOX_FG then return true end
  local ok, info = pcall(function() return getInfo(fg) end)
  if not ok or not info then return false end
  local name = tostring(info.name or ""):lower()
  if name:find("seed", 1, true) then return false end
  return name:find("display box", 1, true) ~= nil
end

function GT_path_exists(x, y)
  local ok, path = pcall(function() return bot:getPath(x, y) end)
  if ok and path and #path > 0 then return true end
  return false
end

function GT_can_reach_tile(x, y)
  if GT_path_exists(x, y) then return true end
  if GT_tile_has_access(x, y) then return true end
  return false
end

function GT_find_display_boxes()
  local world = bot:getWorld()
  if not world then return {} end
  local boxes = {}
  for _, tile in pairs(world:getTiles()) do
    if GT_is_display_box(tile.fg) then
      GT_log("Display box fg=" .. tile.fg .. " @ " .. tile.x .. "," .. tile.y)
      boxes[#boxes + 1] = { x = tile.x, y = tile.y, fg = tile.fg }
    end
  end
  return boxes
end

function GT_find_reachable_display_boxes()
  local all = GT_find_display_boxes()
  if #all == 0 then return {} end
  GT_log("Display boxes in world: " .. #all)
  local reachable = {}
  for _, box in ipairs(all) do
    if GT_display_has_stand_spot(box.x, box.y) then
      reachable[#reachable + 1] = box
    end
  end
  if #reachable == 0 then
    GT_log("Reach check failed — trying all display tiles anyway")
    return all
  end
  return reachable
end

function GT_adjacent_stand_spots(dx, dy)
  -- Display tile (dx,dy) etrafındaki 4 komşu; yan yana kutularda üst/alt gerekir
  return {
    { x = dx, y = dy - 1, kind = "above" },
    { x = dx - 1, y = dy, kind = "left" },
    { x = dx + 1, y = dy, kind = "right" },
    { x = dx, y = dy + 1, kind = "below" },
  }
end

function GT_spot_priority(spot, dx, dy)
  if spot.kind == "above" then return 0 end
  if spot.kind == "left" or spot.kind == "right" then return 1 end
  return 2
end

function GT_display_has_stand_spot(dx, dy)
  for _, spot in ipairs(GT_adjacent_stand_spots(dx, dy)) do
    if GT_tile_standable(spot.x, spot.y) then
      return true
    end
  end
  return false
end

function GT_bot_tile_xy()
  local ok, me = pcall(function() return getLocal() end)
  if not ok or not me then return nil, nil end
  local x, y = me.posx, me.posy
  if x > 200 or y > 200 then
    x = math.floor(x / 32)
    y = math.floor(y / 32)
  else
    x = math.floor(x + 0.5)
    y = math.floor(y + 0.5)
  end
  return x, y
end

function GT_tile_distance(ax, ay, bx, by)
  return math.abs(ax - bx) + math.abs(by - by)
end

function GT_is_adjacent_to(bx, by, dx, dy)
  return GT_tile_distance(bx, by, dx, dy) == 1
end

function GT_walk_to_tile(tx, ty)
  for _ = 1, 4 do
    pcall(function() bot:findPath(tx, ty) end)
    sleep(1000)
    if bot:isInTile(tx, ty) then return true end
    local bx, by = GT_bot_tile_xy()
    if bx and bx == tx and by == ty then return true end
  end
  pcall(function() bot:findWorldPath(tx, ty) end)
  sleep(1200)
  if bot:isInTile(tx, ty) then return true end
  pcall(function() bot:moveTile(tx, ty) end)
  sleep(700)
  if bot:isInTile(tx, ty) then return true end
  local bx, by = GT_bot_tile_xy()
  return bx == tx and by == ty
end

-- Display tile (dx,dy) komşusuna git; yüzü kutunun tile'ına çevir.
function GT_face_left_toward_display(sx, sy, dx, dy)
  if dx > sx then
    return true
  elseif dx < sx then
    return false
  elseif dy > sy then
    return true
  else
    return false
  end
end

function GT_face_toward_display(sx, sy, dx, dy)
  local face_left = GT_face_left_toward_display(sx, sy, dx, dy)
  pcall(function() bot:setDirection(face_left) end)
  sleep(500)
  GT_log(
    "Face display " .. dx .. "," .. dy .. " from stand " .. sx .. "," .. sy ..
    " face_left=" .. tostring(face_left)
  )
  return face_left
end

function GT_stand_adjacent_to_display(dx, dy)
  local bx0, by0 = GT_bot_tile_xy()
  local candidates = {}
  for _, spot in ipairs(GT_adjacent_stand_spots(dx, dy)) do
    if GT_tile_standable(spot.x, spot.y) then
      local dist = 999
      if bx0 then dist = GT_tile_distance(bx0, by0, spot.x, spot.y) end
      candidates[#candidates + 1] = {
        x = spot.x, y = spot.y, dist = dist,
        kind = spot.kind, pri = GT_spot_priority(spot, dx, dy),
      }
    else
      local fg = "?"
      local ok, t = pcall(function() return getTile(spot.x, spot.y) end)
      if ok and t then fg = tostring(t.fg) end
      GT_log("Skip stand " .. spot.x .. "," .. spot.y .. " fg=" .. fg .. " (" .. spot.kind .. ")")
    end
  end
  table.sort(candidates, function(a, b)
    if a.pri ~= b.pri then return a.pri < b.pri end
    return a.dist < b.dist
  end)

  for _, spot in ipairs(candidates) do
    GT_log("Path to stand " .. spot.x .. "," .. spot.y .. " (" .. spot.kind .. ") → display " .. dx .. "," .. dy)
    if GT_walk_to_tile(spot.x, spot.y) and bot:isInTile(spot.x, spot.y) then
      GT_log("Ready stand " .. spot.x .. "," .. spot.y .. " → display " .. dx .. "," .. dy)
      return true, spot.x, spot.y, spot.kind
    end
    GT_log("Could not reach stand tile " .. spot.x .. "," .. spot.y)
  end
  return false
end

function GT_attempt_drop_at_display(sx, sy, dx, dy, kind, item_id, count, before)
  pcall(function() bot.auto_transfer.drop_vertical = true end)

  if kind == "above" or (sx == dx and sy < dy) then
    pcall(function() bot:setDirection(false) end)
    sleep(500)
    GT_log("Vertical drop (above) " .. sx .. "," .. sy .. " → display " .. dx .. "," .. dy)
    GT_drop_once(item_id, count)
    listenEvents(1)
    if GT_inventory_dropped(before, item_id, count) then return true end
    pcall(function() bot:drop(item_id, count) end)
    sleep(800)
    listenEvents(1)
    return GT_inventory_dropped(before, item_id, count)
  end

  if kind == "below" or (sx == dx and sy > dy) then
    pcall(function() bot:setDirection(false) end)
    sleep(500)
    GT_log("Vertical drop (below) " .. sx .. "," .. sy .. " → display " .. dx .. "," .. dy)
    GT_drop_once(item_id, count)
    listenEvents(1)
    if GT_inventory_dropped(before, item_id, count) then return true end
    pcall(function() bot:drop(item_id, count) end)
    sleep(800)
    listenEvents(1)
    return GT_inventory_dropped(before, item_id, count)
  end

  local primary = GT_face_left_toward_display(sx, sy, dx, dy)
  local faces = { primary, not primary }
  for fi, face_left in ipairs(faces) do
    pcall(function() bot:setDirection(face_left) end)
    sleep(500)
    GT_log("Horizontal drop try " .. fi .. " face_left=" .. tostring(face_left))
    GT_drop_once(item_id, count)
    listenEvents(1)
    if GT_inventory_dropped(before, item_id, count) then
      return true
    end
    local delta = before - GT_inventory_count(item_id)
    if delta > 0 then
      GT_log("Inv -" .. delta .. " on wrong tile — stop (no second drop)")
      return false
    end
  end
  pcall(function() bot:drop(item_id, count) end)
  sleep(800)
  listenEvents(1)
  return GT_inventory_dropped(before, item_id, count)
end

function GT_tile_standable(x, y)
  local ok, tile = pcall(function() return getTile(x, y) end)
  if not ok or not tile then return false end
  if tile.fg == 0 then return true end
  if tile.fg == DISPLAY_BOX_FG then return false end
  local info_ok, info = pcall(function() return getInfo(tile.fg) end)
  if info_ok and info and (info.collision_type or 0) > 0 then
    return false
  end
  return true
end

-- Display kutusunun soluna veya sağına git; yüzü kutuya çevir (drop ön tile = display).
function GT_stand_beside_display(dx, dy)
  return GT_stand_adjacent_to_display(dx, dy)
end

function GT_drop_once(item_id, count)
  bot:sendPacket(2, "action|drop\nitemID|" .. tostring(item_id) .. "|\n")
  sleep(450)
  bot:sendPacket(2,
    "action|dialog_return\n" ..
    "dialog_name|drop_item\n" ..
    "itemID|" .. tostring(item_id) .. "|\n" ..
    "count|" .. tostring(count) .. "\n"
  )
  sleep(700)
end

function GT_inventory_dropped(before, item_id, amount)
  local after = GT_inventory_count(item_id)
  return (before - after) >= amount
end

function GT_drop_on_display(display_x, display_y, item_id, count)
  local before = GT_inventory_count(item_id)
  if before < count then return false end

  GT_log("Drop " .. count .. "x item " .. item_id .. " on display tile " .. display_x .. "," .. display_y)

  local stood, sx, sy, skind = GT_stand_adjacent_to_display(display_x, display_y)
  if not stood then
    GT_log("No stand tile beside display " .. display_x .. "," .. display_y)
    return false
  end
  if not skind then skind = "left" end

  local max_attempts = 2
  if item_id == ITEM_BGL then max_attempts = 1 end
  for attempt = 1, max_attempts do
    if GT_order_expired() then return false end
    if GT_inventory_dropped(before, item_id, count) then
      local delta = before - GT_inventory_count(item_id)
      GT_log("Drop OK on " .. display_x .. "," .. display_y .. " (inv -" .. delta .. ")")
      return true
    end
    GT_log("Drop attempt " .. attempt .. "/2 stand " .. sx .. "," .. sy .. " (" .. skind .. ")")
    if not bot:isInTile(sx, sy) then
      GT_log("Not on stand tile, re-walk")
      if not GT_walk_to_tile(sx, sy) then return false end
    end
    if GT_attempt_drop_at_display(sx, sy, display_x, display_y, skind, item_id, count, before) then
      local delta = before - GT_inventory_count(item_id)
      GT_log("Drop OK on " .. display_x .. "," .. display_y .. " (inv -" .. delta .. ")")
      return true
    end
  end

  GT_log("Drop failed — inv before=" .. before .. " after=" .. GT_inventory_count(item_id))
  return false
end

function GT_max_drop_chunk(item_id)
  if item_id == ITEM_BGL then return 1 end
  return MAX_STACK
end

function GT_drop_all_on_display(box, item_id, total_qty)
  local remaining = total_qty
  local max_chunk = GT_max_drop_chunk(item_id)
  while remaining > 0 do
    if GT_order_expired() then return false, "order_timeout_2min" end
    local chunk = remaining
    if chunk > max_chunk then chunk = max_chunk end
    local ok = GT_drop_on_display(box.x, box.y, item_id, chunk)
    if not ok then
      return false, "drop_failed"
    end
    remaining = remaining - chunk
    sleep(500)
  end
  return true, ""
end

function GT_clear_state()
  state.busy = false
  state.order = nil
  state.order_started_ms = 0
end

function GT_finish_fail(order, reason)
  GT_log(reason)
  GT_write_result(order.id, "failed", reason)
  write(ACTIVE_FILE, "")
  GT_clear_state()
  GT_write_bot_balance()
  GT_go_home()
end

function GT_finish_success(order)
  GT_write_result(order.id, "completed", "")
  GT_log("Done #" .. order.id)
  write(ACTIVE_FILE, "")
  GT_clear_state()
  GT_write_bot_balance()
  GT_go_home()
end

function GT_order_deliveries(order)
  if order.deliveries and #order.deliveries > 0 then
    return order.deliveries
  end
  return {
    {
      item_type = order.item_type or "wl",
      quantity = tonumber(order.quantity) or 1,
      item_id = order.item_id or GT_item_id_for_type(order.item_type),
    },
  }
end

function GT_can_fulfill_deliveries(deliveries)
  for _, d in ipairs(deliveries) do
    local iid = d.item_id or GT_item_id_for_type(d.item_type)
    local need = tonumber(d.quantity) or 0
    if need <= 0 or GT_inventory_count(iid) < need then
      return false, iid, need
    end
  end
  return true
end

function GT_run_order(order)
  state.busy = true
  state.order = order
  state.order_started_ms = GT_now_ms()

  local deliveries = GT_order_deliveries(order)
  local world_name = GT_split_world(order.world_name)

  if world_name == "" then
    GT_finish_fail(order, "invalid_world")
    return
  end
  local can, missing_id, missing_qty = GT_can_fulfill_deliveries(deliveries)
  if not can then
    GT_log("Stock check failed item " .. tostring(missing_id) .. " need " .. tostring(missing_qty))
    GT_finish_fail(order, "insufficient_bot_stock")
    return
  end
  if not GT_ensure_connected() then
    GT_finish_fail(order, "bot_offline")
    return
  end

  GT_log(string.format(
    "Order #%d world=%s growid=%s lines=%d",
    order.id, order.world_name, order.growid, #deliveries
  ))

  if not GT_warp_to_world(order.world_name) then
    GT_finish_fail(order, "warp_failed")
    return
  end

  sleep(2000)
  local boxes = GT_find_reachable_display_boxes()
  if #boxes == 0 then
    GT_log("Rescan display boxes in 3s (world load)...")
    sleep(3000)
    boxes = GT_find_reachable_display_boxes()
  end
  if #boxes == 0 then
    GT_finish_fail(order, "no_display_box")
    return
  end

  GT_log("Found " .. #boxes .. " display box(es)")
  local ok = false
  local reason = "drop_failed"
  for i, box in ipairs(boxes) do
    GT_log("Display box " .. i .. "/" .. #boxes .. " tile " .. box.x .. "," .. box.y)
    ok = true
    reason = ""
    for li, line in ipairs(deliveries) do
      local item_id = line.item_id or GT_item_id_for_type(line.item_type)
      local qty = tonumber(line.quantity) or 1
      GT_log("Line " .. li .. "/" .. #deliveries .. ": " .. qty .. "x item " .. item_id)
      if GT_inventory_count(item_id) < qty then
        ok = false
        reason = "insufficient_bot_stock"
        break
      end
      local line_ok, line_reason = GT_drop_all_on_display(box, item_id, qty)
      if not line_ok then
        ok = false
        reason = line_reason ~= "" and line_reason or "drop_failed"
        break
      end
      sleep(600)
    end
    if ok then break end
    GT_log("Display " .. box.x .. "," .. box.y .. " failed: " .. reason)
  end
  if ok then
    GT_finish_success(order)
  else
    GT_finish_fail(order, reason ~= "" and reason or "drop_failed")
  end
end

GT_init_paths()
GT_write_bot_balance()
GT_log("Withdraw worker started")

while true do
  if not state.busy then
    GT_write_bot_balance()
    local order = GT_claim_next_order()
    if order then
      GT_run_order(order)
    end
  end
  listenEvents(2)
  sleep(POLL_MS)
end
