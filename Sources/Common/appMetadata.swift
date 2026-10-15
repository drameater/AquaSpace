public let originalAeroSpaceAppId: String = "bobko.aerospace"
public let stableAeroSpaceAppId: String = "local.aquaspace"
#if DEBUG
    public let aeroSpaceAppId: String = "local.aquaspace.debug"
    public let aeroSpaceAppName: String = "AquaSpace-Debug"
    public let commandSocketAppId: String = aeroSpaceAppId
#else
    public let aeroSpaceAppId: String = stableAeroSpaceAppId
    public let aeroSpaceAppName: String = "AquaSpace"
    // Never unlink, replace, or reclaim stock AeroSpace's live socket.
    public let commandSocketAppId: String = aeroSpaceAppId
#endif
