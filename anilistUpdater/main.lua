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

ANI_CLI_COMPATIBILITY: Boolean. If true, use media title instead of file path for ani-cli compatibility. Ignores directory settings. Default is false.

KEYBIND_UPDATE_ANILIST: String. The keybind to manually update AniList. Default is "ctrl+a".

KEYBIND_LAUNCH_ANILIST: String. The keybind to open AniList page in browser. Default is "ctrl+b".

KEYBIND_OPEN_FOLDER: String. The keybind to open the folder containing the current video. Default is "ctrl+d".
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

-- Default configuration options
local default_options = {
    {key = "DIRECTORIES", value = "", config_value = ""},
    {key = "EXCLUDED_DIRECTORIES", value = "", config_value = ""},
    {key = "UPDATE_PERCENTAGE", value = 85, config_value = "85"},
    {key = "SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE", value = false, config_value = "no"},
    {key = "UPDATE_PROGRESS_WHEN_REWATCHING", value = true, config_value = "yes"},
    {key = "SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT", value = true, config_value = "yes"},
    {key = "SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING", value = true, config_value = "yes"},
    {key = "ADD_ENTRY_IF_MISSING", value = false, config_value = "no"},
    {key = "ANI_CLI_COMPATIBILITY", value = false, config_value = "no"},
    {key = "KEYBIND_UPDATE_ANILIST", value = "ctrl+a", config_value = "ctrl+a"},
    {key = "KEYBIND_LAUNCH_ANILIST", value = "ctrl+b", config_value = "ctrl+b"},
    {key = "KEYBIND_OPEN_FOLDER", value = "ctrl+d", config_value = "ctrl+d"}
}

-- Generate default config content
local function generate_default_conf()
    return [[# Use 'yes' or 'no' for boolean options below
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
ANI_CLI_COMPATIBILITY=no
KEYBIND_UPDATE_ANILIST=ctrl+a
KEYBIND_LAUNCH_ANILIST=ctrl+b
KEYBIND_OPEN_FOLDER=ctrl+d
]]
end

local default_conf = generate_default_conf()

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

-- Initialize options from default_options
local options = {}
for _, option in ipairs(default_options) do
    options[option.key] = option.value
end
if conf_path then
    -- Read the current config file content
    local current_config = ""
    local config_file = io.open(conf_path, "r")
    if config_file then
        current_config = config_file:read("*all")
        config_file:close()
    end
    
    -- This will override the defaults with values from the config file
    mpoptions.read_options(options, "anilistUpdater")
    
    -- Check for missing options and append them
    local missing_options = {}
    for _, option in ipairs(default_options) do
        if not current_config:find(option.key .. "=") then
            table.insert(missing_options, option.key .. "=" .. option.config_value)
        end
    end
    
    -- Append missing options
    if #missing_options > 0 then
        local append_file = io.open(conf_path, "a")
        if append_file then
            for _, option in ipairs(missing_options) do
                append_file:write(option .. "\n")
            end
            append_file:close()
        end
    end
end

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
    -- If ani-cli compatibility is enabled, use media title instead of file path
    if options.ANI_CLI_COMPATIBILITY then
        local media_title = mp.get_property("media-title")
        if media_title and media_title ~= "" then
            return media_title
        end
    end
    
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

local isPaused = false

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
    isPaused = value
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

    if not options.ANI_CLI_COMPATIBILITY and #DIRECTORIES > 0 then
        local path = get_path()

        if not path_starts_with_any(path, DIRECTORIES) then
            mp.unobserve_property(on_pause_change)
            return
        else
            -- If it starts with the directories, check if it starts with any of the excluded directories
            if #EXCLUDED_DIRECTORIES > 0 and path_starts_with_any(path, EXCLUDED_DIRECTORIES) then
                mp.unobserve_property(on_pause_change)
                return
            end
        end
    end

    -- Start timer for this file
    if not isPaused then
        progress_timer:resume()
    end
end)

-- Keybinds (configurable via anilistUpdater.conf)
mp.add_key_binding(options.KEYBIND_UPDATE_ANILIST, 'update_anilist', function()
    update_anilist("update")
end)

mp.add_key_binding(options.KEYBIND_LAUNCH_ANILIST, 'launch_anilist', function()
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

mp.add_key_binding(options.KEYBIND_OPEN_FOLDER, 'open_folder', open_folder)
