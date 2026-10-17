local msg = require("mp.msg")
local config = require("lua.config")
local backend = require("lua.backend")
local overlay = require("lua.overlay")

local silent = config.options.SILENT_MODE

local state = {
    info = nil, -- detected anime for the current file
    generation = 0, -- bumped per file, so late responses for a previous file are dropped
    waiting = {}, -- callbacks for the detection in flight
    fetching = false,
}

local function osd(text, seconds)
    if not silent then
        mp.osd_message(text, seconds or 3)
    end
end

-- Show a backend response. Failures to find or update something are only worth interrupting for
-- when the user asked; real errors always are.
local function notify(response, manual)
    local kind = response.kind
    if kind == "cancelled" then
        return
    elseif kind == "error" then
        mp.osd_message(response.message, 5)
    elseif kind == "skip" then
        if manual then
            osd(response.message, 5)
        end
    elseif response.ok then
        osd(response.message or "Updated anime correctly.", 5)
    end
end

-- Detect the anime once; everyone who asks meanwhile shares the same request.
-- callback(info, response) gets a nil info when detection failed.
local function ensure_info(callback)
    if state.info then
        return callback and callback(state.info)
    end
    table.insert(state.waiting, callback or function() end)
    if state.fetching then
        return
    end

    state.fetching = true
    local generation = state.generation
    backend.call("info", {}, function(response)
        if generation ~= state.generation then
            return
        end
        state.fetching = false
        state.info = response.ok and response.info or nil
        if state.info then
            msg.info("Detected anime: " .. (state.info.anime_name or "?") .. " #" .. (state.info.episode or "?"))
        end

        local waiting = state.waiting
        state.waiting = {}
        for _, waiter in ipairs(waiting) do
            waiter(state.info, response)
        end
    end)
end

local function update_anilist(manual)
    if manual then
        osd("Updating AniList...", 2)
    end

    ensure_info(function(info, response)
        if not info then
            if response and response.kind ~= "cancelled" and response.message then
                notify(response, manual)
            elseif manual then
                mp.osd_message("Error: Anime info not loaded yet.", 3)
            end
            return
        end

        local generation = state.generation
        backend.call("update", { info = info }, function(result)
            if result.ok and result.info and generation == state.generation then
                state.info = result.info
            end
            notify(result, manual)
        end)
    end)
end

local function open(target, folder)
    local platform = mp.get_property("platform")
    local args = { "xdg-open", target }
    if platform == "windows" then
        -- start would read & and ^ in a folder name as cmd syntax
        args = folder and { "explorer", target } or { "cmd", "/c", "start", "", target }
    elseif platform == "darwin" then
        args = { "open", target }
    end
    mp.command_native({ name = "subprocess", args = args, detach = true })
end

local function launch_anilist()
    ensure_info(function(info)
        if info and info.anime_id then
            osd('Opening AniList for "' .. (info.anime_name or "?") .. '"')
            open("https://anilist.co/anime/" .. info.anime_id)
        else
            mp.osd_message("Error: Anime info not loaded yet.", 3)
        end
    end)
end

local function open_folder()
    local path = mp.get_property("path")
    if not path then
        return msg.warn("No file is currently playing.")
    elseif path:match("^%a+://") then
        return mp.osd_message("Not a local file.", 2)
    end
    open(path:match("(.+)[/\\]") or mp.get_property("working-directory"), true)
end

-- Update once the video is far enough along. percent-pos doesn't move while paused.
local progress_timer
progress_timer = mp.add_periodic_timer(1, function()
    local percent = mp.get_property_number("percent-pos")
    if percent and percent >= config.update_percentage then
        progress_timer:kill()
        update_anilist(false)
    end
end)
progress_timer:kill()

mp.register_event("file-loaded", function()
    progress_timer:kill()
    state.generation = state.generation + 1
    state.info, state.waiting, state.fetching = nil, {}, false

    if not backend.is_ani_cli() then
        local path = backend.get_path()
        if not path or (mp.get_property("path") or ""):match("^%a+://") or not config.is_eligible(path) then
            return
        end
    end

    -- Detect early so the update and the keybinds have the info ready
    ensure_info()
    progress_timer:resume()
end)

mp.register_event("end-file", function()
    progress_timer:kill()
end)

mp.add_key_binding("ctrl+a", "update_anilist", function() update_anilist(true) end)
mp.add_key_binding("ctrl+b", "launch_anilist", launch_anilist)
mp.add_key_binding("ctrl+d", "open_folder", open_folder)

overlay.init({
    backend = backend,
    notify = notify,
    ensure_info = ensure_info,
    generation = function() return state.generation end,
    set_info = function(info) state.info = info end,
})
