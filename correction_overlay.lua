local utils = require 'mp.utils'

local M = {}

local python_command = nil
local python_options_json = nil
local callback = nil
local get_current_anime_info = nil
local set_current_anime_info = nil

function M.init(deps)
    python_command = deps.python_command
    python_options_json = deps.python_options_json
    callback = deps.callback
    get_current_anime_info = deps.get_current_anime_info
    set_current_anime_info = deps.set_current_anime_info
end

local correction_overlay = {
    active = false,
    focus = 1,
    id_input = "",
    episode_input = "",
    status_index = 1,
    detected = nil,
    path = nil,
    s_dir = nil
}

local correction_statuses = {
    "CURRENT",
    "PAUSED",
    "DROPPED",
    "COMPLETED",
    "REPEATING",
    "PLANNING",
}

local function get_status_index(status_code)
    if not status_code then
        return 1
    end

    for i, status in ipairs(correction_statuses) do
        if status == status_code then
            return i
        end
    end

    return 1
end

local overlay_timer = mp.add_periodic_timer(0.1, function()
    if not correction_overlay.active then
        return
    end

    local w, h = mp.get_osd_size()
    if not w or not h or w == 0 or h == 0 then
        return
    end

    local detected_name = (correction_overlay.detected and correction_overlay.detected.anime_name) or "Unknown"
    local detected_id = (correction_overlay.detected and correction_overlay.detected.anime_id) or "?"
    local detected_ep = (correction_overlay.detected and correction_overlay.detected.episode) or "?"
    local detected_status = (correction_overlay.detected and correction_overlay.detected.current_status) or "CURRENT"
    local focus = correction_overlay.focus
    local id_text = correction_overlay.id_input ~= "" and correction_overlay.id_input or "(paste AniList URL or type ID)"
    local episode_text = correction_overlay.episode_input ~= "" and correction_overlay.episode_input or "(optional, relative episode)"
    local selected_status = correction_statuses[correction_overlay.status_index] or correction_statuses[1]
    local status_text = selected_status

    local ass = string.format(
        "{\\an7\\pos(40,50)\\fs30\\bord2\\shad0}Correction"
            .. "\\N{\\fs20}Detected: %s"
            .. "\\N{\\fs20}ID: %s | Episode: %s | Status: %s"
            .. "\\N"
            .. "\\N{\\fs22}%s ID / URL: %s"
            .. "\\N{\\fs22}%s Corrected episode: %s"
            .. "\\N{\\fs22}%s Status: %s"
            .. "\\N"
            .. "\\N{\\fs18}Tab/Up/Down: switch field | Left/Right: change status | Enter: submit | Esc: cancel"
            .. "\\N{\\fs18}Ctrl+V: paste clipboard | Backspace/Del: clear field",
        detected_name,
        tostring(detected_id),
        tostring(detected_ep),
        tostring(detected_status),
        focus == 1 and ">" or "  ",
        id_text,
        focus == 2 and ">" or "  ",
        episode_text,
        focus == 3 and ">" or "  ",
        status_text
    )

    mp.set_osd_ass(w, h, ass)
end)
overlay_timer:stop()

local function extract_anilist_id(input_text)
    if not input_text then
        return nil
    end

    local trimmed = input_text:gsub("^%s*(.-)%s*$", "%1")
    if trimmed == "" then
        return nil
    end

    return trimmed:match("anilist%.co/anime/(%d+)") or trimmed:match("^(%d+)$")
end

local function append_to_active_field(text)
    if not text or text == "" then
        return
    end

    if correction_overlay.focus == 1 then
        correction_overlay.id_input = correction_overlay.id_input .. text
    elseif correction_overlay.focus == 2 then
        local digits = text:gsub("%D", "")
        if digits ~= "" then
            correction_overlay.episode_input = correction_overlay.episode_input .. digits
        end
    end
end

local function paste_clipboard_to_field()
    local platform = mp.get_property("platform")
    local args

    if platform == "windows" then
        args = {"powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"}
    elseif platform == "darwin" then
        args = {"pbpaste"}
    elseif os.getenv("XDG_SESSION_TYPE") == "wayland" then
        args = {"wl-paste"}
    else
        args = {"xclip", "-selection", "clipboard", "-o"}
    end

    mp.command_native_async({
        name = "subprocess",
        args = args,
        capture_stdout = true
    }, function(success, result)
        if not success or not result or result.status ~= 0 or not result.stdout then
            mp.osd_message("Clipboard paste failed", 2)
            return
        end

        local text = result.stdout:gsub("[\r\n]+$", "")
        append_to_active_field(text)
    end)
end

local function clear_overlay_bindings()
    local keys = {
        "ESC", "ENTER", "KP_ENTER", "TAB", "UP", "DOWN", "LEFT", "RIGHT", "BS", "DEL", "Ctrl+v", "v", "V", "any_unicode"
    }

    for _, key in ipairs(keys) do
        mp.remove_key_binding("correct_overlay_" .. key)
    end
end

local function close_correction_overlay()
    correction_overlay.active = false
    overlay_timer:stop()
    mp.set_osd_ass(0, 0, "")
    clear_overlay_bindings()
end

local function submit_correction()
    local anilist_id = extract_anilist_id(correction_overlay.id_input)
    if not anilist_id then
        mp.osd_message("Invalid AniList URL/ID.", 2)
        return
    end

    local relative_episode = nil
    if correction_overlay.episode_input ~= "" then
        relative_episode = tonumber(correction_overlay.episode_input)
        if not relative_episode or relative_episode < 1 then
            mp.osd_message("Episode override must be a positive number.", 2)
            return
        end
    end

    local selected_status = correction_statuses[correction_overlay.status_index] or correction_statuses[1]

    local args = {python_command, correction_overlay.s_dir .. "anilistUpdater.py", correction_overlay.path, "correct", python_options_json, anilist_id}
    if relative_episode then
        table.insert(args, tostring(relative_episode))
    end
    table.insert(args, selected_status)

    close_correction_overlay()
    mp.osd_message("Applying correction...", 2)

    mp.command_native_async({
        name = "subprocess",
        args = args,
        capture_stdout = true
    }, function(success, result, error)
        if success and result and result.status == 0 and result.stdout then
            for line in result.stdout:gmatch("[^\r\n]+") do
                local json_part = line:match("^INFO:%s*(.+)$")
                if json_part then
                    local info = utils.parse_json(json_part)
                    if info and type(info) == "table" and set_current_anime_info then
                        set_current_anime_info(info)
                    end
                end
            end
        end
        if callback then
            callback(success, result, error)
        end
    end)
end

local function open_correction_overlay(path, s_dir, detected)
    correction_overlay.active = true
    correction_overlay.focus = 1
    correction_overlay.path = path
    correction_overlay.s_dir = s_dir
    correction_overlay.detected = detected
    correction_overlay.id_input = ""
    correction_overlay.episode_input = ""
    correction_overlay.status_index = 1

    if detected then
        if detected.anime_id then
            correction_overlay.id_input = tostring(detected.anime_id)
        end
        if detected.episode then
            correction_overlay.episode_input = tostring(detected.episode)
        end
        if detected.current_status then
            correction_overlay.status_index = get_status_index(detected.current_status)
        end
    end

    local function switch_focus(step)
        local next_focus = correction_overlay.focus + step

        if next_focus < 1 then
            next_focus = 3
        elseif next_focus > 3 then
            next_focus = 1
        end

        correction_overlay.focus = next_focus
    end

    local function cycle_status(step)
        if correction_overlay.focus ~= 3 then
            return
        end

        local count = #correction_statuses
        local idx = correction_overlay.status_index + step
        if idx < 1 then
            idx = count
        elseif idx > count then
            idx = 1
        end
        correction_overlay.status_index = idx
    end

    mp.add_forced_key_binding("ESC", "correct_overlay_ESC", function()
        close_correction_overlay()
    end)

    mp.add_forced_key_binding("ENTER", "correct_overlay_ENTER", function()
        submit_correction()
    end)

    mp.add_forced_key_binding("KP_ENTER", "correct_overlay_KP_ENTER", function()
        submit_correction()
    end)

    mp.add_forced_key_binding("TAB", "correct_overlay_TAB", function()
        switch_focus(1)
    end)
    mp.add_forced_key_binding("UP", "correct_overlay_UP", function()
        switch_focus(-1)
    end)
    mp.add_forced_key_binding("DOWN", "correct_overlay_DOWN", function()
        switch_focus(1)
    end)
    mp.add_forced_key_binding("LEFT", "correct_overlay_LEFT", function()
        cycle_status(-1)
    end)
    mp.add_forced_key_binding("RIGHT", "correct_overlay_RIGHT", function()
        cycle_status(1)
    end)

    mp.add_forced_key_binding("BS", "correct_overlay_BS", function()
        if correction_overlay.focus == 1 then
            correction_overlay.id_input = ""
        else
            correction_overlay.episode_input = ""
        end
    end)

    mp.add_forced_key_binding("DEL", "correct_overlay_DEL", function()
        if correction_overlay.focus == 1 then
            correction_overlay.id_input = ""
        else
            correction_overlay.episode_input = ""
        end
    end)

    mp.add_forced_key_binding("Ctrl+v", "correct_overlay_Ctrl+v", paste_clipboard_to_field)

    mp.add_forced_key_binding("any_unicode", "correct_overlay_any_unicode", function(key)
        if not key then
            return
        end

        local key_text = key.key_text or ""
        if key.event and key.event ~= "down" and key.event ~= "repeat" then
            return
        end

        if key_text == "" or key_text == "\r" or key_text == "\n" then
            return
        end

        append_to_active_field(key_text)
    end, {repeatable = true, complex = true})

    overlay_timer:resume()
end

function M.correct_anime_id(get_path)
    local path = get_path()
    local s_dir = debug.getinfo(1).source:match("@?(.*/)") or
                  (debug.getinfo(2).source:match("@?(.*/)") or "./")

    -- Use pre-fetched info if available
    local current_info = get_current_anime_info()
    if current_info then
        open_correction_overlay(path, s_dir, current_info)
        return
    end

    -- Fallback: fetch info from Python
    mp.osd_message("Loading detected anime info...", 2)

    mp.command_native_async({
        name = "subprocess",
        args = {python_command, s_dir .. "anilistUpdater.py", path, "info", python_options_json},
        capture_stdout = true
    }, function(success, result)
        local detected = nil
        if success and result and result.status == 0 then
            for line in result.stdout:gmatch("[^\r\n]+") do
                local json_part = line:match("^INFO:%s*(.+)$")
                if json_part then
                    local info = utils.parse_json(json_part)
                    if info and type(info) == "table" then
                        detected = info
                        break
                    end
                end
            end
        end

        open_correction_overlay(path, s_dir, detected)
    end)
end

return M
