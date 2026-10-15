import AppKit
import Common

// Potential alternative implementation
// https://github.com/swiftlang/swift-evolution/blob/main/proposals/0392-custom-actor-executors.md
// (only available since macOS 14)
final class MacApp: AbstractApp {
    /*conforms*/ let pid: Int32
    /*conforms*/ let rawAppBundleId: String?
    let appId: KnownBundleId?
    let nsApp: NSRunningApplication
    private let axApp: ThreadGuardedValue<AXUIElement>
    private let appAxSubscriptions: ThreadGuardedValue<[AxSubscription]> // keep subscriptions in memory
    private let windows: ThreadGuardedValue<[UInt32: AxWindow]> = .init([:])
    private let nativeTabs: ThreadGuardedValue<AxNativeTabState>
    private var windowsCount = 0
    var lastNativeFocusedWindowId: UInt32? = nil
    private var thread: Thread?
    private var setFrameJobs: [UInt32: RunLoopJob] = [:]
    @MainActor private static var focusJob: RunLoopJob? = nil

    /*conforms*/ var name: String? { nsApp.localizedName }
    /*conforms*/ var execPath: String? { nsApp.executableURL?.path }
    /*conforms*/ var bundlePath: String? { nsApp.bundleURL?.path }

    // todo think if it's possible to integrate this global mutable state to https://github.com/nikitabobko/AeroSpace/issues/1215
    //      and make deinitialization automatic in deinit
    @MainActor static var allAppsMap: [pid_t: MacApp] = [:]
    @MainActor private static var wipPids: [pid_t: AwaitableOneTimeBroadcastLatch] = [:]

    private init(
        _ nsApp: NSRunningApplication,
        _ axApp: AXUIElement,
        _ axSubscriptions: [AxSubscription],
        _ thread: Thread,
    ) {
        self.nsApp = nsApp
        self.axApp = .init(axApp)
        self.pid = nsApp.processIdentifier
        self.nativeTabs = .init(AxNativeTabState(pid: nsApp.processIdentifier))
        self.rawAppBundleId = nsApp.bundleIdentifier
        self.appId = nsApp.bundleIdentifier.flatMap { KnownBundleId.init(rawValue: $0) }
        assert(!axSubscriptions.isEmpty)
        self.appAxSubscriptions = .init(axSubscriptions)
        self.thread = thread
    }

    @MainActor
    @discardableResult
    static func getOrRegister(_ nsApp: NSRunningApplication) async throws -> MacApp? {
        // Don't perceive any of the lock screen windows as real windows
        // Otherwise, false positive ax notifications might trigger that lead to gcWindows
        if nsApp.bundleIdentifier == lockScreenAppBundleId { return nil }
        let pid = nsApp.processIdentifier
        // AX requests crash if you send them to yourself
        if pid == myPid { return nil }

        if let existing = allAppsMap[pid] { return existing }
        try checkCancellation()
        if let wip = wipPids[pid] {
            try await wip.await()
            return allAppsMap[pid]
        }
        let wip = AwaitableOneTimeBroadcastLatch()
        wipPids[pid] = wip

        let thread = Thread {
            $axTaskLocalAppThreadToken.withValue(AxAppThreadToken(pid: pid, idForDebug: nsApp.idForDebug)) {
                let axApp = AXUIElementCreateApplication(nsApp.processIdentifier)
                let handlers: HandlerToNotifKeyMapping = unsafe [
                    (refreshObs, [kAXWindowCreatedNotification, kAXFocusedWindowChangedNotification]),
                ]
                let job = RunLoopJob(.cancellable)
                let subscriptions = (try? unsafe AxSubscription.bulkSubscribe(nsApp, axApp, job, handlers)) ?? []
                let isGood = !subscriptions.isEmpty
                let app = isGood ? MacApp(nsApp, axApp, subscriptions, Thread.current) : nil

                let appAxSubscriptionsThreadGuarded = app?.appAxSubscriptions
                let windowsThreadGuarded = app?.windows
                let nativeTabsThreadGuarded = app?.nativeTabs
                let axAppThreadGuarded = app?.axApp

                Task.startUnstructured { @MainActor in
                    allAppsMap[pid] = app
                    wipPids[pid] = nil
                    await wip.signalToAll()
                }
                if isGood {
                    CFRunLoopRun()

                    // Destroy AX objects in reverse order of their creation
                    appAxSubscriptionsThreadGuarded?.destroy()
                    nativeTabsThreadGuarded?.destroy()
                    windowsThreadGuarded?.destroy()
                    axAppThreadGuarded?.destroy()
                }
            }
        }
        thread.name = "AxAppThread \(nsApp.idForDebug)"
        thread.start()

        try await wip.await()
        return allAppsMap[pid]
    }

    func closeAndUnregisterAxWindow(_ windowId: UInt32) {
        if serverArgs.isReadOnly { return }
        setFrameJobs.removeValue(forKey: windowId)?.cancel()
        _ = withWindowAsync(windowId, .cancellable) { [windows] window, job in
            guard let closeButton = window.get(Ax.closeButtonAttr) else { return }
            if AXUIElementPerformAction(closeButton.cast, kAXPressAction as CFString) == .success {
                windows.threadGuarded.removeValue(forKey: windowId)
            }
        }
    }

    func getAxSize(_ windowId: UInt32, _ cm: CancellationMode) async throws -> CGSize? {
        try await withWindow(windowId, cm) { window, job in
            window.get(Ax.sizeAttr)
        }
    }

    // todo merge together with detectNewWindows
    func getFocusedWindow(_ cm: CancellationMode) async throws -> Window? {
        let previousFocusedId = lastNativeFocusedWindowId
        let previousIsManaged = previousFocusedId.flatMap { MacWindow.allWindowsMap[$0] }?.macApp === self
        let focused = try await thread?.runInLoop(cm) { [nsApp, axApp, windows, nativeTabs, previousFocusedId, previousIsManaged] job -> (UInt32, NativeTabUpdate)? in
            guard let focused = axApp.threadGuarded.get(Ax.focusedWindowAttr),
                  let window = try windows.threadGuarded.getOrRegisterAxWindow(windowId: focused.windowId, focused.ax.cast, nsApp, job)
            else { return nil }
            if !previousIsManaged || previousFocusedId != window.windowId {
                _ = try nativeTabs.threadGuarded.reconcile(axApp.threadGuarded.get(Ax.windowsAttr), job, isAppHidden: nsApp.isHidden)
            }
            for oldId in nativeTabs.threadGuarded.inactiveWindowIds {
                windows.threadGuarded.removeValue(forKey: oldId)
            }
            return (nativeTabs.threadGuarded.focusedRepresentative(window.windowId), nativeTabs.threadGuarded.update)
        }
        guard let (windowId, update) = focused else { return nil }
        try await applyNativeTabUpdate(update)
        return try await MacWindow.getOrRegister(windowId: windowId, macApp: self)
    }

    @MainActor
    func applyNativeTabUpdate(_ update: NativeTabUpdate) async throws {
        let replacements = update.replacements
        let exposedWindowIds = update.exposedWindowIds
        func managedOwner(_ id: UInt32) -> MacWindow? {
            var queue = [id]
            var seen: Set<UInt32> = []
            while let next = queue.popLast() {
                guard seen.insert(next).inserted else { continue }
                if let window = MacWindow.allWindowsMap[next], window.macApp === self { return window }
                if let old = replacements[next] { queue.append(old) }
                queue += replacements.filter { $0.value == next }.map(\.key)
                if let destination = update.absorptions[next] { queue.append(destination) }
            }
            return nil
        }
        for (newId, sourceId) in update.inheritances where exposedWindowIds.contains(newId) {
            if let workspace = managedOwner(sourceId)?.nodeWorkspace {
                try await MacWindow.getOrRegister(windowId: newId, macApp: self, nativeTabWorkspace: workspace)
            }
        }
        let plan = nativeTabTransferPlan(replacements: replacements, managedWindowIds: Set(MacWindow.allWindowsMap.keys), exposedWindowIds: exposedWindowIds)
        for (newId, oldId) in plan {
            setFrameJobs.removeValue(forKey: oldId)?.cancel()
            try await MacWindow.getOrRegister(windowId: newId, macApp: self, replacingNativeTabWindowId: oldId)
        }
        for (oldId, destination) in update.absorptions where !exposedWindowIds.contains(oldId) {
            setFrameJobs.removeValue(forKey: oldId)?.cancel()
            MacWindow.absorbNativeTabWindow(oldId, into: destination, macApp: self)
        }
        let completedAbsorptions = update.absorptions.filter { MacWindow.allWindowsMap[$0.key] == nil && MacWindow.allWindowsMap[$0.value] != nil }
        let completedInheritances = update.inheritances.filter { MacWindow.allWindowsMap[$0.key] != nil }
        let managedIds = Set(MacWindow.allWindowsMap.keys).intersection(exposedWindowIds)
        let remaining = replacements.filter {
            !nativeTabReplacementHasManagedSuccessor($0.key, replacements: replacements, managedWindowIds: managedIds)
        }
        // Keep the handoff queued across cancellation and chained rapid tab selections.
        let completed = Set(replacements.keys).subtracting(remaining.keys)
        try await thread?.runInLoop(.nonCancellable) { [nativeTabs] _ in
            nativeTabs.threadGuarded.acknowledge(replacements.filter { completed.contains($0.key) })
            nativeTabs.threadGuarded.acknowledgeTopology(absorptions: completedAbsorptions, inheritances: completedInheritances)
        }
    }

    @MainActor func nativeFocus(_ windowId: UInt32) {
        if !WindowManagerHandoff.canControlWindows { return }
        if serverArgs.isReadOnly { return }
        MacApp.focusJob?.cancel()
        // Performance optimization. If possible avoid doing AX requests
        // (important for apps which are slow at responding even such basic AX requests. E.g. Godot)
        // Beware of the macOS bug: https://github.com/nikitabobko/AeroSpace/issues/101
        if (!NSScreen.screensHaveSeparateSpaces || monitorInfos.count == 1) &&
            (lastNativeFocusedWindowId == windowId || windowsCount == 1)
        {
            nsApp.activate(options: .activateIgnoringOtherApps)
        } else {
            MacApp.focusJob = withWindowAsync(windowId, .cancellable) { [nsApp] window, job in
                // Raise firstly to make sure that by the time we activate the app, the window would be already on top
                window.set(Ax.isMainAttr, true)
                AXUIElementPerformAction(window, kAXRaiseAction as CFString)
                nsApp.activate(options: .activateIgnoringOtherApps)
            }
        }
    }

    @MainActor func setAxFrame(_ windowId: UInt32, _ topLeft: CGPoint?, _ size: CGSize?) {
        if !WindowManagerHandoff.canControlWindows { return }
        setFrameJobs.removeValue(forKey: windowId)?.cancel()
        setFrameJobs[windowId] = withWindowAsync(windowId, .cancellable) { [axApp] window, job in
            try disableAnimations(app: axApp.threadGuarded, job) {
                try setFrame(window, topLeft, size, job)
            }
        }
    }

    @MainActor func cancelPendingFrames() {
        Self.focusJob?.cancel()
        Self.focusJob = nil
        for job in setFrameJobs.values { job.cancel() }
        setFrameJobs.removeAll()
    }

    @MainActor func drainWindowWrites() async {
        _ = try? await thread?.runInLoop(.nonCancellable) { _ in true }
    }

    @MainActor func setAxFrameForTermination(_ windowId: UInt32, _ topLeft: CGPoint?, _ size: CGSize?) {
        if !WindowManagerHandoff.canControlWindows { return }
        setFrameJobs.removeValue(forKey: windowId)?.cancel()
        let semaphore = DispatchSemaphore(value: 0)
        let job = withWindowAsync(windowId, .nonCancellable) { [axApp] window, job in
            try? disableAnimations(app: axApp.threadGuarded, job) {
                try setFrame(window, topLeft, size, job)
            }
            semaphore.signal()
        }
        switch job.isCancelled {
            case true: return
            case false: semaphore.wait()
        }
    }

    func getAxWindowsCount(_ cm: CancellationMode) async throws -> Int? {
        try await thread?.runInLoop(cm) { [axApp] job in
            axApp.threadGuarded.get(Ax.windowsAttr)?.count
        }
    }

    func getAxRect(_ windowId: UInt32, _ cm: CancellationMode) async throws -> Rect? {
        try await withWindow(windowId, cm) { window, job in
            try AppBundle.getAxRect(window: window, job: job)
        }
    }

    func getAxRectForTermination(_ windowId: UInt32) -> Rect? {
        let future = CompletableFuture<Rect?>()
        let job = withWindowAsync(windowId, .nonCancellable) { window, job in
            future.complete(try AppBundle.getAxRect(window: window, job: job))
        }
        return switch job.isCancelled {
            case true: nil
            case false: future.blockingGet()
        }
    }

    func isWindowHeuristic(_ windowId: UInt32, _ windowLevel: MacOsWindowLevel?, _ cm: CancellationMode) async throws -> Bool {
        return try await withWindow(windowId, cm) { [nsApp, axApp, appId] window, job in
            window.isWindowHeuristic(axApp: axApp.threadGuarded, appId, nsApp.activationPolicy, windowLevel)
        } == true
    }

    func getAxUiElementWindowType(_ windowId: UInt32, _ windowLevel: MacOsWindowLevel?, _ cm: CancellationMode) async throws -> AxUiElementWindowType {
        return try await withWindow(windowId, cm) { [nsApp, axApp, appId] window, job in
            window.getWindowType(axApp: axApp.threadGuarded, appId, nsApp.activationPolicy, windowLevel)
        } ?? .window
    }

    func isDialogHeuristic(_ windowId: UInt32, _ windowLevel: MacOsWindowLevel?, _ cm: CancellationMode) async throws -> Bool {
        try await withWindow(windowId, cm) { [appId] window, job in
            window.isDialogHeuristic(appId, windowLevel)
        } == true
    }

    func setNativeFullscreen(_ windowId: UInt32, _ value: Bool) {
        setFrameJobs.removeValue(forKey: windowId)?.cancel()
        setFrameJobs[windowId] = withWindowAsync(windowId, .cancellable) { window, job in
            window.set(Ax.isFullscreenAttr, value)
        }
    }

    func setNativeMinimized(_ windowId: UInt32, _ value: Bool) {
        setFrameJobs.removeValue(forKey: windowId)?.cancel()
        setFrameJobs[windowId] = withWindowAsync(windowId, .cancellable) { window, job in
            window.set(Ax.minimizedAttr, value)
        }
    }

    func dumpWindowAxInfo(windowId: UInt32, _ cm: CancellationMode) async throws -> [String: Json] {
        try await withWindow(windowId, cm) { window, job in
            dumpAxRecursive(window, .window)
        } ?? [:]
    }

    func dumpAppAxInfo(_ cm: CancellationMode) async throws -> [String: Json] {
        try await thread?.runInLoop(cm) { [axApp] job in
            dumpAxRecursive(axApp.threadGuarded, .app)
        } ?? [:]
    }

    func getAxTitle(_ windowId: UInt32, _ cm: CancellationMode) async throws -> String? {
        try await withWindow(windowId, cm) { window, job in
            window.get(Ax.titleAttr)
        }
    }

    func isMacosNativeFullscreen(_ windowId: UInt32, _ cm: CancellationMode) async throws -> Bool? {
        try await withWindow(windowId, cm) { window, job in
            window.get(Ax.isFullscreenAttr)
        }
    }

    func isMacosNativeMinimized(_ windowId: UInt32, _ cm: CancellationMode) async throws -> Bool? {
        try await withWindow(windowId, cm) { window, job in
            window.get(Ax.minimizedAttr)
        }
    }

    @MainActor
    static func refreshAllAndGetAliveWindowIds(frontmostAppBundleId: String?) async throws -> [MacApp: WindowRefresh] {
        let windowServerIds = getWindowServerWindowIdsByPid()
        for (_, app) in MacApp.allAppsMap { // gc dead apps
            try checkCancellation()
            if app.nsApp.isTerminated {
                await app.destroy()
            }
        }
        return try await withThrowingTaskGroup(of: (pid_t, WindowRefresh).self, returning: [MacApp: WindowRefresh].self) { group in
            func refreshTheApp(_ nsApp: NSRunningApplication) {
                group.addTask { @Sendable @MainActor in
                    guard let app = try await MacApp.getOrRegister(nsApp) else { return (nsApp.processIdentifier, WindowRefresh()) }
                    return (nsApp.processIdentifier, try await app.refreshAndGetAliveWindowIds(frontmostAppBundleId: frontmostAppBundleId, windowServerIds: windowServerIds.map { $0[nsApp.processIdentifier] ?? [] }))
                }
            }
            // Register new apps
            for nsApp in NSWorkspace.shared.runningApplications {
                try checkCancellation()
                if nsApp.activationPolicy == .regular {
                    refreshTheApp(nsApp)
                }
            }
            for (_, app) in MacApp.allAppsMap {
                try checkCancellation()
                // "About this Mac" window, TouchID, and a lot of other utility windows
                // We don't monitor them actively as we do for regular apps, but if a window of one of those utility
                // apps got focused it will end up in allAppsMap
                if app.nsApp.activationPolicy != .regular {
                    refreshTheApp(app.nsApp)
                }
            }
            var result: [MacApp: WindowRefresh] = [:]
            for try await (pid, windowIds) in group {
                if let app = MacApp.allAppsMap[pid] {
                    result[app] = windowIds
                }
            }
            return result
        }
    }

    struct WindowRefresh: Sendable {
        var aliveWindowIds: [UInt32] = []
        var nativeTabUpdate = NativeTabUpdate()
    }

    private func refreshAndGetAliveWindowIds(frontmostAppBundleId: String?, windowServerIds: Set<UInt32>?) async throws -> WindowRefresh {
        if nsApp.isTerminated {
            await destroy()
            return WindowRefresh()
        }
        guard let thread else { return WindowRefresh() }
        let (alive, dead, update) = try await thread.runInLoop(.cancellable) { [nsApp, axApp, windows, nativeTabs] (job) -> ([UInt32], [UInt32], NativeTabUpdate) in
            let liveWindows = axApp.threadGuarded.get(Ax.windowsAttr)
            let replacements = frontmostAppBundleId == lockScreenAppBundleId
                ? [:]
                : try nativeTabs.threadGuarded.reconcile(liveWindows, job, isAppHidden: nsApp.isHidden, windowServerIds: windowServerIds)
            for oldId in nativeTabs.threadGuarded.inactiveWindowIds {
                windows.threadGuarded.removeValue(forKey: oldId)
            }
            var alive: [UInt32: AxWindow] = windows.threadGuarded
            var dead = [UInt32: AxWindow]()
            // Second line of defence against lock screen. See the first line of defence: closedWindowsCache
            // Second and third lines of defence are technically needed only to avoid potential flickering
            if frontmostAppBundleId != lockScreenAppBundleId {
                (alive, dead) = try alive.partition {
                    try job.checkCancellation()
                    return $0.value.ax.containingWindowId() != nil
                }
            }

            for (id, window) in liveWindows ?? [] {
                try job.checkCancellation()
                try alive.getOrRegisterAxWindow(windowId: id, window, nsApp, job)
            }

            windows.threadGuarded = alive
            nativeTabs.threadGuarded.observations = nativeTabs.threadGuarded.observations.filter { alive[$0.key] != nil }
            return (Array(alive.keys), Array(dead.keys) + Array(replacements.values) + Array(nativeTabs.threadGuarded.update.absorptions.keys), nativeTabs.threadGuarded.update)
        }
        windowsCount = alive.count
        for windowId in dead {
            setFrameJobs.removeValue(forKey: windowId)?.cancel()
        }
        return WindowRefresh(aliveWindowIds: alive, nativeTabUpdate: update)
    }

    @MainActor
    func invalidateNativeTabObservations() {
        thread?.runInLoopAsync(job: RunLoopJob(.nonCancellable)) { [nativeTabs] _ in
            nativeTabs.threadGuarded.invalidateObservations()
        }
    }

    private func destroy() async {
        _ = await Task.startUnstructured { @MainActor [pid] in _ = MacApp.allAppsMap.removeValue(forKey: pid) }.result
        for (_, job) in setFrameJobs {
            job.cancel()
        }
        setFrameJobs = [:]
        thread?.runInLoopAsync(job: RunLoopJob(.nonCancellable)) { job in CFRunLoopStop(CFRunLoopGetCurrent()) }
        thread = nil // Disallow all future job submissions
    }

    private func withWindow<T>(
        _ windowId: UInt32,
        _ cm: CancellationMode,
        _ body: @Sendable @escaping (AXUIElement, RunLoopJob) throws -> T?,
    ) async throws -> T? {
        try await thread?.runInLoop(cm) { [windows] job in
            guard let window = windows.threadGuarded[windowId] else { return nil }
            return try body(window.ax, job)
        }
    }

    private func withWindowAsync(_ windowId: UInt32, _ cm: CancellationMode, _ body: @Sendable @escaping (AXUIElement, RunLoopJob) throws -> ()) -> RunLoopJob {
        guard WindowManagerHandoff.permitsAXWrites else { return .cancelled }
        return thread?.runInLoopAsync(job: RunLoopJob(cm)) { [windows] job in
            guard WindowManagerHandoff.permitsAXWrites, let window = windows.threadGuarded[windowId] else { return }
            try? body(window.ax, job)
        } ?? .cancelled
    }
}

private final class AxWindow {
    let windowId: UInt32
    let ax: AXUIElement
    // periphery:ignore
    private let axSubscriptions: [AxSubscription] // keep subscriptions in memory

    private init(windowId: UInt32, _ ax: AXUIElement, _ axSubscriptions: [AxSubscription]) {
        self.windowId = windowId
        self.ax = ax
        assert(!axSubscriptions.isEmpty)
        self.axSubscriptions = axSubscriptions
    }

    static func new(windowId: UInt32, _ ax: AXUIElement, _ nsApp: NSRunningApplication, _ job: RunLoopJob) throws -> AxWindow? {
        let handlers: HandlerToNotifKeyMapping = unsafe [
            (refreshObs, [kAXUIElementDestroyedNotification, kAXWindowDeminiaturizedNotification, kAXWindowMiniaturizedNotification]),
            (movedObs, [kAXMovedNotification]),
            (resizedObs, [kAXResizedNotification]),
        ]
        let subscriptions = try unsafe AxSubscription.bulkSubscribe(nsApp, ax, job, handlers)
        return !subscriptions.isEmpty ? AxWindow(windowId: windowId, ax, subscriptions) : nil
    }
}

extension [UInt32: AxWindow] {
    @discardableResult
    fileprivate mutating func getOrRegisterAxWindow(windowId id: UInt32, _ axWindow: AXUIElement, _ nsApp: NSRunningApplication, _ job: RunLoopJob) throws -> AxWindow? {
        if let existing = self[id] { return existing }
        // Delay new window detection if mouse is down
        // It helps with apps that allow dragging their tabs out to create new windows
        // https://github.com/nikitabobko/AeroSpace/issues/1001
        if isLeftMouseButtonDown { return nil }

        if let window = try AxWindow.new(windowId: id, axWindow, nsApp, job) {
            self[id] = window
            return window
        } else {
            return nil
        }
    }
}

private func getAxRect(window: AXUIElement, job: RunLoopJob) throws -> Rect? {
    guard let topLeftCorner = window.get(Ax.topLeftCornerAttr) else { return nil }
    try job.checkCancellation()
    guard let size = window.get(Ax.sizeAttr) else { return nil }
    return Rect(topLeftX: topLeftCorner.x, topLeftY: topLeftCorner.y, width: size.width, height: size.height)
}

private func setFrame(_ window: AXUIElement, _ topLeft: CGPoint?, _ size: CGSize?, _ job: RunLoopJob) throws {
    // Set size and then the position. The order is important https://github.com/nikitabobko/AeroSpace/issues/143
    //                                                        https://github.com/nikitabobko/AeroSpace/issues/335
    if let size { window.set(Ax.sizeAttr, size) }
    try job.checkCancellation()
    if let topLeft { window.set(Ax.topLeftCornerAttr, topLeft) } else { return }
    try job.checkCancellation()
    if let size { window.set(Ax.sizeAttr, size) }
}

// Some undocumented magic
// References: https://github.com/koekeishiya/yabai/commit/3fe4c77b001e1a4f613c26f01ea68c0f09327f3a
//             https://github.com/rxhanson/Rectangle/pull/285
private func disableAnimations<T>(app: AXUIElement, _ job: RunLoopJob, _ body: () throws -> T) throws -> T {
    let wasEnabled = app.get(Ax.enhancedUserInterfaceAttr) == true
    if wasEnabled {
        app.set(Ax.enhancedUserInterfaceAttr, false)
    }
    defer {
        if wasEnabled {
            app.set(Ax.enhancedUserInterfaceAttr, true)
        }
    }
    try job.checkCancellation()
    return try body()
}
