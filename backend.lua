local msg = require("mp.msg")
local utils = require("mp.utils")
local config = require("config")

local M = {}

local python = mp.get_property("platform") == "windows" and "python" or "python3"
local entry_point = config.script_dir .. "anilistUpdater.py"

-- ani-cli plays remote streams, so the filename means nothing and the media title is used instead.
function M.is_ani_cli()
    local full_path = utils.join_path(mp.get_property("working-directory") or "", mp.get_property("path") or "")
    return full_path:match("https?://") ~= nil and not full_path:find("127.0.0.1", 1, true)
end

-- What identifies the current file to the backend.
function M.get_path()
    local title = mp.get_property("media-title")
    if M.is_ani_cli() and title and title ~= "" then
        return title
    end

    -- Linux can report the working directory without a trailing slash
    local directory = mp.get_property("working-directory") or ""
    if not directory:find("[/\\]$") then
        directory = directory .. "/"
    end
    -- "path" is sometimes absolute and sometimes relative
    local path = utils.join_path(directory, mp.get_property("path") or "")

    if (path:match("([^/\\]+)$") or ""):lower() == "file.mp4" then
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
        return { ok = false, kind = "error", message = "Couldn't run " .. python .. "." }
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

    mp.command_native_async({
        name = "subprocess",
        args = { python, entry_point },
        stdin_data = utils.format_json(request),
        capture_stdout = true,
        capture_stderr = true,
        -- Detection is moot once the file changes, but a write must finish.
        playback_only = action == "info",
    }, function(success, result)
        for line in ((success and result and result.stderr) or ""):gmatch("[^\r\n]+") do
            msg.info(line)
        end
        callback(decode(success, result))
    end)
end

return M
