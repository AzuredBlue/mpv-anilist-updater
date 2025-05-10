local utils = require 'mp.utils'

-- === USER CONFIGURABLE OPTIONS ===
-- The directories the script will work on.
-- Specify as a table of strings. Leaving it empty will make it work on every video you watch with mpv.
-- You can still update manually via Ctrl+A.
-- Example: DIRECTORIES = {"D:/Torrents", "D:/Anime"}
DIRECTORIES = {}
-- ================================

local function path_starts_with_any(path, directories)
    for _, dir in ipairs(directories) do
        if path:sub(1, #dir) == dir then
            return true
        end
    end
    return false
end

function callback(success, result, error)
    if result.status == 0 then
        mp.osd_message("Updated anime correctly.", 2)
    end
end

local function get_python_command()
    local os_name = package.config:sub(1,1)
    if os_name == '\\' then
        -- Windows
        return "python"
    else
        -- Linux
        return "python3"
    end
end

local python_command = get_python_command()

-- Make sure it doesnt trigger twice in 1 video
local triggered = false

-- Function to check if we've reached 85% of the video
function check_progress()
    if triggered then return end

    local percent_pos = mp.get_property_number("percent-pos")
    
    if percent_pos then
        if percent_pos >= 85 then
            update_anilist("update")
            triggered = true
        end
    end
end

-- Function to launch the .py script
function update_anilist(action)
    if action == "launch" then mp.osd_message("Launching AniList", 2) end
    local script_dir = debug.getinfo(1).source:match("@?(.*/)")
    local directory = mp.get_property("working-directory")
    -- It seems like in Linux working-directory sometimes returns it without a "/" at the end
    directory = (directory:sub(-1) == '/' or directory:sub(-1) == '\\') and directory or directory .. '/'
    -- For some reason, "path" sometimes returns the absolute path, sometimes it doesn't.
    local file_path = mp.get_property("path")
    local path = utils.join_path(directory, file_path)

    local table = {}
    table.name = "subprocess"
    table.args = {python_command, script_dir.."anilistUpdater.py", path, action}
    local cmd = mp.command_native_async(table, callback)
end

mp.observe_property("percent-pos", "number", check_progress)

-- Reset triggered
mp.register_event("file-loaded", function()
    triggered = false
    if #DIRECTORIES > 0 then
        local directory = mp.get_property("working-directory")
        directory = (directory:sub(-1) == '/' or directory:sub(-1) == '\\') and directory or directory .. '/'
        local file_path = mp.get_property("path")
        local path = utils.join_path(directory, file_path)
        path = path:gsub("\\", "/")

        if not path_starts_with_any(path, DIRECTORIES) then
            mp.unobserve_property(check_progress)
        end
    end
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
    if package.config:sub(1,1) == '\\' then
        -- Windows
        args = { 'explorer', directory }
    elseif os.getenv("XDG_CURRENT_DESKTOP") or os.getenv("WAYLAND_DISPLAY") or os.getenv("DISPLAY") then
        -- Linux (assume a desktop environment like GNOME, KDE, etc.)
        args = { 'xdg-open', directory }
    elseif package.config:sub(1,1) == '/' then
        -- macOS
        args = { 'open', directory }
    end

    mp.command_native({ name = "subprocess", args = args, detach = true })
end

mp.add_key_binding('ctrl+d', 'open_folder', open_folder)