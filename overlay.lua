-- The correction overlay: fix the detected anime, episode mapping and status.

local M = {}

local STATUSES = { "CURRENT", "PAUSED", "DROPPED", "COMPLETED", "REPEATING", "PLANNING" }
local FIELDS = { "id", "episode", "status" }
-- U+FEFF stops libass from reading what follows as part of an override
local JOINER = "\239\187\191"

local deps
local osd = mp.create_osd_overlay("ass-events")
local state = { active = false }

local function escape(text)
    return (tostring(text):gsub("[\\{}]", { ["\\"] = "\\" .. JOINER, ["{"] = "\\{", ["}"] = "}" .. JOINER }))
end

local function field_text(name)
    local text = {
        id = state.id ~= "" and state.id or "(paste AniList URL or type ID)",
        episode = state.episode ~= "" and state.episode or "(optional, relative episode)",
        status = STATUSES[state.status_index],
    }
    return text[name]
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
    local labels = { id = "ID / URL", episode = "Corrected episode", status = "Status" }
    for index, name in ipairs(FIELDS) do
        lines[#lines + 1] = string.format(
            "{\\fs22}%s %s: %s",
            state.focus == index and ">" or "  ",
            labels[name],
            escape(field_text(name))
        )
    end

    osd.res_x, osd.res_y = width, height
    osd.data = table.concat({
        "{\\an7\\pos(40,50)\\fs30\\bord2\\shad0}Correction",
        "{\\fs20}Detected: " .. detected,
        "",
        table.concat(lines, "\\N"),
        "",
        "{\\fs18}Tab/Up/Down: switch field | Left/Right: change status | Enter: submit | Esc: cancel",
        "{\\fs18}Ctrl+V: paste clipboard | Backspace/Del: clear field",
    }, "\\N")
    osd:update()
end

local function extract_id(text)
    text = text:match("^%s*(.-)%s*$")
    return text:match("anilist%.co/anime/(%d+)") or text:match("^(%d+)$")
end

local function append(text)
    local name = FIELDS[state.focus]
    if name == "id" then
        state.id = state.id .. text
    elseif name == "episode" then
        state.episode = state.episode .. text:gsub("%D", "")
    end
    render()
end

local function clear_field()
    local name = FIELDS[state.focus]
    if name ~= "status" then
        state[name] = ""
        render()
    end
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
    osd:remove()
    for name in pairs(bindings) do
        mp.remove_key_binding(name)
    end
    bindings = {}
end

local function submit()
    local anilist_id = extract_id(state.id)
    if not anilist_id then
        return mp.osd_message("Invalid AniList URL/ID.", 2)
    end

    local episode
    if state.episode ~= "" then
        episode = tonumber(state.episode)
        if not episode or episode < 1 then
            return mp.osd_message("Episode override must be a positive number.", 2)
        end
    end

    local correction = {
        anime_id = tonumber(anilist_id),
        episode = episode,
        status = state.status_changed and STATUSES[state.status_index] or nil,
    }
    local info = state.info
    close()
    mp.osd_message("Applying correction...", 2)

    deps.backend.call("correct", { info = info, correction = correction }, function(response)
        if response.ok and response.info then
            deps.set_info(response.info)
        end
        deps.notify(response, true)
    end)
end

local function move_focus(step)
    state.focus = (state.focus + step - 1) % #FIELDS + 1
    render()
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
        state.id = state.id ~= "" and state.id or tostring(info.anime_id or "")
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
        id = "",
        episode = "",
        status_index = 1,
        status_changed = false,
    }

    bind("ESC", close)
    bind("ENTER", submit)
    bind("KP_ENTER", submit)
    bind("TAB", function() move_focus(1) end)
    bind("DOWN", function() move_focus(1) end)
    bind("UP", function() move_focus(-1) end)
    bind("LEFT", function() cycle_status(-1) end)
    bind("RIGHT", function() cycle_status(1) end)
    bind("BS", clear_field)
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

-- deps: backend, notify(response, manual), ensure_info(callback), set_info(info)
function M.init(dependencies)
    deps = dependencies
    mp.observe_property("osd-dimensions", "native", render)
    mp.add_key_binding("c", "correct_anime_id", open)
end

return M
