-- GARNT TOOL REJOIN heartbeat (auto-generated). ID = com.roblox.client
-- WARNING: executors may violate Roblox rules and risk account bans. Use at your own risk.
local ID = "com.roblox.client"
local PORT = 8765
local INTERVAL = 15
local BASE = "http://127.0.0.1:" .. tostring(PORT)

local env = (getgenv and getgenv()) or _G
if env.__GARNT_HB_RUNNING then return end
env.__GARNT_HB_RUNNING = true

if not game:IsLoaded() then game.Loaded:Wait() end

local Players = game:GetService("Players")
local RunService = game:GetService("RunService")
local GuiService = game:GetService("GuiService")
local HttpService = game:GetService("HttpService")

local lp = Players.LocalPlayer
while not lp do
	task.wait(0.5)
	lp = Players.LocalPlayer
end

local lastBeat = os.clock()
RunService.Heartbeat:Connect(function()
	lastBeat = os.clock()
end)

local requestFn = (syn and syn.request) or http_request or request or (http and http.request)

local function enc(v)
	return HttpService:UrlEncode(tostring(v or ""))
end

local function httpGet(url)
	if requestFn then
		local ok = pcall(function()
			requestFn({ Url = url, Method = "GET" })
		end)
		if ok then return true end
	end
	local ok2 = pcall(function()
		game:HttpGet(url)
	end)
	return ok2
end

local function errorShowing()
	local ok, res = pcall(function()
		local msg = GuiService:GetErrorMessage()
		if msg and msg ~= "" then return true end
		local gui = game:GetService("CoreGui"):FindFirstChild("RobloxPromptGui")
		if gui then
			local ov = gui:FindFirstChild("promptOverlay")
			if ov and ov:FindFirstChild("ErrorPrompt") then return true end
		end
		return false
	end)
	return ok and res == true
end

task.spawn(function()
	while true do
		if (os.clock() - lastBeat) <= 5 and not errorShowing() then
			local url = BASE .. "/hb?id=" .. enc(ID)
				.. "&user=" .. enc(lp.Name)
				.. "&uid=" .. enc(lp.UserId)
				.. "&place=" .. enc(game.PlaceId)
				.. "&job=" .. enc(game.JobId)
			httpGet(url)
		end
		task.wait(INTERVAL)
	end
end)
