-- Qualified configuration family: Hyprland 0.55+ Lua, Noctalia 5.x.
hl.monitor({output = "", mode = "preferred", position = "auto", scale = "auto"})
hl.env("XCURSOR_SIZE", "24")
hl.env("HYPRCURSOR_SIZE", "24")
hl.env("QT_QPA_PLATFORM", "wayland;xcb")
hl.env("QT_QPA_PLATFORMTHEME", "qt6ct")
hl.config({
    general = {gaps_in = 5, gaps_out = 10, border_size = 2, layout = "dwindle"},
    decoration = {rounding = 10},
    input = {kb_layout = "@KEYBOARD@", follow_mouse = 1, touchpad = {natural_scroll = true}},
    misc = {disable_hyprland_logo = true},
    xwayland = {force_zero_scaling = true},
})
hl.on("hyprland.start", function()
    hl.exec_cmd("dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE")
    hl.exec_cmd("noctalia")
    hl.exec_cmd("hypridle")
    hl.exec_cmd("wl-paste --type text --watch cliphist store")
    hl.exec_cmd("wl-paste --type image --watch cliphist store")
end)
hl.bind("SUPER + Return", hl.dsp.exec_cmd("kitty"))
hl.bind("SUPER + E", hl.dsp.exec_cmd("thunar"))
hl.bind("SUPER + B", hl.dsp.exec_cmd("firefox"))
hl.bind("SUPER + D", hl.dsp.exec_cmd("wofi --show drun"))
hl.bind("SUPER + V", hl.dsp.exec_cmd("cliphist list | wofi --dmenu | cliphist decode | wl-copy"))
hl.bind("SUPER + L", hl.dsp.exec_cmd("hyprlock"))
hl.bind("SUPER + Q", hl.dsp.window.close())
hl.bind("SUPER + SHIFT + M", hl.dsp.exit())
hl.bind("SUPER + Space", hl.dsp.window.float({action = "toggle"}))
hl.bind("Print", hl.dsp.exec_cmd("hyprshot -m region"))
for _, direction in ipairs({"left", "right", "up", "down"}) do
    hl.bind("SUPER + " .. direction, hl.dsp.focus({direction = direction}))
end
for i = 1, 10 do
    hl.bind("SUPER + " .. i % 10, hl.dsp.focus({workspace = i}))
    hl.bind("SUPER + SHIFT + " .. i % 10, hl.dsp.window.move({workspace = i}))
end
hl.bind("SUPER + mouse:272", hl.dsp.window.drag(), {mouse = true})
hl.bind("SUPER + mouse:273", hl.dsp.window.resize(), {mouse = true})
hl.bind("XF86AudioRaiseVolume", hl.dsp.exec_cmd("wpctl set-volume -l 1 @DEFAULT_AUDIO_SINK@ 5%+"), {locked = true, repeating = true})
hl.bind("XF86AudioLowerVolume", hl.dsp.exec_cmd("wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-"), {locked = true, repeating = true})
hl.bind("XF86AudioMute", hl.dsp.exec_cmd("wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle"), {locked = true})
hl.bind("XF86MonBrightnessUp", hl.dsp.exec_cmd("brightnessctl set +5%"), {locked = true, repeating = true})
hl.bind("XF86MonBrightnessDown", hl.dsp.exec_cmd("brightnessctl set 5%-"), {locked = true, repeating = true})
