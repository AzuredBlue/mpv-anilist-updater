-- The correction overlay: fix the detected anime, episode mapping and status.

local M = {}

local STATUSES = { "CURRENT", "PAUSED", "DROPPED", "COMPLETED", "REPEATING", "PLANNING" }
local FIELDS = { "anime", "episode", "status" }
-- Typing waits this long before searching, so a word costs about one request
local DEBOUNCE = 0.4
local MIN_QUERY = 2
local SELECTED = "{\\1c&H00D7FF&}"
local NORMAL = "{\\1c&HFFFFFF&}"
-- U+FEFF stops libass from reading what follows as part of an override
local JOINER = "\239\187\191"

local deps
local osd = mp.create_osd_overlay("ass-events")
local state = { active = false }
-- Search results by query. Outlives the overlay, so reopening it or retyping costs nothing.
local memo = {}

local function escape(text)
    return (tostring(text):gsub("[\\{}]", { ["\\"] = "\\" .. JOINER, ["{"] = "\\{", ["}"] = "}" .. JOINER }))
end

local function field_text(name)
    local text = {
        anime = state.query ~= "" and state.query or "(type a title, or paste an AniList URL/ID)",
        episode = state.episode ~= "" and state.episode or "(optional, relative episode)",
        status = STATUSES[state.status_index],
    }
    return text[name]
end

local function describe(result)
    local parts = { result.title or "?" }
    for _, part in ipairs({ result.format or "", result.year or "", result.episodes and (result.episodes .. " eps") or "" }) do
        if part ~= "" then
            parts[#parts + 1] = part
        end
    end
    if result.status then
        parts[#parts + 1] = "[" .. result.status .. "]"
    end
    return table.concat(parts, " \194\183 ")
end

local function render()
    if not state.active then
        return
    end
    local width, height = mp.get_osd_size()
    if not width or width == 0 or height == 0 then
        return
    end

    local info = state.info or {}
    local detected = state.loading and "Detecting anime..."
        or string.format(
            "%s\\N{\\fs20}ID: %s | Episode: %s | Status: %s",
            escape(info.anime_name or "Unknown"),
            info.anime_id or "?",
            info.episode or "?",
            info.current_status or "CURRENT"
        )

    local lines = {}
    local labels = { anime = "Anime", episode = "Corrected episode", status = "Status" }
    for index, name in ipairs(FIELDS) do
        lines[#lines + 1] = string.format(
            "{\\fs22}%s %s: %s",
            state.focus == index and ">" or "  ",
            labels[name],
            escape(field_text(name))
        )
        if name == "anime" then
            for i, result in ipairs(state.results) do
                lines[#lines + 1] = string.format(
                    "{\\fs20}%s    %s %s",
                    i == state.selected and SELECTED or NORMAL,
                    i == state.selected and "\226\150\182" or " ",
                    escape(describe(result))
                )
            end
            if state.inflight then
                lines[#lines + 1] = NORMAL .. "{\\fs18}    Searching..."
            elseif state.error then
                lines[#lines + 1] = NORMAL .. "{\\fs18}    " .. escape(state.error)
            end
            lines[#lines + 1] = NORMAL
        end
    end

    osd.res_x, osd.res_y = width, height
    osd.data = table.concat({
        "{\\an7\\pos(40,50)\\fs30\\bord2\\shad0}Correction",
        "{\\fs20}Detected: " .. detected,
        "",
        table.concat(lines, "\\N"),
        "",
        "{\\fs18}Type to search | Up/Down: pick result | Tab: next field | Left/Right: change status",
        "{\\fs18}Enter: submit | Esc: cancel | Ctrl+V: paste | Backspace: delete | Del: clear field",
    }, "\\N")
    osd:update()
end

local function extract_id(text)
    text = text:match("^%s*(.-)%s*$")
    return text:match("anilist%.co/anime/(%d+)") or text:match("^(%d+)$")
end

local function key_of(text)
    return text:match("^%s*(.-)%s*$"):lower()
end

-- Work out what the search box offers for its text. Only a title long enough and not seen before
-- costs a request; one request at a time, and a finished one re-checks what was typed meanwhile.
local function refresh()
    if not state.active then
        return
    end
    local text = key_of(state.query)
    local anilist_id = extract_id(text)
    state.error = nil

    if text == "" then
        state.results = state.detected
    elseif anilist_id then
        state.results = { { id = tonumber(anilist_id), title = "AniList ID " .. anilist_id } }
    elseif memo[text] then
        state.results = memo[text]
    elseif #text < MIN_QUERY then
        state.results = {}
    else
        state.results = {}
        if not state.inflight then
            state.inflight = true
            local requested_by = state
            deps.backend.call("search", { query = text }, function(response)
                local found = response.ok and response.results
                if found then
                    memo[text] = found
                end
                -- the overlay was closed (and maybe reopened) meanwhile; the results are still worth keeping
                if state ~= requested_by then
                    return
                end
                state.inflight = false
                if not found and state.active and key_of(state.query) == text then
                    state.error = response.message
                end
                if state.active and not state.timer:is_enabled() and key_of(state.query) ~= text then
                    return refresh()
                end
                if state.active and key_of(state.query) == text then
                    state.results = memo[text] or {}
                end
                render()
            end)
        end
    end
    state.selected = 1
    render()
end

-- The typed text changed: show it now, search once typing pauses.
local function edited()
    state.timer:kill()
    if FIELDS[state.focus] == "anime" then
        state.timer:resume()
    end
    render()
end

local function append(text)
    local name = FIELDS[state.focus]
    if name == "anime" then
        state.query = state.query .. text
    elseif name == "episode" then
        state.episode = state.episode .. text:gsub("%D", "")
    end
    edited()
end

local function backspace()
    local name = FIELDS[state.focus]
    if name == "anime" then
        state.query = state.query:gsub("[\1-\127\194-\244][\128-\191]*$", "")
    elseif name == "episode" then
        state.episode = state.episode:sub(1, -2)
    end
    edited()
end

local function clear_field()
    local name = FIELDS[state.focus]
    if name == "anime" then
        state.query = ""
    elseif name == "episode" then
        state.episode = ""
    end
    edited()
end

local function paste()
    -- mpv 0.40+ can read the clipboard itself
    local text = mp.get_property("clipboard/text")
    if text then
        return append((text:gsub("[\r\n]+$", "")))
    end

    local platform = mp.get_property("platform")
    local args = { "xclip", "-selection", "clipboard", "-o" }
    if platform == "windows" then
        args = { "powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw" }
    elseif platform == "darwin" then
        args = { "pbpaste" }
    elseif os.getenv("XDG_SESSION_TYPE") == "wayland" then
        args = { "wl-paste" }
    end

    mp.command_native_async({ name = "subprocess", args = args, capture_stdout = true }, function(success, result)
        if success and result and result.status == 0 and result.stdout then
            append((result.stdout:gsub("[\r\n]+$", "")))
        else
            mp.osd_message("Clipboard paste failed", 2)
        end
    end)
end

local bindings = {}

local function close()
    state.active = false
    state.timer:kill()
    osd:remove()
    for name in pairs(bindings) do
        mp.remove_key_binding(name)
    end
    bindings = {}
end

local function submit()
    local picked = state.results[state.selected]
    if not picked then
        return mp.osd_message("Search for the anime and pick it from the results.", 2)
    end

    local episode
    if state.episode ~= "" then
        episode = tonumber(state.episode)
        if not episode or episode < 1 then
            return mp.osd_message("Episode override must be a positive number.", 2)
        end
    end

    local correction = {
        anime_id = picked.id,
        episode = episode,
        status = state.status_changed and STATUSES[state.status_index] or nil,
    }
    local info, generation = state.info, deps.generation()
    close()
    mp.osd_message("Applying correction...", 2)

    deps.backend.call("correct", { info = info, correction = correction }, function(response)
        if response.ok and response.info and generation == deps.generation() then
            deps.set_info(response.info)
        end
        deps.notify(response, true)
    end)
end

local function move_focus(step)
    state.focus = (state.focus + step - 1) % #FIELDS + 1
    render()
end

-- Up and Down walk the results while the search box is focused, then leave it.
local function move_selection(step)
    local target = state.selected + step
    if FIELDS[state.focus] == "anime" and target >= 1 and target <= #state.results then
        state.selected = target
        render()
    else
        move_focus(step)
    end
end

local function cycle_status(step)
    if FIELDS[state.focus] == "status" then
        state.status_index = (state.status_index + step - 1) % #STATUSES + 1
        state.status_changed = true
        render()
    end
end

local function bind(key, handler, flags)
    local name = "correct_overlay_" .. key
    bindings[name] = true
    mp.add_forced_key_binding(key, name, handler, flags)
end

local function status_index(status)
    for index, candidate in ipairs(STATUSES) do
        if candidate == status then
            return index
        end
    end
    return 1
end

-- Prefill what the user hasn't already typed.
local function prefill(info)
    state.info, state.loading = info, false
    if info then
        state.detected = info.anime_id
                and { { id = info.anime_id, title = info.anime_name, status = info.current_status } }
            or {}
        if state.query == "" then
            state.results = state.detected
        end
        state.episode = state.episode ~= "" and state.episode or tostring(info.episode or "")
        if not state.status_changed then
            state.status_index = status_index(info.current_status)
        end
    end
    render()
end

local function open()
    state = {
        active = true,
        loading = true,
        focus = 1,
        query = "",
        detected = {},
        results = {},
        selected = 1,
        episode = "",
        status_index = 1,
        status_changed = false,
    }

    state.timer = mp.add_timeout(DEBOUNCE, refresh)
    state.timer:kill()

    bind("ESC", close)
    bind("ENTER", submit)
    bind("KP_ENTER", submit)
    bind("TAB", function() move_focus(1) end)
    bind("Shift+TAB", function() move_focus(-1) end)
    bind("DOWN", function() move_selection(1) end)
    bind("UP", function() move_selection(-1) end)
    bind("LEFT", function() cycle_status(-1) end)
    bind("RIGHT", function() cycle_status(1) end)
    bind("BS", backspace, { repeatable = true })
    bind("DEL", clear_field)
    bind("Ctrl+v", paste)
    bind("any_unicode", function(event)
        local text = event and event.key_text or ""
        if (event.event == "down" or event.event == "repeat") and text ~= "" and not text:find("[\r\n]") then
            append(text)
        end
    end, { repeatable = true, complex = true })

    render()
    deps.ensure_info(function(info)
        if state.active then
            prefill(info)
        end
    end)
end

-- deps: backend, notify(response, manual), ensure_info(callback), generation(), set_info(info)
function M.init(dependencies)
    deps = dependencies
    mp.observe_property("osd-dimensions", "native", render)
    mp.add_key_binding("c", "correct_anime_id", open)
end

return M
