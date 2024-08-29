local utils = require 'mp.utils'

function callback(success, result, error)
    if result.status == 0 then
        mp.osd_message("Updated anime correctly.", 1)
    else
        mp.osd_message("Did not update anime.", 3)
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

    local duration = mp.get_property_number("duration")
    local position = mp.get_property_number("time-pos")
    
    if duration and position then
        local progress = position / duration
        if progress >= 0.85 then
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
    local path = ((directory:sub(-1) == '/' or directory:sub(-1) == '\\') and directory or directory..'/') .. mp.get_property("path") -- Absolute path of the file we are playing
    local table = {}
    table.name = "subprocess"
    table.args = {python_command, script_dir.."anilistUpdater.py", path, action}
    local cmd = mp.command_native_async(table, callback)
end

-- Checks progress every second
mp.observe_property("time-pos", "number", check_progress)

-- Reset triggered
mp.register_event("file-loaded", function()
    triggered = false
end)

-- Keybinds, modify as you please
mp.add_key_binding('ctrl+a', 'update_anilist', function()
    update_anilist("update")
end)

mp.add_key_binding('ctrl+b', 'launch_anilist', function()
    update_anilist("launch")
end)