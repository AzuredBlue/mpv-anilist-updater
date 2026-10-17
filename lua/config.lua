-- Options are documented in the README (Configuration) and set in anilistUpdater.conf.

local mp_options = require("mp.options")
local msg = require("mp.msg")
local utils = require("mp.utils")

local M = {}

local CONF_NAME = "anilistUpdater.conf"

M.script_dir = (debug.getinfo(1, "S").source:match("^@?(.*[/\\])[^/\\]+[/\\]") or "./")

local CONF_HEADER = [[# anilistUpdater Configuration
# For detailed explanations of all available options, see:
# https://github.com/AzuredBlue/mpv-anilist-updater?tab=readme-ov-file#configuration-anilistupdaterconf

# Use 'yes' or 'no' for boolean options below
# Example for multiple directories (comma or semicolon separated):
# DIRECTORIES=D:/Torrents,D:/Anime
# or
# DIRECTORIES=D:/Torrents;D:/Anime
]]

-- Every option and its default, in the order of the generated .conf.
local DEFAULTS = {
    { "DIRECTORIES", "" },
    { "EXCLUDED_DIRECTORIES", "" },
    { "UPDATE_PERCENTAGE", 85 },
    { "SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE", false },
    { "UPDATE_PROGRESS_WHEN_REWATCHING", true },
    { "SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT", true },
    { "SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING", true },
    { "ADD_ENTRY_IF_MISSING", false },
    { "CACHE_REFRESH_RATE", 24 },
    { "CACHE_MODE", "NORMAL" },
    { "SILENT_MODE", false },
    { "SHOW_DETECTED_ANIME", false },
}

-- The Python side takes whatever is sent and has no defaults of its own, so it gets everything else.
local LUA_ONLY = {
    DIRECTORIES = true,
    EXCLUDED_DIRECTORIES = true,
    UPDATE_PERCENTAGE = true,
    SILENT_MODE = true,
    SHOW_DETECTED_ANIME = true,
}

local options, lines = {}, { CONF_HEADER:sub(1, -2) }
for _, default in ipairs(DEFAULTS) do
    local key, value = default[1], default[2]
    options[key] = value
    lines[#lines + 1] = key .. "=" .. (type(value) == "boolean" and (value and "yes" or "no") or tostring(value))
end
local DEFAULT_CONF = table.concat(lines, "\n") .. "\n"

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

local windows = mp.get_property("platform") == "windows"

local function normalize(path)
    path = path:gsub("\\", "/"):gsub("/$", "")
    return windows and path:lower() or path
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
M.update_percentage = options.UPDATE_PERCENTAGE

M.python_options = {}
for _, default in ipairs(DEFAULTS) do
    local key = default[1]
    if not LUA_ONLY[key] then
        M.python_options[key] = options[key]
    end
end

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
