-- GARNT TOOL REJOIN heartbeat (auto-generated)
-- For your own accounts and devices only. Must run through an executor that supports HTTP
local HOST     = "http://127.0.0.1:8765"
local ID       = "com.roblox.client"       -- package name or instance number; must match the tool
local INTERVAL = 15   -- send every N seconds

if getgenv then
    if getgenv().__GARNT_HB then return end
    getgenv().__GARNT_HB = true
end

if not game:IsLoaded() then game.Loaded:Wait() end
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")
local HttpService = game:GetService("HttpService")
local GuiService = game:GetService("GuiService")

local lp = Players.LocalPlayer
while not lp do task.wait(1); lp = Players.LocalPlayer end

local lastFrame = os.clock()
RunService.Heartbeat:Connect(function() lastFrame = os.clock() end)

local function httpGet(url)
    local req = (syn and syn.request) or http_request or request or (http and http.request)
    if req then
        local ok = pcall(req, { Url = url, Method = "GET" })
        if ok then return true end
    end
    return (pcall(function() return game:HttpGet(url) end))
end

local function errorText()
    local ok, msg = pcall(function() return GuiService:GetErrorMessage() end)
    if ok and type(msg) == "string" and msg ~= "" then return msg end
    return nil
end

local function beat()
    if os.clock() - lastFrame > 5 then return end   -- game frozen: do not send
    local url = string.format("%s/hb?id=%s&user=%s&uid=%d&place=%d&job=%s", HOST,
        HttpService:UrlEncode(ID), HttpService:UrlEncode(lp.Name), lp.UserId, game.PlaceId,
        HttpService:UrlEncode(game.JobId))
    local err = errorText()                          -- Disconnected prompt showing: send the error text immediately
    if err then url = url .. "&err=" .. HttpService:UrlEncode(string.sub(err, 1, 120)) end
    httpGet(url)
end

pcall(function() GuiService.ErrorMessageChanged:Connect(function() pcall(beat) end) end)

task.spawn(function()
    while true do
        pcall(beat)
        task.wait(INTERVAL)
    end
end)
