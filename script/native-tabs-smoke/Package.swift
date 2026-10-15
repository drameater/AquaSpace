// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "NativeTabSmoke",
    platforms: [.macOS(.v13)],
    dependencies: [.package(path: "../..")],
    targets: [
        .executableTarget(name: "NativeTabSmoke", dependencies: [.product(name: "AppBundle", package: "aerospace")]),
    ],
)
