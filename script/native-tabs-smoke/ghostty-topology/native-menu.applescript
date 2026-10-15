-- Menu-only driver for a NEW disposable instance of the installed Ghostty app.
-- Never point this at the user's existing Ghostty process. No terminal keystrokes.
on run argv
    if (count argv) is not 5 then error "Expected operation, test PID, protected PID, unique prefix, selected title"
    set operation to item 1 of argv
    if operation is not "merge" and operation is not "detach" then error "Unsupported menu operation"
    set testPID to (item 2 of argv) as integer
    set protectedPID to (item 3 of argv) as integer
    set testPrefix to item 4 of argv
    set expectedTitle to item 5 of argv
    if testPID is protectedPID then error "Refusing the user's existing Ghostty process"
    if testPrefix does not start with "AeroSpace-NativeTabs-" or (length of testPrefix) < 32 then error "Missing unique test prefix"
    if expectedTitle does not start with testPrefix then error "Selected title is not a test title"
    tell application "System Events"
        -- System Events may return name-based references for two apps with the
        -- same display name. Address the live process by index, then recheck PID.
        set processIndex to 0
        repeat 5 times
            -- Fetch primitive IDs in one event: per-process reads race process exit.
            set liveIDs to unix id of every application process
            set processIndex to 0
            repeat with candidateIndex from 1 to (count liveIDs)
                if (item candidateIndex of liveIDs) is testPID then
                    if processIndex is not 0 then error "Duplicate process identity"
                    set processIndex to candidateIndex
                end if
            end repeat
            if processIndex is 0 then error "Test process identity is unavailable"
            try
                if (unix id of application process processIndex) is testPID then exit repeat
            on error
                -- Retry discovery only. No menu has been touched.
            end try
            set processIndex to 0
        end repeat
        if processIndex is 0 then error "Process inventory did not stabilize"
        tell application process processIndex
            if unix id is not testPID then error "Process index changed"
            if (bundle identifier) is not "com.mitchellh.ghostty" then error "Test process is not Ghostty"
            if (count windows) is 0 then error "No test windows"
            repeat with w in windows
                if (name of w) does not start with testPrefix then error ("An unowned window is present in PID " & (unix id) & ": " & (name of w))
            end repeat
            set frontmost to true
            repeat 20 times
                if frontmost then exit repeat
                delay 0.05
            end repeat
            if unix id is not testPID then error "Process index changed during activation"
            if not frontmost then error "Test Ghostty instance could not become foreground"
            if (name of window 1) is not expectedTitle then error "Selected test window changed"
            if operation is "merge" then
                set actionName to "Merge All Windows"
            else
                set actionName to "Move Tab to New Window"
            end if
            tell menu bar 1
                if not (exists menu bar item "Window") then error "Native Window menu unavailable"
                tell menu bar item "Window"
                    if not (exists menu item actionName of menu 1) then error "Required native tab operation unavailable"
                    if not (enabled of menu item actionName of menu 1) then error "Native tab operation is disabled"
                    if (unix id of application process processIndex of application "System Events") is not testPID then error "Process index changed before menu open"
                    click
                    if (unix id of application process processIndex of application "System Events") is not testPID then error "Process index changed before action"
                    click menu item actionName of menu 1
                end tell
            end tell
        end tell
    end tell
    return "invoked native " & operation & " in isolated PID " & testPID
end run
