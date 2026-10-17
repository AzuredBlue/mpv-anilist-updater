local msg = require("mp.msg")
local utils = require("mp.utils")
local config = require("lua.config")

local M = {}

-- Windows installs often only put the py launcher on PATH; `current` moves on once one can't be started.
local interpreters = { { "python3" } }
if mp.get_property("platform") == "windows" then
    interpreters = { { "python" }, { "py", "-3" } }
end
local current = 1
local entry_point = config.script_dir .. "anilistUpdater.py"

-- ani-cli plays remote streams, so the filename means nothing and the media title is used instead.
-- It names them with --force-media-title="<title> Episode <n>" on the command line.
function M.is_ani_cli()
    local title = mp.get_property("options/force-media-title") or ""
    return (mp.get_property("path") or ""):match("^%a+://") ~= nil
        and mp.get_property_bool("option-info/force-media-title/set-from-commandline", false)
        and title:find("Episode %d[%d.]*$") ~= nil
end

-- What identifies the current file to the backend.
function M.get_path()
    local title = mp.get_property("media-title")
    if M.is_ani_cli() and title and title ~= "" then
        -- a "/" would be read as a folder
        return (title:gsub("/", " "))
    end

    -- Linux can report the working directory without a trailing slash
    local directory = mp.get_property("working-directory") or ""
    if not directory:find("[/\\]$") then
        directory = directory .. "/"
    end
    -- "path" is sometimes absolute and sometimes relative
    local path = utils.join_path(directory, mp.get_property("path") or "")

    if (path:match("([^/\\]+)$") or ""):lower() == "file.mp4" and title and title ~= "" then
        return title
    end
    return path
end

local function last_line(text)
    local last
    for line in (text or ""):gmatch("[^\r\n]+") do
        last = line
    end
    return last
end

-- Always returns a response table; a crashed backend becomes an error response.
local function decode(success, result)
    if not success or not result then
        return { ok = false, kind = "error", message = "Couldn't run " .. interpreters[current][1] .. "." }
    end
    if result.killed_by_us then
        return { ok = false, kind = "cancelled" }
    end

    local response = utils.parse_json(result.stdout or "")
    if type(response) == "table" and response.kind then
        return response
    end

    local reason = last_line(result.stderr) or result.error_string or ("exit status " .. tostring(result.status))
    return { ok = false, kind = "error", message = reason }
end

-- Run an action in the Python backend. `callback` receives the response:
-- { ok, kind = "success" | "skip" | "error" | "cancelled", message, info }
function M.call(action, fields, callback)
    local request = { action = action, path = M.get_path(), options = config.python_options }
    for key, value in pairs(fields) do
        request[key] = value
    end

    local stdin_data = utils.format_json(request)

    local function run()
        local args = {}
        for _, part in ipairs(interpreters[current]) do
            args[#args + 1] = part
        end
        args[#args + 1] = entry_point

        mp.command_native_async({
            name = "subprocess",
            args = args,
            stdin_data = stdin_data,
            capture_stdout = true,
            capture_stderr = true,
            -- Detection is moot once the file changes, but a write must finish.
            playback_only = action == "info",
        }, function(success, result)
            if success and result and result.error_string == "init" and current < #interpreters then
                current = current + 1
                return run()
            end

            for line in ((success and result and result.stderr) or ""):gmatch("[^\r\n]+") do
                msg.info(line)
            end
            local response = decode(success, result)
            if response.message and response.kind ~= "cancelled" then
                (response.kind == "error" and msg.error or msg.info)(response.message)
            end
            callback(response)
        end)
    end

    run()
end

return M
