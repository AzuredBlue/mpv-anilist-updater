local utils = require 'mp.utils'

function callback(success, result, error)
    if result.status == 0 then
        mp.osd_message("Updated anime correctly.", 1)
    else
        mp.osd_message("Could not detect anime.", 3)
    end
end

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
            update_anilist()
            triggered = true
        end
    end
end

-- Function to update anilist. Displays an OSD message when 85% of the video has played.
function update_anilist()
    local script_dir = debug.getinfo(1).source:match("@?(.*/)")
    local path = mp.get_property("working-directory") .. mp.get_property("path") -- Absolute path of the file we are playing
    local table = {}
    table.name = "subprocess"
    table.args = {"python", script_dir.."anilistUpdater.py", path}
    local cmd = mp.command_native_async(table, callback)
end

-- Checks progress every second
mp.observe_property("time-pos", "number", check_progress)

-- Reset triggered
mp.register_event("file-loaded", function()
    triggered = false
end)

-- Keybind, modify as you please
mp.add_key_binding('ctrl+a', 'update_anilist', update_anilist)