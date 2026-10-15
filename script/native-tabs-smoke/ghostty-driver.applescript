-- Use Ghostty's documented API only. Never send terminal input or OS keystrokes.
-- Every mutation after creation checks the terminal UUID returned at creation.
on locateTab(tabID, terminalID)
    tell application id "com.mitchellh.ghostty"
        repeat with w in windows
            repeat with t in tabs of w
                if (id of t) is tabID then
                    if (id of focused terminal of t) is not terminalID then error "Terminal identity changed; refusing to act"
                    return {contents of w, contents of t}
                end if
            end repeat
        end repeat
    end tell
    return missing value
end locateTab

on run argv
    set operation to item 1 of argv
    tell application id "com.mitchellh.ghostty"
        if operation is "probe" then
            set cfg to new surface configuration
            return version
        else if operation is "baseline" then
            set resultLines to {}
            repeat with w in windows
                repeat with t in tabs of w
                    set end of resultLines to (id of t) & ":" & (id of focused terminal of t)
                end repeat
            end repeat
            set AppleScript's text item delimiters to linefeed
            return resultLines as text
        else if operation is "create" or operation is "add" then
            set labelText to item 2 of argv
            set directoryPath to item 3 of argv
            set cfg to new surface configuration
            set initial working directory of cfg to directoryPath
            set command of cfg to "/bin/sh " & quoted form of (directoryPath & "/idle.sh")
            set environment variables of cfg to {"AEROSPACE_TAB_TEST_LABEL=" & labelText}
            set wait after command of cfg to false
            if operation is "create" then
                set w to new window with configuration cfg
                set t to selected tab of w
            else
                set targetPair to my locateTab(item 4 of argv, item 5 of argv)
                if targetPair is missing value then error "Test anchor tab no longer exists"
                set t to new tab in (item 1 of targetPair) with configuration cfg
            end if
            perform action ("set_tab_title:" & labelText) on focused terminal of t
            return (id of t) & (ASCII character 9) & (id of focused terminal of t)
        else
            set targetPair to my locateTab(item 2 of argv, item 3 of argv)
            if targetPair is missing value then
                if operation is "close" then return "already closed"
                error "Test tab no longer exists"
            end if
            set w to item 1 of targetPair
            set t to item 2 of targetPair
            if operation is "select" then
                select tab t
                activate window w
                return id of selected tab of w
            else if operation is "close" then
                close tab t
                return "closed"
            else if operation is "status" then
                return (id of selected tab of w) & (ASCII character 9) & (count of tabs of w)
            else
                error "Unknown operation"
            end if
        end if
    end tell
end run
