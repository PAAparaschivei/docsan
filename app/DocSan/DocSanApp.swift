import SwiftUI

@main
struct DocSanApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    // The self-test gets its own storage and settings so it never touches the user's.
    @State private var model = SelfTest.requested
        ? AppModel(storage: try? Storage.standard(named: "DocSan-selftest"),
                   defaults: UserDefaults(suiteName: "selftest") ?? .standard)
        : AppModel()

    var body: some Scene {
        WindowGroup("DocSan") {
            RootView(model: model)
                .frame(minWidth: 1120, minHeight: 720)
                .task {
                    if SelfTest.requested {
                        await SelfTest.run(model: model)
                        NSApp.terminate(nil)
                    }
                }
        }
        .defaultSize(width: 1280, height: 820)
        .windowToolbarStyle(.unified(showsTitle: false))
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("New Batch", action: model.reset)
                    .keyboardShortcut("n")
            }
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }
}
