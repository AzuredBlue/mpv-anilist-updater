--[[
Configuration options for anilistUpdater (set in anilistUpdater.conf):

DIRECTORIES: Table or comma/semicolon-separated string. The directories the script will work on. Leaving it empty will make it work on every video you watch with mpv. Example: DIRECTORIES = {"D:/Torrents", "D:/Anime"}

EXCLUDED_DIRECTORIES: Table or comma/semicolon-separated string. Useful for ignoring paths inside directories from above. Example: EXCLUDED_DIRECTORIES = {"D:/Torrents/Watched", "D:/Anime/Planned"}

UPDATE_PERCENTAGE: Number (0-100). The percentage of the video you need to watch before it updates AniList automatically. Default is 85 (usually before the ED of a usual episode duration).

SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE: Boolean. If true, when watching episode 1 of a completed anime, set it to rewatching and update progress.

UPDATE_PROGRESS_WHEN_REWATCHING: Boolean. If true, allow updating progress for anime set to rewatching. This is for if you want to set anime to rewatching manually, but still update progress automatically.

SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT: Boolean. If true, set to COMPLETED after last episode if status was CURRENT.

SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING: Boolean. If true, set to COMPLETED after last episode if status was REPEATING (rewatching).
]]

local utils = require 'mp.utils'
local mpoptions = require("mp.options")

local conf_name = "anilistUpdater.conf"
local script_dir = (debug.getinfo(1).source:match("@?(.*/)") or "./")

-- Try script-opts directory (sibling to scripts)
local script_opts_dir = script_dir:match("^(.-)[/\\]scripts[/\\]")

if script_opts_dir then
    script_opts_dir = utils.join_path(script_opts_dir, "script-opts")
else
    -- Fallback: try to find mpv config dir
    script_opts_dir = os.getenv("APPDATA") and
                          utils.join_path(utils.join_path(os.getenv("APPDATA"), "mpv"), "script-opts") or
                          os.getenv("HOME") and
                          utils.join_path(utils.join_path(utils.join_path(os.getenv("HOME"), ".config"), "mpv"),
            "script-opts") or nil
end

local script_opts_path = script_opts_dir and utils.join_path(script_opts_dir, conf_name) or nil

-- Try script directory
local script_path = utils.join_path(script_dir, conf_name)

-- Try mpv config directory
local mpv_conf_dir = os.getenv("APPDATA") and utils.join_path(os.getenv("APPDATA"), "mpv") or os.getenv("HOME") and
                         utils.join_path(utils.join_path(os.getenv("HOME"), ".config"), "mpv") or nil
local mpv_conf_path = mpv_conf_dir and utils.join_path(mpv_conf_dir, conf_name) or nil

local conf_paths = {script_opts_path, script_path, mpv_conf_path}

local default_conf = [[
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
]]

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
                print("Created config at: " .. path)
                break
            end
        end
    end
end

-- If still not found or created, warn and use defaults
if not conf_path then
    mp.msg.warn("Could not find or create anilistUpdater.conf in any known location! Using default options.")
end

-- Now load options as usual
local options = {
    DIRECTORIES = "",
    EXCLUDED_DIRECTORIES = "",
    UPDATE_PERCENTAGE = 85,
    SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE = false,
    UPDATE_PROGRESS_WHEN_REWATCHING = true,
    SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT = true,
    SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING = true
}
if conf_path then
    mpoptions.read_options(options, "anilistUpdater")
end

local function normalize_path(p)
    p = p:gsub("\\", "/")
    if p:sub(-1) == "/" then
        p = p:sub(1, -2)
    end
    return p
end

-- Parse DIRECTORIES if it's a string (comma or semicolon separated)
if type(options.DIRECTORIES) == "string" and options.DIRECTORIES ~= "" then
    local dirs = {}
    for dir in string.gmatch(options.DIRECTORIES, "([^,;]+)") do
        local trimmed = (dir:gsub("^%s*(.-)%s*$", "%1"):gsub('[\'"]', '')) -- trim
        table.insert(dirs, normalize_path(trimmed))
    end
    options.DIRECTORIES = dirs
elseif type(options.DIRECTORIES) == "string" then
    options.DIRECTORIES = {}
end

if type(options.EXCLUDED_DIRECTORIES) == "string" and options.EXCLUDED_DIRECTORIES ~= "" then
    local dirs = {}
    for dir in string.gmatch(options.EXCLUDED_DIRECTORIES, "([^,;]+)") do
        local trimmed = (dir:gsub("^%s*(.-)%s*$", "%1"):gsub('[\'"]', '')) -- trim
        table.insert(dirs, normalize_path(trimmed))
    end
    options.EXCLUDED_DIRECTORIES = dirs
elseif type(options.EXCLUDED_DIRECTORIES) == "string" then
    options.EXCLUDED_DIRECTORIES = {}
end

-- When calling Python, pass only the options relevant to it
local python_options = {
    SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE = options.SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE,
    UPDATE_PROGRESS_WHEN_REWATCHING = options.UPDATE_PROGRESS_WHEN_REWATCHING,
    SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT = options.SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT,
    SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING = options.SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING
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
    if result.status == 0 then
        mp.osd_message("Updated anime correctly.", 4)
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

local function get_path()
    local directory = mp.get_property("working-directory")
    -- It seems like in Linux working-directory sometimes returns it without a "/" at the end
    directory = (directory:sub(-1) == '/' or directory:sub(-1) == '\\') and directory or directory .. '/'
    -- For some reason, "path" sometimes returns the absolute path, sometimes it doesn't.
    local file_path = mp.get_property("path")
    local path = utils.join_path(directory, file_path)

    if path:match("([^/\\]+)$"):lower() == "file.mp4" then
        path = mp.get_property("media-title")
    end

    return path
end

local python_command = get_python_command()

-- Make sure it doesnt trigger twice in 1 video
local triggered = false
-- Check progress every X seconds (when not paused)
local UPDATE_INTERVAL = 0.5

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
    if value then
        progress_timer:stop()
    else
        if not triggered then
            progress_timer:resume()
        end
    end
end

-- Function to launch the .py script
function update_anilist(action)
    if action == "launch" then
        mp.osd_message("Launching AniList", 2)
    end
    local script_dir = debug.getinfo(1).source:match("@?(.*/)")

    local path = get_path()

    local table = {}
    table.name = "subprocess"
    table.args = {python_command, script_dir .. "anilistUpdater.py", path, action, python_options_json}
    local cmd = mp.command_native_async(table, callback)
end

mp.observe_property("pause", "bool", on_pause_change)

-- Reset triggered and start/stop timer based on file loading
mp.register_event("file-loaded", function()
    triggered = false
    progress_timer:stop()

    if #DIRECTORIES > 0 then
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

    -- Start timer for this file
    progress_timer:resume()
end)

-- Keybinds, modify as you please
mp.add_key_binding('ctrl+a', 'update_anilist', function()
    update_anilist("update")
end)

mp.add_key_binding('ctrl+b', 'launch_anilist', function()
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

mp.add_key_binding('ctrl+d', 'open_folder', open_folder)
