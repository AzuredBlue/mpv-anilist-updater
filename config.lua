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

CACHE_REFRESH_RATE: Number (hours). How long a normal cache entry stays valid. Default is 24.

CACHE_MODE: String. Either NORMAL or SLIDING. NORMAL keeps current behavior. SLIDING refreshes cache TTL on reads for all titles.

SILENT_MODE: Boolean. If true, won't show OSD messages.
]]

local mp_options = require("mp.options")
local msg = require("mp.msg")
local utils = require("mp.utils")

local M = {}

local CONF_NAME = "anilistUpdater.conf"

M.script_dir = debug.getinfo(1, "S").source:match("^@?(.*[/\\])") or "./"

local DEFAULT_CONF = [[# anilistUpdater Configuration
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
CACHE_REFRESH_RATE=24
CACHE_MODE=NORMAL
SILENT_MODE=no
]]

-- The defaults for every option. The Python side takes whatever is sent and has no defaults of its own.
local options = {
    DIRECTORIES = "",
    EXCLUDED_DIRECTORIES = "",
    UPDATE_PERCENTAGE = 85,
    SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE = false,
    UPDATE_PROGRESS_WHEN_REWATCHING = true,
    SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT = true,
    SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING = true,
    ADD_ENTRY_IF_MISSING = false,
    CACHE_REFRESH_RATE = 24,
    CACHE_MODE = "NORMAL",
    SILENT_MODE = false,
}

local function expand(path)
    return mp.command_native({ "expand-path", path })
end

local function readable(path)
    local file = io.open(path, "r")
    if file then
        file:close()
    end
    return file ~= nil
end

-- Where the config may live, best first. mpv itself only reads the first one.
local function candidate_paths()
    return {
        utils.join_path(expand("~~/script-opts"), CONF_NAME),
        utils.join_path(M.script_dir, CONF_NAME),
        utils.join_path(expand("~~/"), CONF_NAME),
    }
end

local function find_or_create_conf()
    local paths = candidate_paths()
    for _, path in ipairs(paths) do
        if readable(path) then
            return path
        end
    end

    for _, path in ipairs(paths) do
        local file = io.open(path, "w")
        if file then
            file:write(DEFAULT_CONF)
            file:close()
            return path
        end
    end

    msg.warn("Could not find or create " .. CONF_NAME .. " in any known location! Using default options.")
end

local function coerce(default, raw)
    if type(default) == "boolean" then
        return raw == "yes" or raw == "true"
    elseif type(default) == "number" then
        return tonumber(raw) or default
    end
    return raw
end

-- mp.options only reads script-opts, so a config found elsewhere is applied by hand.
local function read_conf(path)
    for line in io.lines(path) do
        local key, raw = line:match("^%s*([%w_]+)%s*=%s*(.-)%s*$")
        if key and options[key] ~= nil then
            options[key] = coerce(options[key], raw)
        end
    end
end

local function normalize(path)
    return (path:gsub("\\", "/"):gsub("/$", ""))
end

local function parse_directories(value)
    local directories = {}
    for entry in tostring(value):gmatch("[^,;]+") do
        local dir = entry:gsub("^%s+", ""):gsub("%s+$", ""):gsub("['\"]", "")
        directories[#directories + 1] = normalize(dir)
    end
    return directories
end

local conf_path = find_or_create_conf()
if conf_path and conf_path ~= candidate_paths()[1] then
    read_conf(conf_path)
end
mp_options.read_options(options, "anilistUpdater")

local directories = parse_directories(options.DIRECTORIES)
local excluded = parse_directories(options.EXCLUDED_DIRECTORIES)

M.options = options
M.update_percentage = tonumber(options.UPDATE_PERCENTAGE) or 85

-- Only what Python needs, every key always present.
M.python_options = {
    SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE = options.SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE,
    UPDATE_PROGRESS_WHEN_REWATCHING = options.UPDATE_PROGRESS_WHEN_REWATCHING,
    SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT = options.SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT,
    SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING = options.SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING,
    ADD_ENTRY_IF_MISSING = options.ADD_ENTRY_IF_MISSING,
    CACHE_REFRESH_RATE = tonumber(options.CACHE_REFRESH_RATE) or 24,
    CACHE_MODE = tostring(options.CACHE_MODE),
}

local function within(path, dirs)
    for _, dir in ipairs(dirs) do
        if path == dir or path:sub(1, #dir + 1) == dir .. "/" then
            return true
        end
    end
    return false
end

-- Whether a file should be tracked automatically. Keybinds work for any file.
function M.is_eligible(path)
    path = normalize(path)
    return (#directories == 0 or within(path, directories)) and not within(path, excluded)
end

return M
