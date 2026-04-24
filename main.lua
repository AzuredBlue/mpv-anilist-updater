--[[
Configuration options for anilistUpdater (set in anilistUpdater.conf):

DIRECTORIES: Table or comma/semicolon-separated string. The directories the script will work on. Leaving it empty will make it work on every video you watch with mpv. Example: DIRECTORIES = {"D:/Torrents", "D:/Anime"}

EXCLUDED_DIRECTORIES: Table or comma/semicolon-separated string. Useful for ignoring paths inside directories from above. Example: EXCLUDED_DIRECTORIES = {"D:/Torrents/Watched", "D:/Anime/Planned"}

UPDATE_PERCENTAGE: Number (0-100). The percentage of the video you need to watch before it updates AniList automatically. Default is 85 (usually before the ED of a usual episode duration).

SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE: Boolean. If true, when watching episode 1 of a completed anime, set it to rewatching and update progress.

UPDATE_PROGRESS_WHEN_REWATCHING: Boolean. If true, allow updating progress for anime set to rewatching. This is for if you want to set anime to rewatching manually, but still update progress automatically.

SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT: Boolean. If true, set to COMPLETED after last episode if status was CURRENT.

SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING: Boolean. If true, set to COMPLETED after last episode if status was REPEATING (rewatching).

ADD_ENTRY_IF_MISSING: Boolean. If true, automatically add anime to your list if it's not found during search. Default is false.

SILENT_MODE: Boolean. If true, won't show OSD messages.
]]

local utils = require 'mp.utils'
local mpoptions = require("mp.options")

local conf_name = "anilistUpdater.conf"
local script_dir = (debug.getinfo(1).source:match("@?(.*/)") or "./")

-- Helper function to get MPV config directory
local function get_mpv_config_dir()
    return os.getenv("APPDATA") and utils.join_path(os.getenv("APPDATA"), "mpv") or 
           os.getenv("HOME") and utils.join_path(utils.join_path(os.getenv("HOME"), ".config"), "mpv") or nil
end

-- Helper function to normalize path separators
local function normalize_path(p)
    p = p:gsub("\\", "/")
    if p:sub(-1) == "/" then
        p = p:sub(1, -2)
    end
    return p
end

-- Helper function to parse directory strings (comma or semicolon separated)
local function parse_directory_string(dir_string)
    if type(dir_string) == "string" and dir_string ~= "" then
        local dirs = {}
        for dir in string.gmatch(dir_string, "([^,;]+)") do
            local trimmed = (dir:gsub("^%s*(.-)%s*$", "%1"):gsub('[\'"]', '')) -- trim
            table.insert(dirs, normalize_path(trimmed))
        end
        return dirs
    else
        return {}
    end
end

-- Default config
local default_conf = [[# anilistUpdater Configuration
# For detailed explanations of all available options, see:
# https://github.com/AzuredBlue/mpv-anilist-updater?tab=readme-ov-file#configuration-anilistupdaterconf

# Use 'yes' or 'no' for boolean options below
# Example for multiple directories (comma or semicolon separated):
# DIRECTORIES=D:/Torrents,D:/Anime
# or
# DIRECTORIES=D:/Torrents;D:/Anime
DIRECTORIES=
EXCLUDED_DIRECTORIES=
UPDATE_PERCENTAGE=85
SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE=no
UPDATE_PROGRESS_WHEN_REWATCHING=yes
SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT=yes
SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING=yes
ADD_ENTRY_IF_MISSING=no
SILENT_MODE=no
]]

-- Try script-opts directory (sibling to scripts)
local script_opts_dir = script_dir:match("^(.-)[/\\]scripts[/\\]")

if script_opts_dir then
    script_opts_dir = utils.join_path(script_opts_dir, "script-opts")
else
    -- Fallback: try to find mpv config dir
    local mpv_conf_dir = get_mpv_config_dir()
    script_opts_dir = mpv_conf_dir and utils.join_path(mpv_conf_dir, "script-opts") or nil
end

local script_opts_path = script_opts_dir and utils.join_path(script_opts_dir, conf_name) or nil

-- Try script directory
local script_path = utils.join_path(script_dir, conf_name)

-- Try mpv config directory
local mpv_conf_dir = get_mpv_config_dir()
local mpv_conf_path = mpv_conf_dir and utils.join_path(mpv_conf_dir, conf_name) or nil

local conf_paths = {script_opts_path, script_path, mpv_conf_path}

-- Try to find config file
local conf_path = nil
for _, path in ipairs(conf_paths) do
    if path then
        local f = io.open(path, "r")
        if f then
            f:close()
            conf_path = path
            -- print("Found config at: " .. path)
            break
        end
    end
end

-- If not found, try to create in order
if not conf_path then
    for _, path in ipairs(conf_paths) do
        if path then
            local f = io.open(path, "w")
            if f then
                f:write(default_conf)
                f:close()
                conf_path = path
                -- print("Created config at: " .. path)
                break
            end
        end
    end
end

-- If still not found or created, warn and use defaults
if not conf_path then
    mp.msg.warn("Could not find or create anilistUpdater.conf in any known location! Using default options.")
end

-- Initialize options with defaults
local options = {
    DIRECTORIES = "",
    EXCLUDED_DIRECTORIES = "",
    UPDATE_PERCENTAGE = 85,
    SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE = false,
    UPDATE_PROGRESS_WHEN_REWATCHING = true,
    SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT = true,
    SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING = true,
    ADD_ENTRY_IF_MISSING = false,
    SILENT_MODE = false
}

-- Override defaults with values from config file
mpoptions.read_options(options, "anilistUpdater")

-- Parse DIRECTORIES and EXCLUDED_DIRECTORIES using helper function
options.DIRECTORIES = parse_directory_string(options.DIRECTORIES)
options.EXCLUDED_DIRECTORIES = parse_directory_string(options.EXCLUDED_DIRECTORIES)

-- When calling Python, pass only the options relevant to it
local python_options = {
    SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE = options.SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE,
    UPDATE_PROGRESS_WHEN_REWATCHING = options.UPDATE_PROGRESS_WHEN_REWATCHING,
    SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT = options.SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT,
    SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING = options.SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING,
    ADD_ENTRY_IF_MISSING = options.ADD_ENTRY_IF_MISSING
}
local python_options_json = utils.format_json(python_options)

DIRECTORIES = options.DIRECTORIES
EXCLUDED_DIRECTORIES = options.EXCLUDED_DIRECTORIES
UPDATE_PERCENTAGE = tonumber(options.UPDATE_PERCENTAGE) or 85

local function path_starts_with_any(path, directories)
    local norm_path = normalize_path(path)
    for _, dir in ipairs(directories) do
        if norm_path:sub(1, #dir) == dir then
            return true
        end
    end
    return false
end

function callback(success, result, error)

    if options.SILENT_MODE then return end
    
    -- Can send multiple OSD messages to display
    local messages = {}
    if result and result.stdout then
        for line in result.stdout:gmatch("[^\r\n]+") do
            local msg = line:match("^OSD:%s*(.-)%s*$")
            if msg then
                table.insert(messages, msg)
            else
                print(line)
            end
        end
    end
    

    if success and result and result.status == 0 then
        if #messages == 0 then
            table.insert(messages, "Updated anime correctly.")
        end
    end

    if #messages > 0 then
        mp.osd_message(table.concat(messages, "\n"), 5)
    end
end

local function get_python_command()
    local os_name = package.config:sub(1, 1)
    if os_name == '\\' then
        -- Windows
        return "python"
    else
        -- Linux
        return "python3"
    end
end

-- Helper function to detect ani-cli compatibility
local function is_ani_cli_compatible()
    local directory = mp.get_property("working-directory") or ""
    local file_path = mp.get_property("path") or ""
    local full_path = utils.join_path(directory, file_path)
    
    -- Auto-detect ani-cli compatibility by checking for http:// or https:// anywhere in the path
    return full_path:match("https?://") ~= nil
end

local function get_path()
    local directory = mp.get_property("working-directory")
    -- It seems like in Linux working-directory sometimes returns it without a "/" at the end
    directory = (directory:sub(-1) == '/' or directory:sub(-1) == '\\') and directory or directory .. '/'
    -- For some reason, "path" sometimes returns the absolute path, sometimes it doesn't.
    local file_path = mp.get_property("path")
    local path = utils.join_path(directory, file_path)

    -- Auto-detect ani-cli compatibility by checking for http:// or https:// anywhere in the path
    if path:match("https?://") then
        local media_title = mp.get_property("media-title")
        if media_title and media_title ~= "" then
            return media_title
        end
    end

    if path:match("([^/\\]+)$"):lower() == "file.mp4" then
        path = mp.get_property("media-title")
    end

    return path
end

local python_command = get_python_command()

local isPaused = false
local is_file_eligible = false

-- Make sure it doesnt trigger twice in 1 video
local triggered = false
-- Check progress every X seconds (when not paused)
local UPDATE_INTERVAL = 1

-- Initialize timer once - we control it with stop/resume
local progress_timer = mp.add_periodic_timer(UPDATE_INTERVAL, function()
    if triggered then
        return
    end
    
    local percent_pos = mp.get_property_number("percent-pos")
    if not percent_pos then
        return
    end

    if percent_pos >= UPDATE_PERCENTAGE then
        update_anilist("update")
        triggered = true
        if progress_timer then
            progress_timer:stop()
        end
        return
    end
end)
-- Start with timer stopped - it will be started when a valid file loads
progress_timer:stop()

-- Handle pause/unpause events to control the timer
function on_pause_change(name, value)
    isPaused = value
    if value then
        progress_timer:stop()
    else
        if is_file_eligible and not triggered then
            progress_timer:resume()
        end
    end
end

-- Function to launch the .py script
function update_anilist(action)
    if action == "launch" and not options.SILENT_MODE then
        mp.osd_message("Launching AniList", 2)
    end
    local script_dir = debug.getinfo(1).source:match("@?(.*/)")

    local path = get_path()

    local table = {}
    table.name = "subprocess"
    table.args = {python_command, script_dir .. "anilistUpdater.py", path, action, python_options_json}
    table.capture_stdout = true
    local cmd = mp.command_native_async(table, callback)
end

mp.observe_property("pause", "bool", on_pause_change)

-- Reset triggered and start/stop timer based on file loading
mp.register_event("file-loaded", function()
    triggered = false
    is_file_eligible = false
    progress_timer:stop()

    if not is_ani_cli_compatible() and #DIRECTORIES > 0 then
        local path = get_path()

        if not path_starts_with_any(path, DIRECTORIES) then
            return
        else
            -- If it starts with the directories, check if it starts with any of the excluded directories
            if #EXCLUDED_DIRECTORIES > 0 and path_starts_with_any(path, EXCLUDED_DIRECTORIES) then
                return
            end
        end
    end

    is_file_eligible = true

    -- Start timer for this file
    if not isPaused then
        progress_timer:resume()
    end
end)

-- Default keybinds - can be customized in input.conf using script-binding commands
mp.add_key_binding("ctrl+a", 'update_anilist', function()
    update_anilist("update")
end)

mp.add_key_binding("ctrl+b", 'launch_anilist', function()
    update_anilist("launch")
end)

-- Open the folder that the video is
function open_folder()
    local path = mp.get_property("path")
    local directory

    if not path then
        mp.msg.warn("No file is currently playing.")
        return
    end

    if path:find('\\') then
        directory = path:match("(.*)\\")
    elseif path:find('\\\\') then
        directory = path:match("(.*)\\\\")
    else
        directory = mp.get_property("working-directory")
    end

    -- Use the system command to open the folder in File Explorer
    local args
    if package.config:sub(1, 1) == '\\' then
        -- Windows
        args = {'explorer', directory}
    elseif os.getenv("XDG_CURRENT_DESKTOP") or os.getenv("WAYLAND_DISPLAY") or os.getenv("DISPLAY") then
        -- Linux (assume a desktop environment like GNOME, KDE, etc.)
        args = {'xdg-open', directory}
    elseif package.config:sub(1, 1) == '/' then
        -- macOS
        args = {'open', directory}
    end

    mp.command_native({
        name = "subprocess",
        args = args,
        detach = true
    })
end

mp.add_key_binding("ctrl+d", 'open_folder', open_folder)

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

local function parse_detected_info(result)
    if not result or not result.stdout then
        return nil
    end

    for line in result.stdout:gmatch("[^\r\n]+") do
        local json_part = line:match("^INFO:%s*(.+)$")
        if json_part then
            local info = utils.parse_json(json_part)
            if info and type(info) == "table" then
                return info
            end
        end
    end

    return nil
end

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
    local args

    if package.config:sub(1, 1) == '\\' then
        args = {"powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"}
    elseif os.getenv("OSTYPE") == "darwin" then
        args = {"pbpaste"}
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
    }, callback)
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

local function correct_anime_id()
    local path = get_path()
    local s_dir = debug.getinfo(1).source:match("@?(.*/)")

    mp.osd_message("Loading detected anime info...", 2)

    mp.command_native_async({
        name = "subprocess",
        args = {python_command, s_dir .. "anilistUpdater.py", path, "info", python_options_json},
        capture_stdout = true
    }, function(success, result)
        local detected = nil
        if success and result and result.status == 0 then
            detected = parse_detected_info(result)
        end

        open_correction_overlay(path, s_dir, detected)
    end)
end

mp.add_key_binding("c", 'correct_anime_id', correct_anime_id)
